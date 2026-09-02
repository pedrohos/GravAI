# GravAI

GravAI joins online meetings, captures per-participant audio, and prepares it for transcription, diarization, and downstream LLM workflows (reports, summaries, action items) — all running locally, with no meeting audio leaving your infrastructure. It is API-first, so the pipeline is easy to hook into your own workflows.

> Attention: This is an early-stage and evolving tool, so expect breaking changes (especially during this phase).

## Capability status per provider

| Provider | Access meeting | Audio recording | Audio track per participant | Whisper Transcription | Diarization |
|---|---|---|---|---|---|
| **Microsoft Teams** | ✓ - Playwright | ✓ - PulseAudio sink + ffmpeg | ✓ - Through visual component heuristics | ✓ | X - Planned |
| **Google Meet** | ✓ - Playwright, guest or signed in | ✓ - PulseAudio sink + ffmpeg | ✓ - Mixed track cut on the speaking timeline | ✓ | X - Planned |

## How it works

```
POST /jobs ──── a job id, immediately. The work runs in a process of its own.
   │
   ▼
recording ──── Chrome joins the meeting on a virtual screen, playing into a
   │           PulseAudio sink of this recording's own that ffmpeg records,
   │           while a DOM observer records who is speaking and when
   ▼
slicing ────── turns the speaking timeline into one track per participant,
   │           aligned to the main track (silence outside their turns)
   ▼
transcribe ─── each participant's turns, and the whole meeting from the mixed track
   │
   ▼
GET /recordings/{id} ──── when it ran, who spoke when, and what each of them said
```

Nothing here fits inside an HTTP request: a recording lasts as long as the meeting, and transcribing what it captured keeps whisper busy for minutes after that. So nothing is attempted inside one. Every piece of work is a **job** - submitted, polled, and endable - and what the jobs produce accumulates in a **catalogue** of recordings that outlives them.

### The audio comes from the operating system, not from the page

Each recording loads a PulseAudio null sink of its own and launches its browser with `PULSE_SINK` pointing at it, so that browser and everything it spawns play into that sink and nothing else lands there. ffmpeg records the sink's monitor for the length of the meeting into `track_mainAudio.wav`.

That is what makes two recordings at once safe - neither can hear the other - and it is also why nothing is injected into the page to get the audio any more. Tapping WebRTC from inside the page was where every audio defect in this project came from, and all of them wrote a full-length wav of zeros, which reaches transcription as a quiet meeting rather than as an error. `docs/audio-capture-options.md` has the options that were weighed and the measurements behind the choice.

One script is still injected, and only one: the DOM observer that reads who is speaking. Neither Meet nor Teams says so anywhere the operating system can see, and `docs/speaker-attribution-options.md` covers what it would cost to remove it.

The ordering this depends on is that PulseAudio is running before Chrome launches. A browser that starts without a daemon to connect to binds a dummy device for the rest of its life, so the recorder starts the daemon itself and fails the recording loudly rather than capturing silence.

### Two transcripts, not one

Each participant is transcribed from their own turns, with the silence between them removed. That is what attributes a sentence to a speaker, and it is also the version that suffers where the speaking detector was imprecise: a sentence whose opening it missed, or a moment two people talk over each other.

The mixed track is transcribed as well, in one pass. That one reads the meeting as a conversation - interruptions and overlaps in place, offsets already on the meeting's timeline - with no speaker attribution at all. It is the better input for a summary; the per-participant transcripts are the better input for anything that needs to know who said it.

Both come out of the same job. `meeting_transcript_text` on a recording is the mix; `participants[].transcript_text` is each speaker.

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

