from pydantic import BaseModel

from gravai.models.models import ParticipantData, Session, TranscriptedSession


class RecordMeetingResponse(BaseModel):
    recording_path: str
    session_metadata_path: str | None = None
    session: Session[ParticipantData] | None = None


class TranscribeResponse(BaseModel):
    recording_path: str
    session_metadata_path: str
    transcribed_slices_session: TranscriptedSession
