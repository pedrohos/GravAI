from pydantic import BaseModel

from gravai.models.models import ParticipantData, Session, TranscriptedSession


class CaptchaChallenge(BaseModel):
    """A CAPTCHA on a Google sign-in that is waiting for somebody to type it.

    The recording that is waiting holds its own HTTP request open for as long as
    the meeting runs, so nothing it learns can come back in that reply. This is
    the other channel: connect a VNC client to vnc_url, answer the challenge, and
    the recorder carries on by itself.
    """

    state: str
    session_dir: str
    meeting_url: str | None = None
    vnc_url: str
    vnc_host: str
    vnc_port: int
    # Only ever the throwaway password generated for this one challenge. A
    # VNC_PASSWORD configured by the operator is theirs and is not repeated here.
    vnc_password: str | None = None
    screenshot_path: str | None = None
    expires_at: str
    updated_at: str


class RecordMeetingResponse(BaseModel):
    recording_path: str
    session_metadata_path: str | None = None
    session: Session[ParticipantData] | None = None


class TranscribeResponse(BaseModel):
    recording_path: str
    session_metadata_path: str
    transcribed_slices_session: TranscriptedSession
