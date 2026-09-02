# Capturing the meeting audio without tapping it from inside the page

Working notes from 2026-08-28, written while the recorder was producing
full-length wav files of silence out of a call whose audio was demonstrably
arriving.

The question this answers is narrow: **how do we record what the browser played,
per browser process, without injecting JavaScript to intercept the audio?**

Who is speaking is a separate question with its own document,
`docs/speaker-attribution-options.md`. Nothing here removes the VAD observer.

**Option A has since been implemented.** `src/gravai/recording/session_audio_capture.py`
is the sink and the ffmpeg behind one recording; `rtc_intercept.js`,
`audio_worklet_processor.js`, `ws_audio_server.py` and `session_audio_server.py`
are gone, along with the `WS_HOST` setting and the `--disable-features=LocalNetworkAccessChecks`
flag that only existed so the intercept could reach a local socket. Phase 1 has
since been run against a live call and is recorded below. The rest of this
document is the reasoning as it was written, kept because the alternatives are
still the alternatives.


## Why this is on the table

Every audio bug this repository has fought came from the intercept, not from the
browser and not from the network:

- `enabled` cleared by Meet on receivers it is not routing, so a tapped track
  fed WebAudio nothing;
- a remote track that only feeds WebAudio while something consumes it, needing a
  muted `<audio>` element held alive purely as a decoder pump;
- an AudioWorklet blob URL registered at document start, inside the
  opaque-origin frames Meet creates while loading, killing the renderer;
- a stereo remote track written under a mono header, doubling every file, and
  the resample that was added to hide it;
- the socket-open vs first-frame timestamp skew, which shifted every offset
  slicing computed.

The bug open as this is written is the same shape and is pinned *inside* the
intercept. In a 149-second call the `inbound-rtp` packet counter climbed
72 → 373 → 600 in step with the speech window, the receiver reported unmuted,
the transceiver negotiated `recvonly`, both `<audio>` sinks were playing with no
errors, and the AudioContext was running — and the worklet still wrote
−91.0 dBFS. The loss is somewhere between `MediaStreamAudioSourceNode` and
`level.peak`.

So the motivation is not tidiness. It is that the component is load-bearing,
undebuggable from outside, and fails silently with a full-length file of zeros.


## Verified 2026-08-28: WebRTC audio reaches a PulseAudio null sink

An earlier spike showed a null sink's monitor captures a plain `<audio>` element
at −21.1 dB with nothing injected. That was not enough to decide on: Meet's
audio does not arrive that way. It arrives on a remote `MediaStreamTrack`
decoded by WebRTC, which is a different pipeline inside Chrome up until the
output device.

So the actual path was tested. A page builds a real `RTCPeerConnection` pair —
fake microphone in one end, `ontrack` handing the remote stream to an `<audio>`
element out the other, exactly what Meet does with an incoming participant — and
**nothing taps the track**. Chrome is launched with `PULSE_SINK` pointing at a
null sink and ffmpeg records that sink's monitor:

```
page state: remote-track
sink inputs: 1
  Sink: 3   application.name = "Google Chrome"
mean_volume: -20.6 dB
max_volume: -0.0 dB
```

Full scale, from the operating system's side, with no injection of any kind.
This ran in this unprivileged Proxmox LXC container with **no `/dev/snd` and no
sound hardware**, which is the environment the recorder actually runs in.

Scope, stated honestly: the remote peer was local and the microphone was
Chrome's fake device. That exercises the decode-and-play path but not Meet's
network conditions, and it does not prove a real call behaves identically —
only that the output route exists and carries WebRTC audio. The live-call test
in the next section closes that.


## Verified 2026-08-28: a live Meet call reaches the sink

The local peer-connection test above proved the route exists; it could not prove
Meet uses it. So the finished implementation was run against a real call
(`rxn-pxho-yhk`), which removed the bot after 18 seconds - short, but the
question only needed one second of speech to answer.

```
[audio-capture] on entering the call: playing=['Google Chrome'] peak(last 10s)=-120.0 dBFS
[audio-capture] 6s into the call:     playing=['Google Chrome', 'Google Chrome'] peak=-0.2 dBFS

track_mainAudio.wav        55.0s   mean -25.7 dB   max -0.2 dB
sidecar  22:05:07.113062 -> 22:06:02.140687        = 55.028s, and the wav is 55.028s
```

The measurement that matters is not the peak but where it sits. Ten-second
windows across the capture:

```
 0s-10s  -91.0 dB     30s-40s  max -0.2 dB
10s-20s  -91.0 dB     40s-50s  max -2.6 dB
20s-30s  -91.0 dB     50s-60s  max -8.2 dB
```

