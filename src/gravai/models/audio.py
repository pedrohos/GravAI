"""Format-independent metadata shared by audio capture and slicing."""

from datetime import datetime

from pydantic import AliasChoices, BaseModel, Field


class AudioTrackMetadata(BaseModel):
    """Capture timing and format for one track, including a running capture."""

    started_at: datetime
    ended_at: datetime | None = None
    sample_rate: int | None = Field(default=None, gt=0)
    channels: int | None = Field(default=None, gt=0)
    audio_file: str | None = Field(
        default=None, validation_alias=AliasChoices("audio_file", "wav_file")
    )


class SessionAudioMetadata(BaseModel):
    """The session sidecar written by capture and read by slicing."""

    session_id: str
    session_start: datetime | None = None
    tracks: dict[str, AudioTrackMetadata] = Field(default_factory=dict)
