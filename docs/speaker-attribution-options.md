# Speaker attribution without injecting JavaScript into the page

Working notes from 2026-08-28, written alongside the plan to move audio capture
off the page and onto the system audio device (a PulseAudio null sink captured
with ffmpeg). That move has since been made - see
`docs/audio-capture-options.md` and `recording/session_audio_capture.py`.

It deleted `rtc_intercept.js`, `audio_worklet_processor.js`,
`ws_audio_server.py` and `session_audio_server.py` from the audio path. It did
**not** delete `providers/meet/vad_observer.js`, which is a separate question
and the subject of this document: after the audio is coming from the operating
system, the VAD observer is the last script still injected into Meet's page, and
it is there to answer something the audio alone does not — *who* was speaking,
and *when*.

This is a decision worth taking deliberately rather than inheriting.


## What the observer does, and what it costs

Meet has no attribute that says "this person is talking". What it has is an
animated microphone level indicator per participant tile, driven entirely
through the `class` attribute. The observer reads the **rate of change** of that
attribute rather than its values: measured against a live call, the indicator
mutated 5.5 times a second while someone spoke and 0.04 times a second while
nobody did. The class tokens themselves are obfuscated and rotate between
releases, so "which class means speaking" has no stable answer while "it is
animating right now" does.

The structural dependency is one selector, `div[jsname="QgSmzd"]` inside a tile
carrying `data-participant-id`. That is the line that breaks when Meet ships a
rename, and `scripts/inspect_meet_dom.py` exists to re-derive it by correlating
class mutations against inbound-rtp audio levels in a live call.

The cost is therefore: **one selector and one animation heuristic, both owned by
a vendor who has no contract with us**, re-derivable in minutes but silently
wrong until someone notices a recording came back with no speakers.

The cost it does *not* carry is the one that has actually hurt this project.
The observer only reads the DOM. It does not patch `RTCPeerConnection`, does not
clone tracks, does not hold `<audio>` sinks alive to keep a decoder running, and
does not register an AudioWorklet inside frames that crash when you do. Every
audio bug this repository has fought — the cleared `enabled` flag, the
unconsumed remote track, the blob URL registered in an opaque origin, the
interleaved channels under a mono header, the socket-vs-first-frame timestamp
skew — came from the audio intercept, not from here.


## The contract it has to satisfy

Whatever replaces it has to produce `vad_timeline.json`:

```json
{
  "meta": { "session_id": "...", "meeting_url": "...",
            "page_start_ms": 0, "page_end_ms": 0 },
  "events": [
    { "type": "voice-level",
      "data": { "id": "...", "participantName": "pedrohos",
                "classCount": 1, "timestamp": 1787931990258 } }
  ]
}
```

Consumed by `src/gravai/slicing/slice.py`, which:

- walks each participant's events as an **alternating start/end pair sequence**;
- asserts a participant only ever reports **two distinct `classCount` values**
  (which is why the observer reports `0` or `1` rather than a real count);
- treats `timestamp` as epoch milliseconds on the **same wall clock** as the main
  track's `started_at` in `session_sidecar.json`, and subtracts the two to get an
  offset into the mix;
- widens every segment by `_SEGMENT_LEAD_S = 2.0` and `_SEGMENT_TAIL_S = 0.8`
  seconds and merges the ones that then overlap, because Meet's indicator
  animates *after* the audio it reflects.

So the replacement question is narrow: **produce speaker-labelled start/end
timestamps on the meeting's wall clock.** Everything downstream is indifferent
to where they came from.


## Option 1 — Keep the observer

Do nothing. Accept one injected read-only script.

**For.** It works today and is the only one of the three that has ever been
measured against a live call. It costs nothing to keep. It gives real
participant *names*, which neither of the alternatives does without extra work.
It is decoupled from the audio path entirely, so the system-audio migration can
ship without touching it.

**Against.** Still injection, so the stated goal is only half met. Breaks on a
Meet DOM change, and breaks *quietly* — a recording comes back with zero
speakers and a perfectly good mix. Needs a per-provider implementation: Teams
has its own `vad_observer.js` already, and every future provider needs one more.

**Failure mode.** Silent. Worth adding a warning when the mix has audio and the
timeline has no events, which is exactly the shape of a broken selector and is
currently indistinguishable from a silent meeting.


## Option 2 — Read the tiles over CDP instead of from inside the page

Same signal, different delivery: instead of `add_init_script` running a
`MutationObserver` inside Meet's page, the recorder polls from Python over the
Chrome DevTools Protocol — the accessibility tree (`Accessibility.getFullAXTree`),
or `DOM`/`Runtime` queries against the tile elements.

**For.** No script is injected and no page state is touched, which satisfies the
letter of the goal. The accessibility tree is a more stable surface than
obfuscated class names — Meet labels participant tiles for screen readers, and
those labels are the sort of thing that carries "is speaking" semantics without
depending on animation timing. Everything stays on the Python side, where it can
be logged and tested like the rest of the recorder.

**Against.** Polling replaces an event stream. The observer catches a 5 Hz
animation; a CDP poll at, say, 4 Hz over a full accessibility tree is far more
expensive per sample and coarser in time — and segment boundaries are already
being padded by 2.0s/0.8s to compensate for indicator lag, so adding poll
jitter on top makes that padding worse.

