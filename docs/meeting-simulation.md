# Local Meet and Teams environments

These environments exercise GravAI's **existing provider recorders**, browser join flows,
JavaScript speaker observers, PulseAudio capture, slicing, job queue, and catalogue.
The simulator sends real WebRTC audio between two separate browsers: Chrome creates
SDP offers/answers, gathers host ICE candidates, establishes DTLS, and exchanges
SRTP audio and RTCP feedback. HTTP serves the meeting pages and fixtures; WebSocket
carries signaling and scenario activity. Each recording gets its own sender page,
peer connection, scenario clock, and trace.

The Meet and Teams environments run independently and have separate scenario
folders. Their URLs select `simulated_meet` and `simulated_teams` in GravAI's provider
registry. These use `MeetMeetingRecorder` and `TeamsMeetingRecorder` respectively;
no prerecorded WAV or injected VAD event replaces the backend recording path.

## Start with Docker

From the repository root:

```sh
docker compose -f docker-compose.yaml -f docker-compose.simulation.yaml up --build
```

The simulator services use the project's backend image, which already installs
Chrome and its libraries. The backend requires its ordinary `.env` settings.
Recording these scenarios needs no Google credentials or Whisper service.
The Compose overlay stores backend jobs and recordings in dedicated
`gravai-simulation-data` and `gravai-simulation-recordings` volumes, separate from
the normal backend data. When running locally, set `DATABASE_PATH` and `SAVE_DIR`
to development paths before starting the backend.

| Environment | Open in your browser | URL submitted to the Docker backend |
| --- | --- | --- |
| Google Meet | `http://localhost:8765/simulated-meeting/meet/demo` | `http://meet-simulator:8765/simulated-meeting/meet/demo` |
| Teams | `http://localhost:8766/simulated-meeting/teams/demo` | `http://teams-simulator:8765/simulated-meeting/teams/demo` |

The URL must be reachable **from the backend**, including its Chrome process.
`localhost` in the backend container refers to that container.
Every press of Join starts a fresh replay; the meeting id labels the URL and does
not create a shared room. Multiple backend recordings are independent.

Submit and check each environment:

```sh
uv run python -m gravai.simulation.check --meeting-url http://meet-simulator:8765/simulated-meeting/meet/demo
uv run python -m gravai.simulation.check --meeting-url http://teams-simulator:8765/simulated-meeting/teams/demo
```

The checker submits a recording job, polls for completion, downloads its WAV,
checks that audio samples exist, and checks for participant segments. It raises
an error on failed jobs, silent audio, missing segments, or a timeout. It is meant
for the basic and media outage scenarios; denial scenarios should be inspected
through the job/log API instead. The checker does not start the services.

To submit manually:

```sh
curl -X POST 'http://localhost:8000/record_meeting?meeting_url=http://meet-simulator:8765/simulated-meeting/meet/demo'
curl -X POST 'http://localhost:8000/record_meeting?meeting_url=http://teams-simulator:8765/simulated-meeting/teams/demo'
```

Poll `/jobs/{id}`, inspect `/jobs/{id}/log`, and retrieve the resulting recording
through `/recordings/{recording_id}`. Stop/cancel jobs through the ordinary backend
job endpoints. Browser end screens and socket closure clean up sender pages.

## Start locally

Use the project's Python environment, with Chrome installed. The backend still
needs PulseAudio, ffmpeg, and its usual runtime settings:

```sh
uv run python -m gravai.simulation.server --provider meet --scenario scenarios/meet/basic.json --port 8765 --artifacts tmp/simulation/meet
uv run python -m gravai.simulation.server --provider teams --scenario scenarios/teams/basic.json --port 8766 --artifacts tmp/simulation/teams
```

Run the commands in separate terminals, start the backend normally, and submit
`http://127.0.0.1:8765/simulated-meeting/meet/demo` or
`http://127.0.0.1:8766/simulated-meeting/teams/demo`.
The server defaults to a loopback binding; Docker supplies `--host 0.0.0.0`.
`--channel chromium` uses Playwright's bundled Chromium for the sender instead of
Chrome. Production Meet recordings continue using their usual Chrome channel.

## Provider behavior

| Behavior | Meet | Teams |
| --- | --- | --- |
| Prejoin | Guest name, microphone/camera toggles, Ask to join | Web join, browser continuation, Continue without audio or video, display name, Join now |
| Admission | Delayed appearance of Leave call, matching the recorder's admission check | Delayed media/tiles; recorder starts its timeline after submitting Join now |
| Denial | Visible request-denied copy recognized by `MeetJoinError` | `calling-retry-screen-title` rejection heading |
| Participant structure | `data-participant-id` tiles with repeated display names | `data-tid` name on the containing tile; stable indicator id |
| Speaking | `jsname="QgSmzd"` class animation every 200 ms; production observer applies burst detection and 600 ms silence hold | `data-tid="voice-level-stream-outline"` changes between two/three classes; production observer reads class counts |
| Shadow DOM | Ordinary document tiles | Open shadow roots by default, exercised through the production shadow observer |
| Host ends/leave | Meet's recognized end-of-call copy | `Did you leave by mistake?` span |
| Removal | `You've been removed from the meeting` | `You've been removed from this meeting` heading |

The production Teams recorder currently catches its own rejection exception
inside a broad exception handler. `scenarios/teams/denied.json` exposes this: the
job may keep waiting instead of promptly failing. Use the backend stop/cancel
endpoint after observing the behavior. The simulator intentionally supplies the
rejection screen without hiding that recorder defect.

