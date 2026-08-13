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
recording ──── headless Chromium joins the meeting; injected JS taps WebRTC
   │           audio and streams it to a local WebSocket server, while a DOM
   │           observer records who is speaking and when
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
| `WS_HOST` | `127.0.0.1` | Host for the internal audio WebSocket server |
| `WS_PORT` | `8765` | Port for the internal audio WebSocket server |
| `SAVE_DIR` | `/tmp` | Where session directories are written |
| `WHISPER_HOST` | *required* | Whisper server hostname |
| `WHISPER_PORT` | *required* | Whisper server port |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `DEBUG_GRAVAI` | `False` | Saves prejoin screenshots to help debug joining |

`WHISPER_VERSION` and `WHISPER_LANGUAGE` are read by `docker-compose-whisper.yaml` when building and running the bundled whisper server; the application itself ignores them.

## API

All endpoints are `POST` and take query parameters.

| Endpoint | Does |
|---|---|
| `/record_meeting` | Records, then slices per participant |
| `/record_meeting_and_transcribe` | Records, slices, and transcribes |
| `/transcribe` | Slices and transcribes an existing recording |

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
