# Recording system rework and the Google Meet access investigation

Working notes from 2026-08-15/16. Two separate pieces of work ended up in one
session: the per-recording audio server rework (finished), and the investigation
into why the Meet provider cannot join a call from this server.

Read Parts 3 and 7 together, in that order: Part 3 finds the root cause and
concludes that client-side spoofing cannot beat it, and Part 7 shows that
conclusion was drawn from a matrix that never varied the operating system. A
Windows persona clears the gate Part 3 identified, and two more behind it. At the
end of Part 7 the join is still refused, one call further in.

Part 8 closes the media hypothesis: `CreateMeetingDevice` refuses this machine with
real devices exactly as it does with fake ones and with none at all.

**Parts 9 and 10 end the investigation.** The last gate falls to something no part
of the fingerprint work touched: how the browser is *driven*. The pointer has to
travel to the button before clicking it (Part 9), and typing must not begin in the
same instant as the click that focused the field (Part 10). With the Windows
persona for the first gate and both of those for the last, **the recorder joins
the meeting and is admitted.**

Machines referred to below:

- **server** — the Proxmox LXC (`homelab-local`, LAN `192.168.0.22`) with the
  devcontainer inside it. No GPU exposed, no display. This is where the recorder
  is meant to run.
- **workstation** — a Windows desktop running Brave, on the same household
  connection and therefore the same public IPv4.

---

## Part 1 — One audio server process per recording

### The problem

A single audio server process served every recording: fixed port `8765`, started
by the FastAPI lifespan, sessions multiplexed by id. Two meetings recorded at
once shared a socket, an event loop and a process lifetime, and stopping the app
cut everyone's audio mid-track.

### What changed

- **`recording/session_audio_server.py`** (new) — `SessionAudioServer`, a context
  manager that spawns one server process per recording, waits for a ready-file
  handshake to learn the OS-assigned port, exposes `ws_url`, and stops it
  gracefully on exit.
- **`recording/ws_audio_server.py`** (rewritten) — single-session worker. Binds an
  ephemeral port, owns one session directory and sidecar, and on `SIGTERM` stops
  accepting while letting in-flight tracks finish streaming and re-encoding
  before exiting.
- **`providers/common/provider_base.py`** — the browser process is wrapped in a
  `SessionAudioServer`. Because the server drains before the `with` block exits,
  the directory returned is complete and slicing no longer races the writer.
- Removed `start_ws_server` / `stop_ws_server`, the FastAPI lifespan, and the
  fixed `WS_PORT` setting. Ports are always OS-assigned; a fixed one caps the
  service at one recording.

### Concurrency bugs fixed along the way

| Bug | Consequence |
|---|---|
| `mp.set_start_method('spawn', force=True)` called per recording | Mutated a process-wide default, racing any recording starting at the same time. Now `mp.get_context("spawn")`. |
| No parent-liveness check in the server | A SIGKILLed recording left an orphan holding a port and an open wav indefinitely. Now it notices being reparented and writes out what it has. |
| Connections not checked against session id | A stray client could file another session's audio under this one. Now rejected. |

### Tests

`tests/audio_server_test.py` — fake browser clients speaking the real protocol:
two recordings at once on separate ports, stopping one while the other keeps
recording, the forced-close path when a track never disconnects, the orphan
guard, and a refused fixed port. `tests/rtc_intercept_test.py` guards the crash
fix in Part 2. All pass in the devcontainer.

---

## Part 2 — Meet provider fixes

Three defects, all found and fixed before the access problem was understood.

### The renderer crash

`Page.goto: Page crashed` on every Meet navigation. Bisected inside the
container, each case run twice:

| Injected at document start | Result |
|---|---|
| nothing | loads |
| VAD observer only | loads |
| RTC intercept only | **crash** |
| worklet string constant only | loads |
| `new Blob([...])`, no URL | loads |
| `URL.createObjectURL` of a **1-byte** blob | **crash** |

`rtc_intercept.js` built its worklet blob URL at the top level. `add_init_script`
runs in every frame, including the opaque-origin iframes Meet creates while
loading, and registering a blob URL in one of those kills the renderer. Teams
never tripped it because it doesn't create such frames. The URL is now built on
first use inside `getWorkletUrl()`, verified end to end: a loopback WebRTC track
produced a 5-second 48 kHz wav through the full pipeline.

### Language

Meet serves its UI in the browser's language. The provider matched English copy
only, so against a Brazilian host's meeting it saw nothing, sat out its 90-second
green-room timeout, and reported "no join button" instead of the real refusal.
Refusal and end-of-call matching now cover both languages, join selectors accept
`Pedir para participar` / `Seu nome` / `Sair da chamada`, and the context is
pinned to `en-US` so the UI is deterministic to begin with.

### Detection latency and the join flow

Refusal detection matched element by element and took ~57 seconds to notice text
that was on screen the whole time; it now reads the whole visible page and
reports in ~2 seconds with a screenshot. The join flow was Teams' — including a
`Tab`×4 + `Enter` fallback that, on Meet's refusal screen, pressed "Return to
home screen". It now walks Meet's green room properly and screenshots into the
session directory rather than the working directory, where concurrent recordings
would overwrite each other.

---

## Part 3 — Why Meet refuses this server

### Symptom

Every join attempt from the container ends on "You can't join this video call",
with the "Your meeting is safe" card and a countdown. A signed-out incognito
window on the workstation, on the same public IP at the same moment, is offered
the name field and "Ask to join".

### Hypotheses eliminated

Each tested against a live meeting, most of them more than once.

| Hypothesis | Test | Verdict |
|---|---|---|
| Meeting policy blocks anonymous guests | Workstation incognito reaches the green room | Wrong |
| Meeting code dead | Confirmed live while probing | Wrong |
| `/dev/shm` exhaustion | 64 MB confirmed, `--disable-dev-shm-usage` added | Real but unrelated; reverted |
| Missing proprietary codecs | `RTCRtpReceiver.getCapabilities` — H264/VP8/VP9/AV1/Opus present, H264+AAC playback `"probably"` | Wrong |
| User agent | Spoofed desktop Chrome UA | Refused |
| Client hints / browser brand | Real Google Chrome 151, `sec-ch-ua: "Google Chrome"` | Refused |
| `navigator.webdriver` | Suppressed to `undefined` | Refused |
| Headless mode | Full Chromium new-headless, and headful under Xvfb | Refused |
| Automation itself | Plain `google-chrome`, no CDP, no Playwright | Refused |
| Media devices absent | Fake mic + camera injected (7 devices enumerated) | Refused |
| Network / IP reputation | Container and workstation share one public IPv4; workstation's Google Search is clean | Wrong |
| Address family | Browsers egress IPv4, same address as `curl` | Wrong |
| Cookie / consent state | Workstation's `NID`+`OTZ` injected | Refused |
| Engine-specific detection | Firefox and WebKit — different TLS fingerprints, no CDP | Both refused |