A participant who never speaks also exposes provider differences: Meet only
emits voice events after animation, while Teams emits an initial idle event.
The current slicer infers start/end from observed class counts; a Teams
participant with only an idle event may receive a spurious full-length segment.
The `basic` scenarios include a silent participant to make this observable.

## Scenarios and speech fixtures

Each provider has `basic.json`, `denied.json`, `media-outage.json`, and
`roster-changes.json` (late join, departure, return, and media blackout). Choose a
scenario with `--scenario`, or change the corresponding Compose service command
and recreate that simulator. `/healthz` reports the active provider and scenario.

Scenario fields:

- `duration`: seconds of media after admission, up to 7200.
- `admission_delay`: seconds before acceptance/rejection.
- `deny`: reject the join instead of starting media.
- `end_reason`: `host`, `removed`, or `left`.
- `teams_shadow_dom`: render Teams tiles in open shadow roots, default `true`.
- `participants`: unique `id`, `name`, optional `frequency`, optional `audio`, and
  a list of `[start, end]` speaking intervals in seconds.
- Participant `presence`: optional `[join, leave]` intervals. Omit it for full
  meeting attendance; use `[]` for an absent participant. Tiles enter and leave
  the DOM and media is gated by attendance. Tracks are negotiated up front;
  roster changes do not model SFU track negotiation.
- `outages`: `[start, end]` intervals during which all sender tracks are disabled
  and speaking indicators go idle. Signaling and peer connections stay alive.

Intervals must be sorted, nonoverlapping, and within the duration. Unknown fields
fail validation so a misspelled scenario setting cannot silently change a test.
The same scenario schedule repeats
on each join, including overlaps and silence. Absolute timestamps, port numbers,
ICE credentials, SSRCs, encrypted packets, and browser scheduling vary each run.
Allow timing tolerance for codec buffering, recorder polling, and observer delay.
The slicer also widens segments using its existing speech padding.

By default participants send distinct deterministic sine tones. These exercise
capture, activity, overlapping audio, and slicing; they are not speech fixtures
and should not produce meaningful transcripts. To test transcription, supply a
WAV or another browser-decodable audio file, for example:

```json
{
  "duration": 15,
  "participants": [
    {"id": "alice", "name": "Alice", "audio": "fixtures/alice.wav", "turns": [[1, 12]]}
  ]
}
```

`audio` paths resolve relative to the scenario file. The fixture starts at media
time zero and the turn schedule gates it; include the desired leading silence
in the fixture. Playback does not loop. Missing files fail at server startup.
Fixtures are served only through the configured participant's id. Supply a
configured Whisper service and use the checker with `--transcribe` to wait for
both recording and transcription jobs.

## Evidence and automated checks

`tmp/simulation/{provider}/{run_id}.json` contains the scenario, join request,
full SDP signaling messages, admission/activity/end messages, receiver WebRTC
statistics each second, and final sender statistics. These show negotiated
codecs, candidate pairs, transports, packet/byte counts, audio energy, loss, and
jitter when the browser reports them. The backend retains its usual WAV,
`session_sidecar.json`, `vad_timeline.json`, session logs, and sliced tracks.
Compare these artifacts with the scenario's expected turns; the trace is saved
when the connection ends. Concurrent joins use separate run ids.
Each trace also includes the provider, browser version, run id, scenario SHA-256,
expected audible intervals after attendance/outage gating, and sender/receiver
ICE, signaling, and connection state transitions. Expected intervals precede
codec buffering, polling tolerance, and slicer padding. The server aligns its
scenario clock to the sender's scheduled audio start.

Run fast validation checks:

```sh
uv run pytest tests/simulation_test.py
```

Run real browser checks for both environments, requiring installed browsers:

```sh
GRAVAI_SIM_BROWSER_TESTS=1 uv run pytest tests/simulation_test.py
# Or test with bundled Chromium:
GRAVAI_SIM_BROWSER_TESTS=1 GRAVAI_SIM_BROWSER_CHANNEL=chromium uv run pytest tests/simulation_test.py
```

These checks establish actual WebRTC, verify received packets and nonzero audio
energy, and verify that each production speaker observer emits start/end events.
They do not start PulseAudio or the backend: use the backend checker above to
validate the complete recording/capture/slicing path.

## Fidelity limits

This is a reproducible local approximation of the interfaces GravAI consumes,
not an implementation of Google's or Microsoft's complete meeting protocols.
Signaling is simulator JSON over WebSocket, not Meet RPC/protobuf or Teams
signaling. Media travels directly between browser peers, not through a provider
SFU, TURN service, enterprise proxy, or provider-specific multiplexing. There is
no Google/Microsoft authentication, CAPTCHA, attestation, invitation policy,
provider UI versioning, or account-specific localization.

Speaking indicators are driven by the scenario schedule rather than remote
provider audio-level telemetry. Outages model media blackouts by disabling
tracks, not UDP packet loss, packet reordering, jitter injection, ICE failure, or
reconnection. The environments currently simulate audio and provider DOM; they
do not send video, screen sharing, chat, SFU renegotiation, or provider
network captures. WebRTC generates genuine packets with negotiated browser
codecs, but cannot replay encrypted production packets byte for byte.

Keep real-provider checks for authentication, evolving DOM/signaling contracts,
provider routing and network failure behavior. Captures from authorized real
meetings can guide future scenario/DOM revisions; this environment does not
claim to replay them. Exact production parity requires captured contracts and
additional provider-specific implementations.