Digital silence while the browser sat in the green room, audio from the moment
it was admitted at 36s. The VAD timeline put the one speaker at 40.5s-51.2s into
the capture, slicing padded that to 38.5s-52.0s, and the speech-only track it cut
is 13.5s at mean -19.7 dB. So the audio and the speaking timeline agree about
where in the file the talking is, which is the thing the two clocks have to get
right and the thing that was silently wrong before.

One expected difference from the intercept: the 30s-40s window peaks at -0.2 dB
with nothing attributed to it. That is Meet's join chime, which a monitor capture
hears and a per-receiver tap did not. It lands in the mix and outside every
participant segment.


## The contract a replacement has to satisfy

`src/gravai/slicing/slice.py` reads a session directory and requires:

- **exactly one** `track_mainAudio*.wav`. More than one is rejected outright;
  the per-receiver `track_<participant>*.wav` files the intercept also writes
  are never read by anything.
- `session_sidecar.json`:

```json
{
  "session_id": "...",
  "session_start": "2026-08-28T20:12:26.031000+00:00",
  "tracks": {
    "mainAudio-90205": {
      "started_at": "...", "ended_at": "...",
      "sample_rate": 48000, "channels": 1,
      "wav_file": "track_mainAudio-90205.wav"
    }
  }
}
```

  The key is the wav's basename with `track_` stripped and `.wav` removed.
  Slicing blocks until that entry has an `ended_at`, which is how it knows the
  writer finished.
- `started_at` on the **same wall clock** as the `timestamp` fields in
  `vad_timeline.json`; the difference between them is the offset into the mix.

That is the whole interface. Everything downstream is indifferent to where the
audio came from.


## Option A — A PulseAudio null sink per recording, captured with ffmpeg

```
pactl load-module module-null-sink sink_name=gravai_<session_id>
PULSE_SINK=gravai_<session_id> <launch chrome>
ffmpeg -f pulse -i gravai_<session_id>.monitor -ac 1 -ar 48000 track_mainAudio-<n>.wav
```

**Per-process by construction.** `PULSE_SINK` is read by the PulseAudio client
library when the process connects, so it binds that Chrome process tree to that
sink and nothing else lands there. Measured with two concurrent sinks: the
browser on sink A recorded −21.1 dB while sink B recorded −91.0 dB. That
isolation is what makes simultaneous recordings safe, and it is the property the
shared WebSocket server had to be rewritten per-session to get.

**For.** No new dependency: `pulseaudio` and `ffmpeg` are already installed in
both Dockerfiles. No kernel module, no `/dev/snd`, no added container
privileges. It deletes `rtc_intercept.js`, `audio_worklet_processor.js`,
`ws_audio_server.py` and `session_audio_server.py` from the audio path — the
four files every bug above came from.

It also makes the timestamp honest. Today `started_at` has to be corrected by
`mark_first_audio` because the page opens the socket and then spends seconds
building an AudioContext, loading a worklet and resuming it. A monitor capture
is a continuous stream from the moment ffmpeg attaches; there is no gap to
correct for, and `mark_first_audio` stops existing.

**Against.** Ordering matters: Pulse must be up before Chrome launches, or
Chrome binds a dummy device and records silence — the same failure this is
meant to end, so it needs an explicit check rather than a hope. Each session
must unload its module and reap its ffmpeg on teardown, including when the
recording is killed outright; that is the supervisor role
`session_audio_server.py` plays today, doing considerably less.

It captures *everything the browser plays*, including any notification sound
Meet makes. In practice that is join and leave chimes, which land in the mix.

**Gives up.** Per-receiver tracks. This costs nothing: both providers go through
`slice_and_create_session_teams_audio_track`, which globs `track_mainAudio*.wav`
and never touches the per-receiver files.


## Option B — Route after the fact with `pactl move-sink-input`

Same sink, but instead of setting the environment you find Chrome's sink-input
index once it appears and move it there.

**Against.** Racy — there is a window between the browser producing audio and
being moved, and identifying the right sink input needs its own logic. Setting
`PULSE_SINK` gets it right at launch with no window at all. No reason to prefer
this.


## Option C — ALSA loopback (`snd-aloop`)

**Against.** Needs the module loaded on the **Proxmox host** and `/dev/snd`
mapped into the container. A host-level dependency, outside this repository's
control, for something Pulse does entirely in userspace. Strictly worse than A.


## Option D — PipeWire

Same architecture as A, with nicer per-node routing and per-application capture
without the null-sink indirection.

