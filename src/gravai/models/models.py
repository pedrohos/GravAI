from enum import Enum
from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel


class RecordingType(Enum):
    TEAMS = "teams"
    MEET = "meet"

class ActionType(Enum):
    START = 0
    END = 1

class TrackInfoDTO(BaseModel):
    # action: ActionType
    participant_name: str
    element_id: str
    timestamp: int
    class_count: int
    # segments: list[AudioSegmentTime]

class SessionDataDTO(BaseModel):
    start_time: datetime
    end_time: datetime
    main_track_name: str
    session_id: str
    events: list[TrackInfoDTO]

class SpeechSegment(BaseModel):
    """One stretch of the meeting a participant was speaking, in seconds from the
    start of the main track."""

    start: float
    end: float


class Track(BaseModel):
    # start_time: int
    # end_time: int
    wav_file_path: str
    # The same audio with the silence removed, and the segments it was built
    # from. Whisper invents speech in silence - a track that is 220 seconds long
    # around 4 seconds of talking comes back with "E aí" every 30 seconds, which
    # is its window length - so transcription reads this one and maps the offsets
    # it gets back onto the meeting timeline through `speech_segments`.
    speech_wav_file_path: str | None = None
    speech_segments: list[SpeechSegment] = []

class ParticipantData(BaseModel):
    participant_id: str
    participant_name: str
    track: Track

class Transcription(BaseModel):
    transcription_text_file_path: str
    transcription_segments_file_path: str

class ParticipantDataTransc(ParticipantData):
    transcription: Transcription

ParticipantDataT = TypeVar("ParticipantDataT", bound=ParticipantData)


class Session(BaseModel, Generic[ParticipantDataT]):
    session_id: str
    session_start: datetime
    session_end: datetime
    tracks: dict[str, ParticipantDataT]  # Map participant_id to ParticipantData


class TranscriptedSession(Session[ParticipantDataTransc]):
    pass