### Root cause

Full request capture of both flows (202 requests accepted, 138 refused) isolated
a single divergence:

```
POST /$rpc/google.rtc.meetings.v1.MeetingSpaceService/ResolveMeetingSpace
    workstation → 200      container → 403
```

The accepted flow then proceeds to reCAPTCHA Enterprise and `CreateMediaSession`;
the refused one never reaches attestation at all — it is turned away when it asks
whether the meeting exists.

Header-level diff of that one request: post bodies byte-identical (16 bytes, the
meeting code), and the only meaningful difference is

```
x-goog-meeting-botguardid
```

a **BotGuard attestation token**, computed inside the page by Google's obfuscated
VM from what it observes about the environment, then validated server-side.

This explains every failed attempt above: user agent, client hints, `webdriver`,
plugins and WebGL strings are *inputs the token attests to*, not values the
server reads. Rewriting them changes the report, not the verdict.

> **Superseded by Part 7.** The conclusion below - that client-side spoofing
> cannot move the verdict - held for every configuration tried here, and all of
> them presented as Chrome *on Linux*. Presenting as Chrome on Windows turns
> `ResolveMeetingSpace` into a 200. The reasoning about what BotGuard attests to
> was right; the assumption that the report could not be made acceptable was not.

### Spoofing attempts, for the record

Measured against `ResolveMeetingSpace`'s status. Nine configurations:

| Configuration | Headless tell on wire | RPC |
|---|---|---|
| control | present | 403 |
| UA + all client hints via `Emulation.setUserAgentOverride` + JS patches | gone | 403 |
| …with real Chrome channel | gone | 403 |
| …plus spoofed Intel WebGL renderer | gone | 403 |
| …plus headful at 1920×1080 | gone | 403 |
| playwright-stealth applied to the context | gone | 403 |
| playwright-stealth via the documented `use_sync` hook | gone | 403 |
| …hooked + headful + real Chrome | gone | 403 |
| …hooked + own overrides + fake GPU | gone | 403 |

The three headers that announced headless (`user-agent`, `sec-ch-ua`,
`sec-ch-ua-full-version-list`) were genuinely removed, and it changed nothing.

### What is proven to work

Two findings that bound the solution space:

1. **The container is otherwise acceptable.** Replaying a token minted on the
   workstation onto the container's request returned **200 / GREEN ROOM**. Linux,
   HeadlessChrome UA, SwiftShader and all — none of it is disqualifying once the
   token passes. The server does not bind the token to its minter.
2. **Automation is not what is refused.** A fresh incognito context created and
   driven entirely over CDP inside the workstation's browser reached the green
   room. Being controlled by CDP is not the problem; the environment is.

### Fingerprint diff, accepted vs refused

| Signal | workstation | server |
|---|---|---|
| platform | Windows | Linux |
| brands | Google Chrome 151 / Brave | HeadlessChrome 145 |
| `webdriver` | false | true |
| plugins | 7 | 0 |
| WebGL renderer | AMD Radeon RX 9070 XT | SwiftShader (software) |
| screen | 1920×1080 | 1280×720 |
| timezone | America/Sao_Paulo | UTC |
| cores / memory | 8 / 8 | 6 / 8 |

The renderer line is the one that cannot be honestly fixed here: the LXC has no
`/dev/dri`, and passing the host's iGPU through was ruled out.

---

## Part 4 — Tooling built

All under `scripts/`, all reusable:

| Script | Does |
|---|---|
| `capture_meet_traffic.py` | Records every request, wire headers, post bodies and console from a locally launched browser or one attached over CDP. `--diff` compares two captures. `--check` prints the one-line verdict (`ResolveMeetingSpace` status, page verdict, headless tell, renderer) in ~30s — the iteration oracle. `--persona` runs it as another machine and prints the fingerprint the page sees, so a refusal is never confused with a persona that failed to apply; `--join` also asks to join and lists every RPC in order. `--real-media` runs the browser against the PulseAudio devices instead of no devices, and prints what the page can see. `--human` drives the green room the way a person does, and takes `typing` / `pointer` / `move` / `dwell` to isolate one behaviour; `--no-inject` keeps the persona's headers and drops its javascript. `--user-data-dir`, `--stealth`, `--stealth-pkg`, `--stealth-hook`, `--fake-gpu`, `--channel`, `--headful`, `--arg`. |
| `analyze_capture.py` | Failing requests, attestation endpoints, Meet RPCs reached, and headless tells found in headers. |
| `compare_request.py` | Header-by-header diff of one endpoint across two captures. |
| `inspect_browser.py` | Attaches to a browser over CDP and diffs its fingerprint against the one this machine builds. |
| `replay_botguard.py` | Mints a token in an accepted browser and replays it here. Diagnostic that established the root cause. |
| `bot_profile_login.py` | Opens Chrome in the container with a persistent profile and remote debugging, for a one-time interactive sign-in, then reports the meeting verdict. |

The oracle, for any future experiment:

```bash
uv run python scripts/capture_meet_traffic.py --check --url "https://meet.google.com/<code>"
```

`ResolveMeetingSpace: 200` means access; `403` means refused, whatever the page
happens to render.

Added later, in Part 8:

| Script | Does |
|---|---|
| `virtual_media.sh` | Builds a real PulseAudio sound card in the container - a null sink, and its monitor remapped into a capture source - plus a y4m file to stand in for a webcam. `--tone` plays a quiet sine into the sink so the microphone carries signal; `--status`, `--restart`. Fails loudly if the daemon comes up without the devices on it, and logs to `/tmp/gravai-pulse.log`. |
| `check_media_devices.py` | Launches a browser and reports what it can actually capture: device labels, peak RMS over a second of microphone audio both as Chrome processes it and raw, and whether the camera's frames change. `--fake` runs the same probe against Chrome's synthetic devices for comparison. |