3. Open [http://localhost:3000](http://localhost:3000) for the web interface, or [http://localhost:8000/docs](http://localhost:8000/docs) for the interactive API.

Compose brings up two services. `app` is the API on port 8000. `frontend` is the web interface on port 3000 - a Next.js server that reaches the API over the Docker network, so your browser only ever talks to port 3000 and nothing is cross-origin. Publishing 8000 is a convenience for calling the API by hand; the page does not need it, which is the point of running the front end this way.

Keep the `gravai-data` volume from `docker-compose.yaml`: it holds the SQLite catalogue, and without it the service forgets every meeting it has recorded each time the container is replaced. The audio is a separate decision - `SAVE_DIR` defaults to `/tmp`, which the catalogue outlives, so a meeting reads back with its transcript and nothing to play. Set `SAVE_DIR=/recordings` in `.env` to put it on the `gravai-recordings` volume that is already mounted for it.

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

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and `ffmpeg`, `pulseaudio` and `pactl` on `PATH` - the meeting audio is captured from a PulseAudio sink with ffmpeg. Then run:

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
| `SAVE_DIR` | `/tmp` | Where session directories are written |
| `DATABASE_PATH` | `./data/gravai.db` | SQLite file holding the job queue and the catalogue of recordings |
| `CORS_ALLOW_ORIGINS` | *empty* | Origins a browser may call this API from, comma separated. Not needed for the shipped setup - the front end calls the API from its own server, not from the browser. Only for putting a browser on this API directly |
| `JOB_LOG_TAIL_LINES` | `400` | Lines `GET /jobs/{id}/log` returns by default |
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

A recording is a job running in a process of its own, and nothing it learns mid-join reaches whoever submitted it. `GET /captcha_challenges` is the other way to find that message - and the web interface shows it as a banner on every page, since the challenge expires:

```bash
curl "http://localhost:8000/captcha_challenges"
```

Point any VNC client at the port, type what the image says, press **Next**, then leave the browser alone - the recorder fills in the password and carries on into the meeting by itself. The server is stopped the moment the sign-in is over, so the port is only open while somebody is actually needed. If nobody comes within `VNC_CAPTCHA_TIMEOUT_S`, the join ends with an error the way it did before.

Two things worth knowing:

- **The port is a keyboard attached to a browser holding a Google session.** Keep `VNC_PASSWORD` set, or drop the `5900:5900` mapping from `docker-compose.yaml` and reach it through an SSH tunnel. Left empty, a throwaway password is generated for each challenge and appears only in the log and in the session's `captcha_challenge.json`; VNC truncates passwords to 8 characters.
- `VNC_ENABLED=False` goes back to a headless browser, where a CAPTCHA simply ends the run. The screen cannot be added to a browser that is already running, which is why the choice is made at launch and not at the challenge.

## Jobs

Everything the service does is a job. Submitting one returns immediately with an id; the work carries on in a process of its own, and keeps going whether or not anything is still listening - a reload of the API does not end a meeting.

```bash
# submit
curl -s -X POST http://localhost:8000/jobs -H 'Content-Type: application/json' \
  -d '{"type": "record_and_transcribe", "meeting_url": "https://meet.google.com/abc-defg-hij"}'
# {"id": "6f2e…", "status": "queued", …}

# poll
curl -s http://localhost:8000/jobs/6f2e…

# watch it join the meeting
curl -s http://localhost:8000/jobs/6f2e…/log
```

Three types: `record` (join and record, slicing per participant unless `slice_tracks` is false), `transcribe` (slice and transcribe a directory a previous recording wrote), and `record_and_transcribe`, which is both and is what most callers want.

A job is `queued`, then `running`, and ends `succeeded`, `failed` or `cancelled`. `stopping` is the state between asking a recording to leave a meeting and it having finished doing so.

### Ending a job: stop and cancel are different

| | `POST /jobs/{id}/stop` | `POST /jobs/{id}/cancel` |
|---|---|---|
| What it does | Asks the recording to leave the meeting | Kills the job's process group |
| The meeting | Ends early | Ends early |
| The recording | Finalized, sliced and transcribed as if everyone had hung up | The captured audio is closed off but nothing is sliced or transcribed, and no speaking timeline was written - so what is left is a wav and not a recording |
| Ends as | `succeeded`, with a real result | `cancelled`, with nothing |

Stop is a request, delivered as a file in the session directory, that the recording notices on its next pass around the call loop - so the call returns while the job is still winding up. It applies only to a job that is in a meeting: a transcription has no meeting to leave and no point at which abandoning it leaves something usable, so stop refuses it rather than killing it under a friendlier name.

Each job runs in a process group of its own, which is what makes cancel reach the browser, its virtual screen and its audio capture together, and reach nothing belonging to any other recording.

## API

| Endpoint | Does |
|---|---|
| `POST /jobs` | Submits a job. Body: `type`, plus `meeting_url` or `tracks_output_dir` |
| `GET /jobs` | Lists jobs, newest first. Filter by `status` and `type` |
| `GET /jobs/{id}` | One job: where it is, and its result once it has one |
| `POST /jobs/{id}/stop` | Leaves the meeting and keeps the recording |
| `POST /jobs/{id}/cancel` | Kills the job. Nothing is kept |
| `DELETE /jobs/{id}` | Forgets a finished job |
| `GET /jobs/{id}/log` | The tail of the session log - the only way to watch a recording happen |
| `GET /recordings` | Every meeting, newest first, each with its speakers and transcripts |
| `GET /recordings/{id}` | One meeting: timings, per-speaker segments and text, whole-meeting transcript |
| `GET /recordings/{id}/jobs` | The jobs that produced it |
| `GET /recordings/{id}/audio` | The mixed track |
| `GET /recordings/{id}/participants/{pid}/audio` | One speaker's track; `speech_only=true` for their turns alone |
| `DELETE /recordings/{id}` | Forgets a meeting. The audio on disk is left alone |
| `GET /config` / `PUT /config` | The settings in `.env` a person is expected to change |
| `GET /captcha_challenges` | Any recording waiting on a CAPTCHA, and where to answer it |
| `POST /record_meeting`, `POST /record_meeting_and_transcribe`, `POST /transcribe` | Shorthands that submit the corresponding job and answer with it |

```bash
curl -X POST "http://localhost:8000/record_meeting_and_transcribe?meeting_url=https://teams.live.com/meet/..."
```

These three used to run the pipeline inside the request and answer with the result, which meant an HTTP connection held open for the length of a meeting. They now submit a job and return it with `202`; poll `GET /jobs/{id}` for the result.

`group_slices_by_name` controls whether a participant is identified by display name (default) or by the underlying DOM element - set it to `false` when two people share a display name.

### Common status codes

| Code | Meaning |
|---|---|
| `400` | No provider recognises the meeting URL, or the request cannot be carried out as asked |
| `404` | No such job or recording |
| `409` | The job exists, but its current state makes the request impossible - already finished, or a transcription asked to stop |
| `410` | The recording is catalogued but its audio is no longer on disk |
| `501` | Provider recognised but not implemented yet |
| `502` | The whisper server rejected or failed the request |
| `500` | Failure inside GravAI - check the session log |

## Web interface

`src/frontend` is a single page over these routes: submit and watch jobs, follow a recording's log while it sits in the meeting, read a meeting back speaker by speaker with its audio, and edit the settings in `.env` without opening the file.

It is a Next.js app, built from `Dockerfile.frontend` and served at [`localhost:3000`](http://localhost:3000) by the `frontend` service.

### It calls the API over the Docker network, not from your browser

The page fetches `/api/...` on its own origin, and the Next server forwards that to the API from inside the Docker network - see [`src/frontend/lib/proxy.ts`](src/frontend/lib/proxy.ts). So:

- `GRAVAI_API_URL` is `http://app:8000`, a **service name**, because this server resolves it and not your browser. That is the whole difference from serving the page as static files, where the address had to be one you could paste into your own address bar.
- There is **no CORS to configure**. As far as the browser is concerned the page and the API are one origin, so `CORS_ALLOW_ORIGINS` is empty and the API answers no cross-origin call at all.
- The API's port does not have to be published. `/docs` is proxied through port 3000 as well.

Point `GRAVAI_API_URL` somewhere else to run the page against another API; it is read when the request is served, so one image serves any deployment.

See [`src/frontend/README.md`](src/frontend/README.md).

## Output

Each run writes to `$SAVE_DIR/<date>_<uuid>_tracks/`:

| File | Contents |
|---|---|
| `track_mainAudio.wav` | The full meeting audio as one mixed track, captured from this recording's PulseAudio sink |
| `track_<participant>.wav` | One per participant, silent outside their speaking turns, aligned to the main track |
| `track_<participant>_speech.wav` | The same turns concatenated with the silence removed - what whisper is given |
| `*_transcription_text.txt` | Plain transcript, one per participant plus one for the mixed track |
| `*_transcription_segments.json` | Timestamped segments, both mapped onto the meeting's timeline |
| `session_metadata.json` | Participants and their track paths |
| `session_sidecar.json` | When the capture started and ended, which is what slicing turns speaking events into offsets against |
| `vad_timeline.json` | Raw speaking events captured in the browser |
| `slice_metadata.json` | The speaking timeline and the segments each track was cut from |
| `audio_capture.log` | What ffmpeg said while recording the sink - the account of an empty capture |
| `stop.request` | Present when somebody asked this recording to leave the meeting |
| `session.log` | This session's log, also appended to `./logs/session.log` |

The transcripts and the speaking segments are also copied into the SQLite catalogue at `DATABASE_PATH`, which is what `GET /recordings` reads - so listing meetings and reading one back does not mean opening a file per speaker. The audio itself stays on disk and is only pointed at from there.

## Layout

```
Dockerfile              the API and the recorder: Python, Playwright, Chrome, ffmpeg
Dockerfile.frontend     the web interface as its own container: a Next.js server
src/
   frontend/         the web interface: Next.js, and the /api proxy to the backend
   gravai/
      api/           FastAPI app, routes, response schemas, pipeline composition
      jobs/          the job queue and the catalogue: SQLite store, job processes
      recording/     browser automation, per-recording audio capture, the VAD observer
         providers/  per-platform join logic (teams/, meet/)
      slicing/       speaking timeline -> per-participant tracks
      transcribe/    whisper client and output formatting
      config/        settings, logging, and reading/writing .env
      models/        shared pydantic models
      registry.py    which provider handles which URLs, recorder and slicer
```

Adding a meeting platform means adding one entry to `registry.py` and the provider implementation it points at.

## Roadmap

🔴 - High Priority
- Optional diarization step
- Better DOM matching for more reliable per-participant slicing

🟡 - Medium Priority
- LLM workflows (summaries, minutes, action items, knowledge base sync) as job types
- Search across recorded meetings and their transcripts
- Retries for a job whose meeting was never joined

🟢 - Low Priority
- Further increase supported provider list (Zoom)
- Improved observability and error reporting for long sessions

Done since the last release: Google Meet as a provider, concurrent recordings each in their own process, the job-oriented API and its SQLite catalogue, the whole-meeting transcript, and the web interface.

## Contributions

Open to contributions as long as they do not divert from the project's proposed vision, purpose and when it is well intended.

;)