### Verified 2026-08-28: the accessibility tree does not carry speaking state

Tested against a live call (`rxn-pxho-yhk`). The bot joined through the normal
green-room path with **only** `vad_observer.js` injected, as ground truth, and
sampled twice a second for 75 seconds: the VAD verdict, every participant tile's
own attributes, and `Accessibility.getFullAXTree`.

**145 samples. The ground truth toggled 8 times. Of 243 distinct observable
features from the tree and the tile attributes, zero toggled even 4 times.**

A first pass scoring features by raw agreement produced apparent hits at 0.88
(`button|Share screen`, `Send a reaction|pressed=false`, the tile's
`data-participant-id`). All were artefacts: they appear once when the UI finishes
rendering and never change, and correlate with speech only because speech
happened later in the window. Requiring a feature to *toggle* like speech does
eliminated all of them.

What the tree does expose:

- Property names available anywhere in the tree: `controls`, `describedby`,
  `disabled`, `expanded`, `focusable`, `focused`, `hasPopup`, `invalid`,
  `labelledby`, `level`, `pressed`, `url`, `valuemax`, `valuemin`, `valuetext`.
  Nothing about speech, and no `live`/`busy` region either.
- The only speech- or mic-related names concern the **bot's own** state:
  `"Your camera is off. Your microphone is off."`, `"Audio settings"`,
  `"Microphone problem. Show more info"`.
- The other participant appears only as a bare name: `StaticText | pedrohos`.
  No state, no suffix, nothing that changes while they talk.

Scope of the test, stated honestly: it covered the full AX tree's named nodes and
its `image`/`button` nodes, plus every tile's own attributes. It did not
enumerate DOM attributes of tile *descendants*, which is where the level
indicator lives. That gap does not rescue the option — the indicator's class
churn is precisely the fragile signal Option 2 existed to avoid, and reading it
over CDP would mean polling the same obfuscated selector with worse timing and
none of the stability benefit.

**Verdict. Option 2 is dead as a speaking-state source.** It remains a valid way
to read the *roster* — names are in the tree, and cleanly — so it survives only
as the "name source" half of Option 3.


## Option 3 — Diarize the mixed audio

Drop the browser from speaker attribution entirely. Run diarization
(`pyannote.audio`, or `whisperX`, which wraps it and aligns to transcript words)
over the mixed track that system-audio capture already produces.

**For.** No browser dependency of any kind, so it survives every Meet redesign
and works identically for Teams and anything added later — one implementation
instead of one per provider. It is the only option that degrades gracefully:
diarization is wrong at the margins rather than absent. It also fixes a problem
the DOM approach cannot: overlapping speech, where two tiles animate at once and
the current timeline produces two segments over the same audio.

**Against.** The heaviest option by far. It needs a model, and on this
container that means CPU inference on top of whisper, which is already the
slowest stage — or a GPU this Proxmox LXC does not have. It yields anonymous
labels (`SPEAKER_00`), not names, so putting a name to a voice still needs the
roster from somewhere: the DOM, the accessibility tree, or a human. Accuracy on
short utterances and on a low-bitrate conference mix is materially worse than
reading a UI element that is *telling you the answer*.

**Verdict.** The right end state if this project ever needs to work against a
platform whose UI cannot be read, or to support many providers. Too heavy to
adopt now purely to remove one read-only script.


## Comparison

| | Option 1: keep observer | Option 2: CDP tiles | Option 3: diarization |
|---|---|---|---|
| Provides speaking state | Yes | **No** | Yes |
| Injection removed | No | Yes | Yes |
| Provider-specific | Yes, one per provider | Yes, one per provider | No |
| Gives speaker names | Yes | Yes | No |
| Handles overlapping speech | No | No | Yes |
| Survives a Meet redesign | No | Partly | Yes |
| Cost to build | None | Half a day + spike | Days, plus inference budget |
| Fails | Silently | Silently | Noisily and partially |
| Verified against a live call | **Yes** | **Yes — no speaking state** | No |


## Recommendation

**Keep the observer (Option 1) for the system-audio migration.**

With Option 2 measured and dead, the choice is now between keeping one
read-only injected script and taking on diarization. That is not a close call
for the migration itself: its value is deleting the audio intercept, which is
where every bug in this session came from, and it is worth shipping without
also rewriting speaker attribution in the same change. The observer is the
least dangerous injected code in the repository and is orthogonal to the audio
path.

So the honest position after the spike is: **injection cannot be fully removed
without paying for diarization.** There is no cheap third way — the browser
only tells you who is speaking through the animated indicator, and that is
readable only from inside the page at any useful resolution.

Two things to do regardless:

1. **Make the silent failure loud.** If the mixed track has audio above the
   silence gate and `vad_timeline.json` has zero events, that is a broken
   selector, not a quiet meeting. Say so in the log and on the recording. This
   is the single highest-value change in this document and costs an afternoon.
2. **Revisit Option 3 when the driver appears** — a third provider, a platform
   with an unreadable UI, or a real need to attribute overlapping speech. Until
   one of those lands, diarization is a large bill for removing a script that
   works.