---

## Part 5 — Options

> Rewritten after Part 9. The first two rows are the answer; everything below
> them is a bridge that is no longer needed.

| Option | Status | Cost |
|---|---|---|
| **Windows persona headers + real Chrome** (Part 7) | **Required** — clears `ResolveMeetingSpace` | None; it is in the Meet provider |
| **Pointer movement before the click** (Part 9) | **Required, and sufficient for the rest** — clears `CreateMeetingDevice`, and the join proceeds | A few `mouse.move` calls in the join walk; not yet in the provider |
| `browser_persona.js` | **Not needed for either gate** — both pass with `--no-inject`. Harmless, and still the only thing making the in-page fingerprint agree with the headers | Already written |
| **Real media devices** (Part 8) | **Not needed** — real, correctly named devices leave `CreateMeetingDevice` on 403, same as fake devices and as none | Already built; `virtual_media.sh` is worth keeping for the recording itself |
| **Signed-in bot account**, persistent profile on the server | **Not needed** — never tested, never had to be | — |
| **CDP to a browser on an accepted machine** | Unnecessary now; was proven to reach the green room | A browser process on the workstation during recordings |
| **Token bridge** — mint on an accepted machine, use here | Unnecessary now; was half proven | — |
| GPU passthrough to fix the renderer honestly | Ruled out by decision, and shown not to matter at either gate | Host config, container restart |

---

## Part 6 — Open items

- **`meet/vad_observer.js` is still a byte-for-byte copy of the Teams observer.**
  It watches for `data-tid="voice-level-stream-outline"`, which Meet's DOM does
  not have. Joining will work; `window.__vadEvents` stays empty, so slicing gets
  no speaking timeline and produces no participant tracks. Needs rewriting
  against Meet's own speaking indicator, which means a look at the live DOM.
- The Meet slicer is registered as `slice_and_create_session_teams_audio_track`.
- **Transcription has never been run on a Meet session.** Recording, joining,
  capture, attribution and slicing are all proven (Part 13); the whisper step
  after them is not.
- The per-receiver wavs are kept alongside the mix and nothing prunes them: three
  files of mostly silence per call, at ~10MB per 100 seconds each.
- Meet opens three receiver tracks and only one carries anything; the other two
  are recorded as 100 seconds of silence each. Nothing prunes them.
- Whether the persona should keep claiming a graphics card it does not have is
  open: the claim is not what unlocked anything, and it is checkable by
  rendering. `windows-chrome-honest-gpu` is the variant that drops it.
- Nothing from this session is committed yet.
- `tests/realtime_test.py::test_record_meeting_and_transcribe_meet` fails at the
  join for the reason above, whenever `GRAVAI_TEST_MEET_MEETING_URL` is set.

---

## Part 7 — Masking as a Windows desktop

Working notes from 2026-08-16, later the same day. Part 3 concluded that no
client-side change could move the verdict. That was measured across nine
configurations, **every one of which presented as Chrome on Linux** - spoofed UA,
client hints, `webdriver`, plugins, an Intel renderer, playwright-stealth. The
gap in the matrix was the line at the top of Part 3's own fingerprint diff:
`platform: Windows` vs `platform: Linux`.

### What changed

`src/gravai/recording/common/browser_persona.py` and its `.js`, applied together
on three levels, because a persona that only changes one contradicts itself on
the others:

- **headers** — user agent *and* the full client-hint set rewritten together
  over CDP, plus timezone and locale. Playwright's `user_agent` option leaves
  `sec-ch-ua` alone, so a UA-only spoof says Windows and Linux at once. The
  GREASE brand (`Not;A=Brand`) is read back from the live browser rather than
  written by hand, since its spelling rotates by version.
- **scripts** — `navigator.platform`, the five PDF plugins cross-linked with
  their mime types, a 1920×1080 screen with a taskbar-shaped `availHeight`, the
  Windows speech voices, `document.fonts.check` for the Windows font set,
  `window.chrome`, permission/`Notification` consistency, and a GPU. Every
  replacement stringifies as `[native code]`.
- **the process** — real Google Chrome rather than bundled Chromium, no
  `--enable-automation`, a window the size of the persona's screen.

### Result

Measured against the live meeting `mxc-wxkv-txb`, control re-run at the same
minute:

| Configuration | `ResolveMeetingSpace` |
|---|---|
| control (no persona) | 403, refused |
| **windows-chrome persona** | **200, green room** — twice |

The green room renders in the real recorder: name field, "Ask to join", the lot.
Everything Part 3 concluded about *why* headers cannot be copied across was
right - the token attests to the environment. What was wrong was the assumption
that the environment could not be made to report something acceptable.

### The wall moved rather than fell

Clicking "Ask to join" walks four RPCs, and the persona clears three of them:

```
ResolveMeetingSpace  200   (was 403 - this is what the persona fixed)
CreateMediaSession   200   (listed as untested in Part 5; it passes)
CreateMeetingDevice  403   ← the refusal now
```

Exactly one request out of 243 fails. The flow reaches and completes reCAPTCHA
Enterprise, which Part 3 had only ever seen the accepted workstation do. The
failing call is the one that registers the guest as a participant, and it is the
only call carrying `x-goog-meeting-bot-info` alongside the BotGuard id - a
second, fuller attestation payload than the one the earlier gates take.

Confirmed with the workstation: a signed-out incognito window there *does* get
past this same click. So the join is refused on what the environment is, not on
a policy this meeting has about anonymous guests.

### Ablations, all measured against the same live meeting

| Change | `ResolveMeetingSpace` | `CreateMeetingDevice` |
|---|---|---|
| persona on bundled Chromium instead of Chrome | **403** | — |
| persona without the GPU spoof (`windows-chrome-honest-gpu`) | 200 | 403 |
| persona headful under Xvfb at 1920×1080 | 200 | 403 |
| persona with a warmed persistent profile | 200 | 403 |
| persona with fake mic and camera devices | 200 | 403 |

Two of these are worth keeping in mind:

1. **The real Chrome build is required.** The identical persona on Playwright's
   bundled Chromium is refused at `ResolveMeetingSpace`, with a byte-identical
   page-visible fingerprint. Something in the branded build is checked that no
   script reaches, so `channel="chrome"` is load-bearing and both Dockerfiles now
   install it.
