# GravAI

GravAI joins online meetings, captures per-participant audio, and prepares it for transcription, diarization, and downstream LLM workflows (reports, summaries, action items) — all running locally, with no meeting audio leaving your infrastructure. It is API-first, so the pipeline is easy to hook into your own workflows.

> Attention: This is an early-stage and evolving tool, so expect breaking changes (especially during this phase).

## Capability status per provider

| Provider | Access meeting | Audio recording | Audio track per participant | Whisper Transcription | Diarization |
|---|---|---|---|---|---|
| **Microsoft Teams** | ✓ - Playwright | ✓ - WebRTC intercept + AudioWorklet | ✓ - Through visual component heuristics | ✓ | X - Planned |
| **Google Meet** | X - Planned | X - Planned | X - Planned | — | X - Planned |

## How it works

```
meeting URL
   │
   ▼
recording ──── Chrome joins the meeting on a virtual screen; injected JS taps WebRTC
   │           audio and streams it to this recording's own audio server
   │           process, while a DOM observer records who is speaking and when
   ▼
slicing ────── turns the speaking timeline into one track per participant,
   │           aligned to the main track (silence outside their turns)
   ▼
transcribe ─── sends each participant track to whisper
```

The pipeline runs end-to-end, or resumes from an existing recording directory through the routes described in the **API** section.

## Setup

### With Docker (recommended)

1. Create your env file and fill it in:

```bash
cp .env.example .env
```

2. Start it. Either point at a whisper server you already run (set `WHISPER_HOST` / `WHISPER_PORT` in `.env`):

```bash
docker compose -f docker-compose.yaml up -d
```

Or bring up GravAI together with a bundled whisper:

```bash
docker compose -f docker-compose-whisper.yaml up -d
```