**Against.** Not installed, and it adds a daemon and a compatibility layer to
buy routing ergonomics this use case does not need. Revisit only if Pulse's
per-sink model becomes the constraint.


## Option E — A Chrome extension using `chrome.tabCapture`

Worth naming because it honestly is not page injection: the code runs in the
extension context, never patches `RTCPeerConnection`, and gets a clean per-tab
`MediaStream` from an API that is *designed* to hand it over.

**Against.** It is still JavaScript we ship and maintain, and it still needs a
transport to get the audio out of the browser — so the WebSocket server and its
per-session supervisor survive, and with them most of the complexity the
migration is meant to delete. `--load-extension` is also a fingerprinting tell
against the same Meet gate the Windows persona exists to pass, and that gate
already refuses this container over less. More moving parts than A for an
identical mixed track.


## Option F — Chrome's audio debug recordings (AEC dump)

Chrome can dump WebRTC's render and capture streams to disk from
`chrome://webrtc-internals`. Genuinely no injection, genuinely per-process.

**Against.** It emits `.aecdump`, which needs WebRTC's `unpack_aecdump` to turn
into wav, and it is an unsupported internal debug surface with no stability
promise. A diagnostic, not a foundation.


## Comparison

| | A: null sink | B: move-sink-input | C: snd-aloop | D: PipeWire | E: extension | F: AEC dump |
|---|---|---|---|---|---|---|
| Injection removed | Yes | Yes | Yes | Yes | Page yes, JS no | Yes |
| Per-process capture | Yes | Yes | Yes | Yes | Per tab | Yes |
| New dependency | None | None | Host kernel module | PipeWire | Extension + transport | Unpack tooling |
| Deletes the audio intercept | Yes | Yes | Yes | Yes | Partly | Yes |
| Runs in this container today | **Verified** | Yes | No | No | Yes | Untested |
| Verified against WebRTC audio | **Yes** | Same path | No | No | No | No |
| Race on startup | Pulse before Chrome | Yes, inherent | Pulse before Chrome | Same | No | No |


## Recommendation

**Option A.**

It is the only one measured in this container against the actual WebRTC output
path, it needs nothing that is not already installed, and it removes the four
files that have produced every audio defect in this project's history.

It is also plausibly the fix rather than a workaround. The open bug lives
strictly between `MediaStreamAudioSourceNode` and `level.peak`; Option A deletes
that entire span instead of debugging it. That is worth saying carefully — it is
a reason to try A early, not a claim that A is known to fix it.

### Migration sketch

All five are done. Phase 1 ran last, because it needed a meeting to join; its
result is in "Verified 2026-08-28: a live Meet call reaches the sink" above.

1. **Phase 1 — spike against a real call.** *(done)* Start Pulse, join a real Meet with
   a per-session null sink, record the monitor, confirm speech above the
   silence gate. This is the only step that can still surprise us, and it
   answers the one thing the local peer-connection test could not.
2. **Phase 2 — write the contract.** *(done)* A session audio recorder that loads the
   module, launches ffmpeg onto the monitor, writes `track_mainAudio-<n>.wav`
   and the sidecar entries, and on stop closes ffmpeg, stamps `ended_at` and
   unloads the module. Slicing and everything after it are untouched.
3. **Phase 3 — launch ordering.** *(done, in the recorder rather than the entrypoint: `ensure_pulse_running` starts the daemon before the sink is created and raises if it will not come up, so a daemon that died between recordings is brought back instead of being discovered at slicing time)* Bring Pulse up in the container entrypoint,
   and fail the recording loudly if the sink is missing at launch instead of
   recording a dummy device.
4. **Phase 4 — pass `PULSE_SINK`** *(done)* through the provider's browser launch, and
   tear the module down alongside the recording, including on a kill.
5. **Phase 5 — delete** *(done)* `rtc_intercept.js`, `audio_worklet_processor.js`,
   `ws_audio_server.py`, `session_audio_server.py`, `prepare_injection` and the
   per-receiver track handling, once a recording has gone end to end.

### What this does not solve

All six options produce **one mixed track**. That is exactly what slicing
consumes, so nothing downstream regresses — but speaker attribution still comes
from `vad_timeline.json`, and that still comes from an injected observer. Per
`docs/speaker-attribution-options.md`, reading it over CDP was tested and does
not work, so the only alternative is diarization. System audio removes the
audio injection completely; it does not remove injection outright.

The change to make regardless, and the cheapest high-value one available: **if
the mixed track has audio above the silence gate and `vad_timeline.json` has
zero events, say so loudly.** That is a broken selector, not a quiet meeting,
and today the two are indistinguishable.