2. **The graphics card is not.** SwiftShader passes the gate the spoofed AMD card
   passes. The renderer line in Part 3's diff was not the obstacle it looked like.

### The oracle, updated

`--join` walks the green room and reports every RPC in order, so a refusal after
the green room is pinned on the call that produced it:

```bash
uv run python scripts/capture_meet_traffic.py --check --join \
    --persona windows-chrome --url "https://meet.google.com/<code>"
```

---

## Part 8 — Real media devices

**Status: concluded. The hypothesis is dead.** The devices were finished, a
browser was shown to capture from them, and the experiment was run against the
live meeting `mxc-wxkv-txb`: `CreateMeetingDevice` answers 403 with real devices
exactly as it does with fake ones and with none at all.

### Why it was worth trying

The persona clears three RPCs and stops at the one that registers the guest as a
participant. What the browser reports about its *devices* is a plausible input to
that decision, and until now the recorder ran with
`--use-fake-device-for-media-stream`, whose devices enumerate like this:

```
audioinput: Fake Default Audio Input
audioinput: Fake Audio Input 1
videoinput: fake_device_0
```

Anything that calls `enumerateDevices` reads the word "Fake". That is a poor
thing to be carrying into the one call that is being refused.

### What was built

`scripts/virtual_media.sh` — a real PulseAudio server in the container:

- system-mode daemon on an anonymous socket at `/tmp/pulse-socket`
- `module-null-sink` **SpeakerOut**, a working output that discards what it plays
- `module-remap-source` **MicIn** over `SpeakerOut.monitor`, which turns the
  monitor into a plain microphone rather than a "Monitor of…" device
- descriptions matching the persona: `Speakers (Realtek(R) Audio)` and
  `Microphone (Realtek(R) Audio)`
- `--tone` plays a quiet 440 Hz sine into the sink through ffmpeg, so the
  microphone carries signal instead of digital silence

`scripts/check_media_devices.py` — opens a real capture and measures it, because
enumerating a device and being able to read from it are different claims.

### The devices, as the browser sees them

| Configuration | Devices seen | Capture |
|---|---|---|
| Chrome fake devices | `Fake Audio Input 1`, `fake_device_0` | audio peak RMS 0.30, video live |
| **PulseAudio devices** | `Microphone (Realtek(R) Audio)`, `Speakers (Realtek(R) Audio)`, **no videoinput** | **raw peak RMS 0.0044 with the tone playing** (processed: see below) |

The second row is the point: the capture is genuine, all the way through
ffmpeg → SpeakerOut → monitor → MicIn → Chrome's audio stack. It is not a
synthetic source and it does not announce itself as one.

Read that RMS figure carefully, because the *processed* one is not trustworthy
here. Through Chrome's default constraints the identical live device measured
0.018, then 0.000, then 0.018, then 0.000 across four consecutive runs: a steady
440 Hz sine is exactly what `noiseSuppression` exists to remove, and how far the
processing has converged inside the one-second measurement window decides the
answer. With `echoCancellation`, `noiseSuppression` and `autoGainControl` off it
reads 0.0044 every time.

`check_media_devices.py` therefore reports both, as `peakRms` and `rawPeakRms`.
Only the second one answers "is anything arriving at all" - and had the first
been read alone, this sound card would have looked dead on half its runs. (If the
tone is ever wanted as audible content rather than as proof of life, it should be
broadband and modulated rather than a pure sine, which is what the suppressor is
built to erase.)

### The camera cannot be done honestly here

A v4l2loopback device needs a kernel module, and this container has no
`/lib/modules`, no `/dev/video*`, and no `CAP_SYS_MODULE` - it is Docker inside
an unprivileged LXC. Loading it would be a Proxmox host change plus passing the
device through both layers.

`--use-file-for-fake-video-capture` on its own does **not** register a camera;
Chrome appears to need `--use-fake-device-for-media-stream` for the file to be
played into, and that flag also replaces the audio devices. So the two cannot be
made to coexist: the run below has a real microphone and no camera at all, which
is an ordinary enough desktop but not a complete one.

Note that `getUserMedia({audio: true, video: true})` fails outright with
`NotFoundError` when there is no camera, taking the microphone down with it. The
probe asks only for what enumerates; the recorder does not yet.

### The descriptions, and why the devices were missing entirely

The devices had been enumerating as `Null Output` and `Remapped Monitor of Null
Output`, which is very nearly as loud a tell as `Fake`. Fixing that turned up
three things, the third of which had been hiding the first two:

- pactl 17 has no `update-sink-proplist`, so a description cannot be set after
  the fact.
- `pactl load-module … sink_properties=device.description="…"` fails whenever the
  value contains a space, whatever the escaping: pactl splits its own arguments
  before PulseAudio's property parser sees them. Hence the startup file
  (`/tmp/gravai-virtual-media.pa`) that PulseAudio parses itself.
- **That startup file was failing too, and silently.** PulseAudio's module-argument
  parser only treats a quote as a quote when the value *starts* with one, so
  `sink_properties=device.description="Speakers (Realtek(R) Audio)"` ends the value
  at the first space and the module never loads. The daemon then comes up
  reporting *"Daemon startup successful"* with no sink and no source on it, and
  the only symptom is an empty device list - the failure is written to the log
  target, which the script did not set. Quoting the whole property list and
  single-quoting the description inside it parses:

  ```
  sink_properties="device.description='Speakers (Realtek(R) Audio)'"
  ```

`virtual_media.sh` now logs the daemon to `/tmp/gravai-pulse.log`, and fails
loudly if it is running but `MicIn` is not there, rather than reporting success
over an empty sound card. `--restart` falls back to SIGTERM by process name when
`pactl exit` is refused, which is what a daemon from an older version of the
script (started with `--disallow-exit`) does.

### The experiment, and its answer

`capture_meet_traffic.py --real-media` passes `PULSE_SERVER=unix:/tmp/pulse-socket`
into the browser's environment - worth knowing that the `--check` path never
passed `--use-fake-device-for-media-stream` in the first place, so every
measurement in Part 7 was taken with **no media devices at all**. It also prints
the device list the page can see, for the same reason the persona prints its
fingerprint: a refusal is only evidence about devices if the devices were there
to be refused.

Three configurations, same live meeting, same sitting, persona on in all three:

| Devices the page has | `ResolveMeetingSpace` | `CreateMediaSession` | `CreateMeetingDevice` |
|---|---|---|---|
| none (the Part 7 baseline) | 200 | 200 | **403** |
| **real PulseAudio microphone and speakers** | 200 | 200 | **403** |
| Chrome's fake mic and camera | 200 | 200 | **403** |

The middle row ran with the page reporting exactly this:

```
["audioinput: Default", "audioinput: Microphone (Realtek(R) Audio)",
 "audiooutput: Default", "audiooutput: Speakers (Realtek(R) Audio)"]
```

Nothing about the devices moves that call - not their existence, not their
labels, not whether they are synthetic. **The media hypothesis is closed.**

### What is worth keeping anyway

The sound card is not wasted work even though it did not open the gate. The Meet
recorder passes `--use-fake-device-for-media-stream`
(`providers/meet/provider.py:301`) today, which means the
bot's own microphone carries Chrome's synthetic beep into any call it does join;
a null sink with a real capture source is what it should be using regardless of
what Meet decides. That is a separate change from this investigation, and would
mean `pulseaudio` and `ffmpeg` in both Dockerfiles and the daemon started as the
container comes up.

### Next

> **Superseded by Part 9.** The next lever was going to be the signed-in bot
> account, on the reasoning that everything the *browser* could report had been
> tried. What had not been tried was how the browser was being *driven*, and that
> is what the gate was reading. No account was needed.

---

## Part 9 — The pointer

Every configuration in Parts 3, 7 and 8 varied what the browser reports about
itself: its headers, its platform, its plugins, its graphics card, its devices.
The join was still driven the same way in all of them - `fill()` the name in one
assignment, then click the button with the pointer teleporting to its centre. A
person does neither.

### The result

The persona is unchanged; only the input is:

| How the join was driven | `ResolveMeetingSpace` | `CreateMeetingDevice` |
|---|---|---|
| `fill()` + immediate click (every earlier part) | 200 | 403 |
| **pointer travels to each control** (`--human move`) | 200 | **200** — twice |
| **pointer travels, with pauses** (`--human pointer`) | 200 | **200** |
| **all of it** (`--human full`) | 200 | **200** |
| **all of it, and no injected javascript** | 200 | **200** — twice |

And past `CreateMeetingDevice` the flow simply continues, which no run in this
investigation had ever seen:

```
ResolveMeetingSpace 200   CreateMediaSession 200   CreateMeetingDevice 200
UpdateMeetingDevice 200   SyncMeetingSpaceCollections 200
GetSmartNotesEligibilities 200   GetUser 200   WriteConferenceSessionLog 200
```

That is a guest knocking on a meeting, waiting to be admitted.

### Which half of "human" did it

Both halves were measured on their own, and the answer is narrower than "act like
a person":

| Isolated behaviour | `CreateMeetingDevice` |
|---|---|
| name typed a key at a time, everything else instant (`--human typing`) | 403 |
| unequal pauses throughout, pointer still teleporting (`--human dwell`) | 403 |
| **pointer paths only, at no particular pace** (`--human move`) | **200** |

**It is the pointer movement.** Typing the name a key at a time does nothing on
its own, and waiting between actions does nothing on its own; a handful of
`mousemove` events before the click is what separates 403 from admitted.

> **Read on before acting on that.** "Only the pointer movement" is how this
> looked from these five runs, and Part 10 shows it was half the rule: every
> configuration above that passed had *no keystrokes at all* (`move` and
> `pointer` leave the name to `fill()`), so none of them could measure what
> happens between a click and a first keystroke. The recorder does type, and the
> pointer alone did not save it.

This also explains why nothing in Parts 3, 7 or 8 moved it: they were all
answering "what is this machine?", and the gate was asking "did a person press
this button?". Playwright's `click()` dispatches a genuine trusted click - but it
dispatches it out of nowhere, with no pointer having crossed the page to get
there, which is a thing no mouse can do.

These runs alternated 200 and 403 within minutes of each other against the same
meeting, so the difference is the input and not the meeting drifting into a
friendlier state.

### What is still load-bearing, and what is not

| Piece | Verdict |
|---|---|
| Windows persona **headers** + real Chrome build | **Required.** Without a persona the run is 403 at `ResolveMeetingSpace`, and the green room never renders - there is no button to move a pointer to. |
| Pointer movement before the click | **Required.** The only thing that clears `CreateMeetingDevice`. |
| `browser_persona.js` | **Not required for either gate.** Both gates pass with `--no-inject`, and the page then honestly reports `platform: Linux x86_64`, no Windows voices and a SwiftShader renderer. A blatantly Linux environment behind Windows headers is admitted. |
| Real media devices (Part 8) | Not required. |
| Signed-in account | Never needed. |

The third row is worth sitting with. Part 3 reasoned that BotGuard attests to the
environment, so the environment had to be made to report something acceptable;
Part 7 read that as vindication when the Windows persona worked. But the persona's
*javascript* half turns out to contribute nothing at either gate - the headers and
the branded Chrome binary carry the first gate on their own, and the last gate was
never about the environment at all.

### The oracle, final form

```bash
uv run python scripts/capture_meet_traffic.py --check --join --human \
    --persona windows-chrome --url "https://meet.google.com/<code>"
```

`--human` also takes `typing`, `pointer`, `move` or `dwell` to isolate one
behaviour at a time, and `--no-inject` drops the persona's javascript while
keeping its headers.

---

## Part 10 — The recorder joins

Part 9 was measured with `capture_meet_traffic.py`. Wiring the same behaviour into
`providers/meet/provider.py` refused four times in a row - `ResolveMeetingSpace`
200, `CreateMediaSession` 200, `CreateMeetingDevice` **403** - while the script,
run against the same meeting minutes apart, was admitted. Same persona, same real
Chrome, same pointer movement.

### The bisect

`compare_request.py` on the failing call, provider against script: **every
reproducible header identical** - user agent, all client hints, the four
`x-browser-*` headers, even `content-length` at 132 bytes. The only differences
were values that differ every run by construction: the cookie, `bot-info`,
`botguardid`, `debugid`, `token`. The verdict was formed in the page, not in
anything copyable.

So the flows were bisected against each other instead:

