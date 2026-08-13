from gravai.models.models import Session
from gravai.transcribe.base import transcribe_meeting_tracks as transcribe_meeting_tracks_base
from gravai.config.settings import get_settings

def transcribe_meeting_tracks(
        session: Session,
        whisper_server_host: str | None = None,
        whisper_server_port: int | None = None
    ):
    settings = get_settings()
    whisper_host = whisper_server_host or settings.WHISPER_HOST
    whisper_port = int(whisper_server_port or settings.WHISPER_PORT)
    return transcribe_meeting_tracks_base(
        session, 
        whisper_host,
        whisper_port
    )