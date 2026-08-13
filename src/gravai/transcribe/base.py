import os
from pathlib import Path
import requests

from gravai.config.settings import Settings
from gravai.config.logging_config import get_logger
from gravai.models.models import Session, ParticipantDataTransc, Transcription, TranscriptedSession
from gravai.transcribe.errors import WhisperError
from gravai.transcribe.formatter import format_and_save_single_segment

# (connect, read). Transcribing a long track legitimately takes minutes, so the
# read budget is generous; only the connect needs to fail fast.
_WHISPER_TIMEOUT_S = (10.0, 1800.0)

def transcribe_meeting_tracks(
        session: Session,
        whisper_host: str,
        whisper_port: int
    ):
    log_dir = os.path.dirname(next(iter(session.tracks.values())).track.wav_file_path) if session.tracks else None
    logger = get_logger("transcribe", log_dir)

    logger.info(f"Transcription started for session {session.session_id} ({len(session.tracks)} track(s))")
    tracks = {}
    for participant_id, participant_data in session.tracks.items():
        wav_file_path = participant_data.track.wav_file_path
        os.makedirs(os.path.dirname(wav_file_path), exist_ok=True)
        logger.info(f"Transcribing track for participant {participant_data.participant_name} (id: {participant_id})")
        transcription = transcribe(wav_file_path, whisper_host, int(whisper_port))
        transcription_text = transcription.get("text", "")
        transcription_segments = transcription.get("segments") or []
        track_path = Path(wav_file_path)
        transcription_text_output_path = str(track_path.with_name(f"{track_path.stem}_transcription_text.txt"))
        segments_input_path = track_path.with_name(f"{track_path.stem}_transcription_segments.txt")
        with open(transcription_text_output_path, "w") as f:
            f.write(transcription_text)
        # Returns the .json path it renamed to, which is what actually exists.
        transcription_segments_output_path = str(
            format_and_save_single_segment(segments_input_path, transcription_segments)
        )

        logger.info(f"Transcription finished for participant {participant_data.participant_name} (id: {participant_id})")
        tracks[participant_id] = ParticipantDataTransc(
            participant_id=participant_id,
            participant_name=participant_data.participant_name,
            track=participant_data.track,
            transcription=Transcription(
                transcription_text_file_path=transcription_text_output_path,
                transcription_segments_file_path=transcription_segments_output_path
            )
        )

    logger.info(f"Transcription completed for session {session.session_id}")
    return TranscriptedSession(
        session_id=session.session_id,
        session_start=session.session_start,
        session_end=session.session_end,
        tracks=tracks
    )


def transcribe(
    audio_file: str,
    whisper_server_host: str,
    whisper_server_port: int,
    timeout: tuple[float, float] = _WHISPER_TIMEOUT_S,
) -> dict:
    # Without a timeout a stalled whisper server pins this thread forever, and
    # since the routes run in the threadpool, enough of those stop the API from
    # serving anything at all.
    url = f"http://{whisper_server_host}:{whisper_server_port}/inference"
    with open(audio_file, "rb") as audio:
        response = requests.post(
            url,
            files={
                "file": (os.path.basename(audio_file), audio, "audio/wav")
            },
            data={
                "temperature": "0.0",
                "response_format": "verbose_json"
            },
            timeout=timeout,
        )
    if response.status_code != 200:
        raise WhisperError(response.status_code, response.text, url, audio_file)

    # whisper.cpp usually returns the transcription under the "text" key when using verbose_json
    return response.json()