| Question | Test | Answer |
|---|---|---|
| Is it the injected intercept or VAD observer? | recorder with both disabled | Still 403 |
| Is it the permission reset (`grant_permissions([])`)? | recorder without it | Still 403 |
| Is it the launch flags (fake devices, `--enable-logging`)? | script *with* them | 200 - not the flags |
| Is the guest name (`Bot de …`) scored? | script with that exact name | 200 - not the name |
| Does the pointer actually move in the recorder? | `mousemove` counter in the page | **42 events** - it moves, and is still refused |
| Does the recorder simply act too soon? | 20s settle before joining | Still 403 |
| Is it the process the recorder runs in? | provider flow in a foreground process | 403 - not the process |
| Is it the browser setup? | **provider browser + script's green-room walk** | **200 - not the setup** |
| Is it the green-room polling loop? | provider loop + script's walk | 200 - not the loop |

Which left the last few lines of the walk, and one difference in them.

### The cause

The recorder clicked into the name field and began typing **in the same instant**:

```python
move_and_click(page, name_input.first)
type_like_a_person(page, _GUEST_NAME)      # first keystroke ~0ms after the click
```

Adding one beat, and changing nothing else:

```python
move_and_click(page, name_input.first)
pause(page, 0.2, 0.7)
type_like_a_person(page, _GUEST_NAME)
```

turns `CreateMeetingDevice` into 200 and the recorder is **admitted into the
meeting** - confirmed twice, with the intercept, the VAD observer, the permission
reset and the mic/camera toggles all back on.

### The rule, restated

The refined model accounts for all nine configurations measured across Parts 9
and 10, including every one that passed:

1. the click must be preceded by pointer movement, **and**
2. if the name is typed, a human interval must separate the focusing click from
   the first keystroke.

Part 9's `move` and `pointer` runs passed while typing nothing - they left the
name to `fill()`, so rule 2 had nothing to bite on. `typing` failed because it
broke both rules at once. The recorder broke only rule 2, and that was enough.

### What changed

| File | Change |
|---|---|
| `common/human_input.py` (new) | `pause`, `type_like_a_person`, `move_and_click`, shared by the recorder and the capture script so that what the oracle measures is what the recorder does |
| `providers/meet/provider.py` | Green-room clicks go through the pointer, the name is typed rather than filled, with the beat between; `[rpc] <call> <status>` logged under `debug`, so the next refusal names the call that produced it instead of the screen it rendered |
| `capture_meet_traffic.py` | Imports the shared helpers rather than keeping its own copy |

One trap worth keeping: ending the pointer path with Playwright's `locator.click()`
rather than `page.mouse.click()` is refused, even though the path is identical and
the click is trusted either way. The actionability checks it runs on the way are
paid for in the verdict, so `move_and_click` gives them up deliberately.

---

## Part 11 — Audio out of a Meet call

The recorder joined, sat in the meeting, and wrote nothing: `"tracks": {}`, no
wav files, `0 track connection(s) open` at shutdown. From the outside that looks
exactly like a call where nobody spoke.

### What the console said

The recorder discarded the page console, so it was blind to its own intercept.
Forwarding `[rtc-intercept]` lines and page errors into the session log under
`debug` answered it in one run:

```
[rtc-intercept] RTCPeerConnection created
[rtc-intercept] attaching track 451462cd-…      (three of them)
[rtc-intercept] AudioContext sampleRate 48000 track sampleRate 48000
[rtc-intercept] ws error Event
```

The intercept was hooking Meet's audio correctly and then failing to ship it. The
audio server was fine - a plain `websockets` client in the same container
connected to the same url and sent a frame.

### The cause

Asking the browser directly, from a page on `https://meet.google.com`:

```
WebSocket connection to 'ws://127.0.0.1:39705/?session_id=…' failed:
    net::ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS
```

**Chrome 151 blocks a public site from opening a connection to a local address.**
The bundled Chromium 145 the recorder used before Part 7 did not enforce this, so
the persona work - which made `channel="chrome"` load-bearing for the join - is
what introduced it. The two fixes are visible from the same measurement:
`--disable-features=LocalNetworkAccessChecks` on the browser, or serving the audio
server over `wss://` with a real certificate. Binding the server somewhere else
does not help; the check covers private addresses too.

The flag is in the Meet provider's launch args, and the join still passes with it.

### Measured end to end

One meeting, one speaker, the call ended by hand so the recorder shut down its own
way:

| | |
|---|---|
| join | `CreateMeetingDevice` 200, admitted |
| tracks hooked | 3 receivers, 48 kHz mono |
| sockets | `ws open` on all three |
| written | 3 wavs, finalised, `ended_at` in the sidecar |
| **content** | **`peak=0.12`, 16 half-second windows above 1%** on one track |
| end of call | detected, `vad_timeline.json` written |

The other two receivers are silence for the whole call. `inbound-rtp` stats
during the speech showed `packetsReceived` climbing with `audioLevel` between
0.03 and 0.26, which is what confirmed the audio was arriving before the wav was
checked.

The pipeline is proven from a live Meet call to a wav on disk. What is still
missing is everything downstream: `vad_timeline.json` comes out with `"events":
[]` because the Meet VAD observer is still the Teams one, so slicing has no
speaking timeline to cut on and no way to attribute a track to a participant.

---

## Part 12 — Who is speaking

`meet/vad_observer.js` was a byte-for-byte copy of the Teams observer, watching
for `data-tid="voice-level-stream-outline"`. Meet has no such attribute, and no
attribute that says "this person is talking" at all.

### Finding the indicator without guessing

Meet's class names are obfuscated and rotate between releases, so a selector read
off the DOM once would rot. `scripts/inspect_meet_dom.py` derives it from
behaviour instead: it joins a live call, records every class mutation in the page
with a timestamp, and at the same time polls `inbound-rtp.audioLevel` off Meet's
own peer connections. Then it ranks elements by how much more they mutate while
someone is speaking than while nobody is.

The answer was unambiguous - two elements, both `div[jsname="QgSmzd"]`:

```
[577] div  5.50/s while speaking vs 0.04/s quiet   jscontroller=YQvg8b
[596] div  5.25/s while speaking vs 0.00/s quiet   jscontroller=tae9tc
      participant: spaces/laXm04X4ONUB/devices/487
      toggling classes: wEsLMd x20, OgVli x18, Oaajhc x17, gjg47c x14
```

They are the animated microphone level bars, one in the tile and one in the
thumbnail, and the bar heights are driven by rewriting the class attribute.

### What is read, and what is not

