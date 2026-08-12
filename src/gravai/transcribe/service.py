from src.gravai.models.models import Session
from src.gravai.transcribe.base import transcribe_meeting_tracks as transcribe_meeting_tracks_base
from src.gravai.config.settings import Settings

def transcribe_meeting_tracks(
        session: Session,
        whisper_server_host: str | None = None,
        whisper_server_port: int | None = None
    ):
    settings = Settings() # type: ignore
    whisper_host = whisper_server_host or settings.WHISPER_HOST
    whisper_port = int(whisper_server_port or settings.WHISPER_PORT)
    return transcribe_meeting_tracks_base(
        session, 
        whisper_host,
        whisper_port
    )