3. Open [http://localhost:8000/docs](http://localhost:8000/docs) for the interactive API.

### Local development - Devcontainers (recommended)

.devcontainers will handle all dependencies. Just run with:

```bash
uv sync
make run
```

### Local development

So far local development has only been tested with .devcontainers (recommended) or on Ubuntu.

WSL should work but it is not officially supported. 
Supporting Windows is not a priority currently, this might change in the future.

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and `ffmpeg` on `PATH`. Then run:

```bash
uv sync
uv run playwright install-deps
uv run playwright install
make run
```

`make test` runs the suite.

## Configuration

Read from `.env` (see `.env.example`).

| Variable | Default | Purpose |
|---|---|---|
| `WS_HOST` | `127.0.0.1` | Host the internal audio WebSocket servers
| `SAVE_DIR` | `/tmp` | Where session directories are written |
| `WHISPER_HOST` | *required* | Whisper server hostname |
| `WHISPER_PORT` | *required* | Whisper server port |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `DEBUG_GRAVAI` | `False` | Saves prejoin screenshots to help debug joining |
| `GOOGLE_ACCOUNT_EMAIL` | *empty* | Account to sign in as when a Meet refuses guests |
| `GOOGLE_ACCOUNT_PASSWORD` | *empty* | Password for that account |
| `VNC_ENABLED` | `True` | Lets a CAPTCHA on the Google sign-in be answered over VNC |
| `VNC_HOST` | `0.0.0.0` | Interface that VNC server binds to |
| `VNC_PORT` | `5900` | First port it tries; it walks upwards if taken |
| `VNC_PASSWORD` | *empty* | Empty generates a throwaway one per challenge and logs it |
| `VNC_CAPTCHA_TIMEOUT_S` | `600` | How long a challenge waits for somebody before giving up |

`WHISPER_VERSION` and `WHISPER_LANGUAGE` are read by `docker-compose-whisper.yaml` when building and running the bundled whisper server; the application itself ignores them.

### Joining a Meet that refuses guests

By default the recorder joins Google Meet anonymously, types a guest name and waits to be admitted. Some meetings will not take a guest at all - the host's organisation blocks them, or nobody has started the call - and Meet answers `ResolveMeetingSpace` with a 403 and sends the tab to the Google sign-in page.

Setting `GOOGLE_ACCOUNT_EMAIL` and `GOOGLE_ACCOUNT_PASSWORD` lets the recorder sign in when, and only when, that redirect happens; a meeting that admits guests never reaches the sign-in code. The account has to be one that signs in with a password alone: **2-Step Verification stops the flow**, because the container has no way to produce a code. Google may also refuse the sign-in as automated, which ends the run with an error saying so rather than being retried.

Left empty, the redirect ends the run with an error saying the meeting does not admit guests.

### Answering a CAPTCHA over VNC

A CAPTCHA is the one refusal on that page a person can clear: it is asking for eyes, not for a device or a secret. So the recorder's browser runs on a virtual screen, and when - and only when - a CAPTCHA appears during the Google sign-in, that screen goes onto a VNC port and the join waits for somebody to answer it.

What that looks like in the session log:

```
Google is showing a CAPTCHA on the sign-in page. It asks for a person to read an
image, so the recorder's screen is now on VNC and the sign-in is waiting up to
10 min for one:
    vnc://0.0.0.0:5900  (0.0.0.0 is every interface - connect to this
                        machine's own address on port 5900)
    password: H1ALtz6B
    what it looks like: /tmp/<session>_tracks/google_captcha.png
    waiting challenge recorded in: /tmp/<session>_tracks/captcha_challenge.json
    Type the characters and press Next, then leave the browser alone - the
    recorder types the password and joins the meeting itself.
```

Since a recording holds its HTTP request open for the whole meeting, that message cannot come back in the reply. `GET /captcha_challenges` is the other way to find it:

```bash
curl "http://localhost:8000/captcha_challenges"
```

Point any VNC client at the port, type what the image says, press **Next**, then leave the browser alone - the recorder fills in the password and carries on into the meeting by itself. The server is stopped the moment the sign-in is over, so the port is only open while somebody is actually needed. If nobody comes within `VNC_CAPTCHA_TIMEOUT_S`, the join ends with an error the way it did before.

Two things worth knowing:

- **The port is a keyboard attached to a browser holding a Google session.** Keep `VNC_PASSWORD` set, or drop the `5900:5900` mapping from `docker-compose.yaml` and reach it through an SSH tunnel. Left empty, a throwaway password is generated for each challenge and appears only in the log and in the session's `captcha_challenge.json`; VNC truncates passwords to 8 characters.
- `VNC_ENABLED=False` goes back to a headless browser, where a CAPTCHA simply ends the run. The screen cannot be added to a browser that is already running, which is why the choice is made at launch and not at the challenge.

## API

Every endpoint takes query parameters.

| Endpoint | Does |
|---|---|
| `POST /record_meeting` | Records, then slices per participant |
| `POST /record_meeting_and_transcribe` | Records, slices, and transcribes |
| `POST /transcribe` | Slices and transcribes an existing recording |
| `GET /captcha_challenges` | Lists any recording waiting on a CAPTCHA, and where to answer it |

```bash
curl -X POST "http://localhost:8000/record_meeting_and_transcribe?meeting_url=https://teams.live.com/meet/..."
```

The call blocks for the duration of the meeting and returns the paths it produced plus the session structure.

`group_slices_by_name` parameter controls whether a participant is identified by display name (default) or by the underlying DOM element — set it to `false` when two people share a display name.

### Common status codes

| Code | Meaning |
|---|---|
| `400` | No provider recognises the meeting URL |
| `501` | Provider recognised but not implemented yet (i.e. Google Meet) |
| `502` | The whisper server rejected or failed the request |
| `500` | Failure inside GravAI — check the session log |

## Output

Each run writes to `$SAVE_DIR/<date>_<uuid>_tracks/`:

| File | Contents |
|---|---|
| `track_mainAudio-*.wav` | The full meeting audio as one mixed track |
| `track_<participant>.wav` | One per participant, silent outside their speaking turns, aligned to the main track |
| `*_transcription_text.txt` | Plain transcript per participant |
| `*_transcription_segments.json` | Timestamped segments per participant |
| `session_metadata.json` | Participants and their track paths |
| `session_sidecar.json` | Track start/end times, written by the audio server |
| `vad_timeline.json` | Raw speaking events captured in the browser |
| `session.log` | This session's log, also appended to `./logs/session.log` |

## Layout

```
src/gravai/
   api/           FastAPI app, routes, response schemas, pipeline composition
   recording/     browser automation, injected JS, WebSocket audio server
      providers/  per-platform join logic (teams/)
   slicing/       speaking timeline -> per-participant tracks
   transcribe/    whisper client and output formatting
   config/        settings and logging
   models/        shared pydantic models
   registry.py    which provider handles which URLs, recorder and slicer
```

Adding a meeting platform means adding one entry to `registry.py` and the provider implementation it points at.

## Roadmap

🔴 - High Priority
- Multiple concurrent recordings (shared WS server and routing improvements)
- Support Google Meet as a provider
- Improve recording task efficiency by establishing recording per process

🟡 - Medium Priority
- LLM workflows (summaries, minutes, action items, knowledge base sync)
- Better DOM matching for more reliable per-participant slicing
- Optional diarization step

🟢 - Low Priority
- Further increase supported provider list (Zoom)
- Improved observability, retries, and error reporting for long sessions

## Contributions

Open to contributions as long as they do not divert from the project's proposed vision, purpose and when it is well intended.

;)