The obvious rule - "these classes mean speaking" - is wrong twice over. The
tokens rotate between releases, and a dump taken while the call was silent showed
the same elements holding base classes (`DYfzY cYKTje gjg47c`), so there is no
value that means quiet. **The rate of change is the signal**: 5.5 mutations a
second while talking, 0.04 while not. The observer therefore watches for any
mutation on those elements and treats the participant as speaking until the
animation stops for 600ms.

Two details it needs to survive:

- **A binary `classCount`.** `slicing/slice.py` asserts that a participant reports
  exactly two distinct counts, start and end; Meet's animation would report four
  or five, so the observer emits 1 and 0 rather than a real count.
- **A burst threshold.** A single class rewrite is the tile re-rendering, not
  speech, so two mutations within 500ms are required before a start is emitted.

The participant comes from the enclosing `[data-participant-id]` tile, and the
display name from the fact that Meet renders it twice under that tile - once in a
span and once in a div - which is what separates it from tooltips, button labels
and material-icon ligatures.

### Measured

One call, speaking in bursts, ended by hand:

```
+ 4.0s  pedrohos  START      + 9.4s  pedrohos  stop
+10.0s  pedrohos  START      +11.1s  pedrohos  stop
+11.5s  pedrohos  START      +12.2s  pedrohos  stop      … 6 pairs over 17s
```

The bot's own tile also produced one start/stop pair from a render. Its
microphone is turned off in the green room before it joins, so it cannot have
spoken, and the recorder now drops its own guest name while draining - 14 events
become 12, six balanced pairs, one participant.

### Slicing still cannot run

`slice.py` looks for `track_mainAudio*.wav` - one mixed track to cut per
participant, which is what the Teams intercept produces. Meet gives three
per-receiver tracks named after their WebRTC track ids, of which one carries
audio, and no mixed one. So the timeline is now correct and there is nothing to
apply it to yet. The choice is to have the Meet intercept publish a mixed
`mainAudio` track alongside the receivers, or to teach slicing about
per-receiver recordings - and the second needs a track-to-participant mapping
that WebRTC ids alone do not give.

---

## Part 13 — A track slicing can cut

Part 12 left a correct speaking timeline and nothing to apply it to: `slice.py`
globs `track_mainAudio*.wav`, which Teams names itself, while Meet writes one wav
per WebRTC receiver. Meet routes a handful of receivers and reuses them between
speakers, so no receiver is a participant - the option taken is to publish a
mixed track alongside them and cut that.

### The change

`common/rtc_intercept.js` is shared by both providers, so the mix is behind a
placeholder each substitutes for itself - `{{MAIN_MIX}}`, compared as a string so
a provider that forgets it gets no mix rather than a syntax error at document
start. Meet passes `true`, Teams `false` and its path is unchanged.

