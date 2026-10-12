"""Turning a finished session into rows somebody can read.

The pipeline's own output is a Session: a tree of file paths. That is the right
shape for the pipeline - the next stage wants the wav, not its contents - and
the wrong shape for a caller asking what was said in a meeting, who would have
to fetch a directory listing and then a file per speaker to find out.

This is the one place that opens those files and puts what is in them into the
catalogue, so that reading a meeting back is a single query.
"""

import json
import os
from typing import Any

from gravai.config.logging_config import get_logger
from gravai.jobs import store
from gravai.models.common import Session, TranscriptedSession

logger = get_logger("jobs.catalogue")

#: A recording that is in progress: the browser is in the meeting.
STATUS_RECORDING = "recording"
#: The meeting is over and the audio is being sliced or transcribed.
STATUS_PROCESSING = "processing"
#: Nothing is left to do for this recording.
STATUS_COMPLETE = "complete"
#: The job that was producing it ended without one.
STATUS_FAILED = "failed"


def _read_text(path: str | None) -> str | None:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as exc:
        logger.warning(f"Could not read transcript text {path}: {exc}")
        return None


def _read_json(path: str | None) -> Any:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"Could not read transcript segments {path}: {exc}")
        return None


def register_recording(
    recording_id: str,
    session_dir: str,
    meeting_url: str | None = None,
    provider: str | None = None,
    status: str = STATUS_RECORDING,
) -> None:
    """Puts a meeting in the catalogue before there is anything to say about it.

    Called the moment the recorder has a directory, which is the only point at
    which the meeting URL and the provider are still in hand - by the time a
    Session exists, neither is part of it.
    """
    store.upsert_recording(
        recording_id=recording_id,
        session_dir=session_dir,
        status=status,
        meeting_url=meeting_url,
        provider=provider,
    )


def save_session(
    session: Session | TranscriptedSession,
    session_dir: str,
    meeting_url: str | None = None,
    provider: str | None = None,
    status: str = STATUS_COMPLETE,
) -> str:
    """Writes a session and everything read out of its files into the catalogue.

    Works for a session that was only sliced as well as one that was transcribed:
    the transcript columns simply stay empty for the former, which is exactly
    what a recording job with slicing on produces.
    """
    transcripts = getattr(session, "tracks", {})
    participants = []
    for participant_id, participant in transcripts.items():
        transcription = getattr(participant, "transcription", None)
        participants.append(
            {
                "participant_id": participant_id,
                "participant_name": participant.participant_name,
                "track_path": participant.track.wav_file_path,
                "speech_track_path": participant.track.speech_wav_file_path,
                "segments": [
                    {"start": segment.start, "end": segment.end}
                    for segment in participant.track.speech_segments
                ],
                "transcript_text": _read_text(
                    transcription.transcription_text_file_path if transcription else None
                ),
                "transcript_segments": _read_json(
                    transcription.transcription_segments_file_path if transcription else None
                ),
            }
        )

    meeting_transcription = getattr(session, "meeting_transcription", None)
    store.upsert_recording(
        recording_id=session.session_id,
        session_dir=session_dir,
        status=status,
        meeting_url=meeting_url,
        provider=provider,
        started_at=session.session_start,
        ended_at=session.session_end,
        main_track_path=session.main_track_path,
        meeting_transcript_text=_read_text(
            meeting_transcription.transcription_text_file_path if meeting_transcription else None
        ),
        meeting_transcript_segments=_read_json(
            meeting_transcription.transcription_segments_file_path
            if meeting_transcription
            else None
        ),
    )
    store.replace_participants(session.session_id, participants)
    logger.info(
        f"Catalogued session {session.session_id} ({len(participants)} participant(s)) "
        f"from {session_dir}"
    )
    return session.session_id
