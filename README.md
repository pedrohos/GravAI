## GravAI

GravAI is a privacy-first system designed to join online meetings, capture per-participant audio, and prepare the data for transcription, diarization, and downstream LLM workflows (meeting reports, summaries, action items, and other custom use cases), all done locally. The focus is multi-platform meeting recording, supporting major providers such as Microsoft Teams and Google Meet, with an API-first flow and a pipeline that is easy to connect to custom workflows.

### Intended Features

- Record meetings across multiple platforms.
- Capture per-track audio and session metadata.
- Transcribe and diarize audio.
- Enable easy coupling with LLM workflows such as meeting report generation and summarization.

### What is working today

- FastAPI endpoints to trigger recording and transcribe.
- Teams meeting recording (WIP) with per-participant audio output.
- WebSocket audio server writing per-track WAV files and session metadata sidecar.
- Transcription via external Whisper service.

### Roadmap

- Support for multiple concurrent recordings (shared WS server and routing improvements).
- Improve the UI element matching for better per-participant audio slicing.
- Support more providers (Google Meet, Zoom, etc).
- Add diarization support as an optional step.
- Integrate LLM workflows (summaries, minutes, action items, knowledge base sync).
- Better observability, retries, and error reporting for long sessions.

The project is early-stage and evolving. Expect breaking changes as we harden reliability and multi-platform support.