With a mix to build, every receiver has to land in one AudioContext, because
nodes only connect to nodes of the same context - so the per-track contexts
become one shared context, and `track.onended` stops closing it (closing it when
one participant's track ended would have ended the recording).

### The trap in the middle of it

The mix node was first built with what looked like the tidy option - explicit
mono, matching the `ch=1` announced to the audio server:

```js
new AudioWorkletNode(ctx, "pcm-collector", {
  channelCount: 1, channelCountMode: "explicit", channelInterpretation: "speakers",
})
```

That posts **exactly half** the frames a per-receiver node posts. A 46-second call
produced a 23-second mix, and nothing complained: the wav is valid, the sidecar is
valid, and slicing then reads speaking offsets of 25-38 seconds into a file that
ends at 23 - so every segment falls past the end and the participant track comes
out silent. The symptom is a silent track, not an error.

Measured wall-clock against sample count:

| Track | wall | audio | ratio |
|---|---|---|---|
| receivers | 46.3s | 45.9s | 0.99 |
| mix, explicit mono | 46.1s | 22.9s | **0.50** |
| mix, default options | 155.3s | 154.4s | **0.99** |

The fix is to build the mix exactly like a per-receiver track - default node
options, sources connected straight into it, which is all a mixer is in WebAudio.

`tests/mix_alignment_test.py` guards it with a WebRTC loopback inside one page, no
meeting required: it records for twelve seconds and asserts every track, mix
included, holds a second of audio per second of recording. It is opt-in behind
`GRAVAI_TEST_BROWSER=1` because it drives a browser.

### End to end, at last

One call, spoken into, ended by hand, then sliced:

```
track_mainAudio-22069.wav   154.4s  peak=0.1911  speech from 135.0s
track_pedrohos.wav          156.4s  peak=0.0881  speech from 135.5s
```

The second file is the product: the mixed call audio, gated to the moments the
VAD timeline says `pedrohos` was speaking, named after them. Recording, joining,
audio capture, attribution and slicing all work against Google Meet.

---

## Part 14 — playwright-stealth, measured against the persona

The persona is a few hundred lines of hand-written masking, and
`playwright-stealth` is a package that claims to do the same job. The question
asked here is whether it can replace any of that code. Every run below is against
the same live meeting `yej-qyiq-bpu`, in one sitting, with `--human full` and the
real Chrome build, so the only variable is the mask.

### Results

| Configuration | `ResolveMeetingSpace` | `CreateMeetingDevice` |
|---|---|---|
| persona `windows-chrome` (control) | 200 | **200**, admitted |
| persona headers, `--no-inject` (control) | 200 | **200**, admitted |
| playwright-stealth as documented (`use_sync`), no persona | **403** | — |
| playwright-stealth given the persona's user agent and `Win32` | **403** | — |
| persona headers + stealth's evasions in place of `browser_persona.js` | **403**, twice | — |
| the same, minus 8 of its evasions | 200, twice | **403**, twice |

The fifth row is the one to read twice. The wire is byte-identical to the second
row, which was admitted minutes earlier: same user agent, same client hints, same
launch flags, same Chrome. Adding the package's javascript to a configuration
that passes turns it into a refusal at the first gate.

Disabling the eight evasions that touch the browser rather than the navigator
(`chrome_app`, `chrome_csi`, `chrome_load_times`, `hairline`,
`iframe_content_window`, `media_codecs`, `error_prototype`, `webgl_vendor`)
recovers the first gate and loses the last one, where the same run without any of
the package passes. Both halves of its evasion block cost something, at different
gates.

### Why it cannot carry the first gate on its own

What the package writes, read against what Part 7 found the gate needs:

| Signal | persona | playwright-stealth 2.0.3 |
|---|---|---|
| user agent | Windows, over CDP | this browser's own, `HeadlessChrome` relabelled to `Chrome` |
| `sec-ch-ua` | full metadata over CDP | rewritten as an extra header |
| `sec-ch-ua-platform`, `-platform-version`, `-full-version-list`, `-arch`, `-bitness`, `-wow64` | all set together | **untouched**, whatever Chrome derives |
| `navigator.platform` | `Win32` | `Win32` **by default** |
| timezone, locale | `America/Sao_Paulo`, `en-US` | not covered |
| screen, voices, fonts | the persona's | not covered |
| real Chrome channel, `--enable-automation` dropped | both | neither |
| how the browser is driven | `human_input.py` | not covered |

Left to itself the package therefore says Windows in the page and Linux on the
wire, which is the exact contradiction `browser_persona.py` exists to avoid:

```
sec-ch-ua-platform: "Linux"        navigator.platform: Win32
```

Handing it the persona's user agent removes that contradiction, since Chrome
derives the hints from the string it is given, and the refusal does not move. Two
details it gets wrong even then: `sec-ch-ua-platform-version` reads `10.0` where
this Chrome on Windows 11 sends `15.0.0`, and the greased brand comes out as
`"Not=A?Brand"` where the browser's own list says `"Not;A=Brand"`. The persona
reads that brand off the live browser for this reason.

### Verdict

Not a substitute, and not a simplification. It cannot produce the client hints
that carry the first gate, it says nothing about how the browser is driven, which
is what carries the last one, and its evasions are themselves refused at both
gates when everything else is held constant.

The code it would have replaced is `browser_persona.js`, which Part 9 already
found to be load-bearing at neither gate. The simplification available here is to
delete that file, not to swap it for a dependency.

### Added to the oracle

| Flag | Does |
|---|---|
| `--stealth-windows` | Configures playwright-stealth with the persona's user agent and `Win32`, its closest reachable approximation |
| `--stealth-off EVASION` | Disables one evasion by its keyword name, repeatable, so a refusal can be pinned on the evasion that produced it |
| `--no-cdp-hints` | With `--persona`, skips `apply_persona`, leaving the user agent to the context option and the hints to whatever Chrome derives |

`--check` now also prints the client hints as they leave the machine. A mask that
rewrites `sec-ch-ua` and leaves `sec-ch-ua-platform` alone reads as consistent
from inside the page, and the wire is the only place it shows.

---

## Part 15 — pw-stealth-enhanced

A second package, `pw-stealth-enhanced` 0.1.0, which presents itself as the
successor to the deprecated `playwright-stealth`. One release, 7.5 KB, uploaded
2026-04-15, no repository url and an author with nothing else on PyPI, so it was
read in full before being run: three `add_init_script` calls and nothing else, no
network, no subprocess, no build hooks.

### Results

Same live meeting `yej-qyiq-bpu`, same sitting, `--human full`, real Chrome.

| Configuration | `ResolveMeetingSpace` | `CreateMeetingDevice` |
|---|---|---|
| persona headers, `--no-inject` (control) | 200 | **200**, admitted |
| pw-stealth-enhanced alone | **403** | — |
| persona headers + pw-stealth-enhanced | 200, twice | **200**, twice, admitted |

Alone it is refused at the first gate, which is what the second row of Part 14
predicts for any package that writes no headers: the user agent still reads
`HeadlessChrome/151.0.0.0` and `sec-ch-ua-platform` still reads `"Linux"`, since
`apply_stealth` accepts a `user_agent` and a `viewport` and then **logs them
rather than applying them**. Nothing in the package touches a client hint, a
browser channel, a launch flag or the pointer.

Added to the persona it is harmless, unlike `playwright-stealth`, and for an
unflattering reason: most of it never executes.

### Two thirds of the payload is dead

`_JS_BASE_STEALTH` defines `navigator.language` with `Object.defineProperty` and
no `configurable`, which makes the property permanent. `_JS_ADVANCED_STEALTH`
then redefines the same property, four lines into a `try` block whose `catch`
swallows everything, so the `TypeError` ends the block where it stands. Measured
in a page, with the three scripts applied separately:

| Scripts applied | `webdriver` | `platform` | canvas patched | webgl patched | permissions patched |
|---|---|---|---|---|---|
| none | `true` | `Linux x86_64` | no | no | no |
| base only | `true` | `Linux x86_64` | no | no | no |
| **base + advanced (the package)** | `undefined` | **`Linux x86_64`** | **no** | **no** | **no** |
| advanced only | `undefined` | `Win32` | yes | yes | yes |

What survives is `navigator.webdriver`, `hardwareConcurrency` and `deviceMemory`,
the three assignments that precede the throw. The canvas noise, the WebGL
spoofing, the audio perturbation, the font handling, the permissions patch and
`platform: Win32` are all advertised and none of them run. The font evasion would
be a no-op regardless: it patches `Navigator.prototype.fonts`, and the API is
`document.fonts`.

### What it does to a persona that works

It still costs something, and the run passed in spite of it:

- `Intl.DateTimeFormat` is forced to `UTC` over the persona's
  `America/Sao_Paulo`, while `Date.prototype.getTimezoneOffset` keeps returning
  `-180`. The page then reports a timezone its own clock contradicts.
- `navigator.language` is forced to `en-GB` over the persona's `en-US`, which the
  wire still announces as `en-US` in `accept-language`.
- `navigator.webdriver` becomes `undefined` where a real Chrome reports `false`.

Both gates passed with all three of those in place, which says the gates do not
read them today rather than that they are safe to carry.

### Verdict

Nothing to remove. It writes no headers, so it cannot carry the first gate; it
says nothing about how the browser is driven, so it cannot carry the last one;
and what it does write is three navigator properties the persona does not need
help with. Measured against `playwright-stealth` it is the better neighbour and
the worse package: it leaves a working configuration working, because most of it
silently fails to run.

`--stealth-enhanced` applies its scripts the way its own `apply_stealth` applies
them. The package is deliberately not a project dependency; the runs above were
taken with it layered on for the run alone:

```bash
uv run --with pw-stealth-enhanced python scripts/capture_meet_traffic.py --check --join \
    --human --persona windows-chrome --no-inject --stealth-enhanced --url "https://meet.google.com/<code>"
```
