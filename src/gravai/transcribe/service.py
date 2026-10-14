from __future__ import annotations

from gravai.config.settings import get_settings
from gravai.models.common import ParticipantDataT, Session, TranscriptedSession
from gravai.transcribe.base import (
    transcribe_meeting_tracks as transcribe_meeting_tracks_base,
)


def transcribe_meeting_tracks(
        session: Session[ParticipantDataT],
        whisper_server_host: str | None = None,
        whisper_server_port: int | None = None,
        language: str | None = None,
        transcribe_meeting: bool = True,
    ) -> TranscriptedSession:
    settings = get_settings()
    whisper_host = whisper_server_host or settings.WHISPER_HOST
    whisper_port = int(whisper_server_port or settings.WHISPER_PORT)
    return transcribe_meeting_tracks_base(
        session, 
        whisper_host,
        whisper_port,
        language or settings.WHISPER_LANGUAGE,
        transcribe_meeting=transcribe_meeting,
    )