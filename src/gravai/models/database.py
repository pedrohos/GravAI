"""Pydantic DTOs for SQLite rows, write inputs, and JSON columns."""

from datetime import datetime
from enum import Enum
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_serializer,
    model_validator,
)

from gravai.models.common import (
    ParticipantData,
    Session,
    SpeechSegment,
    TranscriptedSession,
)


class JobType(str, Enum):
    """The kinds of work that can be asked for.

    RECORD joins a meeting and, unless told otherwise, cuts the mix into one
    track per participant. TRANSCRIBE picks up a directory that was already
    recorded - re-slicing it first, since slicing is cheap and its result is what
    transcription reads. RECORD_AND_TRANSCRIBE is the whole pipeline in one job,
    which is what most callers want, because the alternative is polling for the
    end of a recording only to submit its directory straight back.
    """

    RECORD = "record"
    TRANSCRIBE = "transcribe"
    #: An ask, never the type of a row. Submitting it creates a RECORD job and a
    #: TRANSCRIBE job waiting on it - see runner.submit.
    RECORD_AND_TRANSCRIBE = "record_and_transcribe"


class JobStatus(str, Enum):
    """Where a job is.

    STOPPING is its own state rather than a flag on RUNNING because it is what a
    caller sees between asking a recording to leave the meeting and that
    recording noticing: the meeting is being wound up and the audio still has to
    be finalized, sliced and transcribed, so the job is neither still recording
    nor finished. A stop that lands ends in SUCCEEDED - there is a recording -
    while CANCELLED is the outcome of killing the job outright, where there is
    not.
    """

    QUEUED = "queued"
    #: Held until the job it depends on finishes. A transcription created
    #: alongside a recording sits here for the length of the meeting: its
    #: process exists and can be cancelled, but it has not started work and the
    #: directory it will read does not exist yet.
    WAITING = "waiting"
    RUNNING = "running"
    STOPPING = "stopping"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATUSES


_TERMINAL_STATUSES = frozenset(
    {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
)


class DatabaseDTO(BaseModel):
    """Validated data crossing the SQLite boundary."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TranscriptSegmentDTO(BaseModel):
    """Whisper timings and text, preserving optional upstream metadata."""

    model_config = ConfigDict(extra="allow")
    start: float | None = None
    end: float | None = None
    text: str | None = None


def _segment_payloads(
    segments: list[TranscriptSegmentDTO] | None,
) -> list[dict[str, JsonValue]] | None:
    return (
        [segment.model_dump(mode="json", exclude_unset=True) for segment in segments]
        if segments is not None
        else None
    )


class ParticipantDTO(DatabaseDTO):
    participant_id: str
    participant_name: str | None = None
    track_path: str | None = None
    speech_track_path: str | None = None
    segments: list[SpeechSegment] = Field(default_factory=list)
    transcript_text: str | None = None
    transcript_segments: list[TranscriptSegmentDTO] | None = None

    @field_serializer("transcript_segments")
    def serialize_transcript_segments(
        self, segments: list[TranscriptSegmentDTO] | None
    ) -> list[dict[str, JsonValue]] | None:
        return _segment_payloads(segments)


class RecordingDataDTO(DatabaseDTO):
    session_dir: str
    status: str
    meeting_url: str | None = None
    provider: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    main_track_path: str | None = None
    meeting_transcript_text: str | None = None
    meeting_transcript_segments: list[TranscriptSegmentDTO] | None = None

    @field_serializer("meeting_transcript_segments")
    def serialize_transcript_segments(
        self, segments: list[TranscriptSegmentDTO] | None
    ) -> list[dict[str, JsonValue]] | None:
        return _segment_payloads(segments)


class RecordingUpsertDTO(RecordingDataDTO):
    recording_id: str

    def to_json(self) -> dict[str, JsonValue]:
        """Known optional columns as JSON values; Core handles their SQL encoding."""
        return self.model_dump(
            mode="json",
            exclude={"recording_id", "session_dir", "status"},
            exclude_none=True,
        )


class RecordingDTO(RecordingDataDTO):
    id: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    participants: list[ParticipantDTO] = Field(default_factory=list)


class JobParamsDTO(DatabaseDTO):
    meeting_url: str | None = None
    provider: str | None = None
    tracks_output_dir: str | None = None
    participant_id: str | None = None
    slice_tracks: bool = True
    group_slices_by_name: bool = True


class JobResultDTO(DatabaseDTO):
    recording_path: str | None = None
    session_metadata_path: str | None = None
    recording_id: str | None = None
    session: Session[ParticipantData] | None = None
    transcribed_slices_session: TranscriptedSession | None = None

    @field_serializer("session", "transcribed_slices_session")
    def serialize_session(
        self, session: Session[ParticipantData] | TranscriptedSession | None
    ) -> dict[str, JsonValue] | None:
        # Session snapshots retain their complete shape even when the surrounding
        # job result only serializes fields supplied by the pipeline.
        return session.model_dump(mode="json") if session is not None else None


class JobPayloadDTO(DatabaseDTO):
    """Sparse JSON payloads retain the keys the caller actually supplied."""

    @field_serializer("params", check_fields=False)
    def serialize_params(
        self, params: JobParamsDTO | None
    ) -> dict[str, JsonValue] | None:
        return (
            params.model_dump(mode="json", exclude_unset=True)
            if params is not None
            else None
        )

    @field_serializer("result", check_fields=False)
    def serialize_result(
        self, result: JobResultDTO | None
    ) -> dict[str, JsonValue] | None:
        return (
            result.model_dump(mode="json", exclude_unset=True)
            if result is not None
            else None
        )


class Job(JobPayloadDTO):
    """One unit of asked-for work and whatever has come of it so far."""

    id: str
    type: JobType
    status: JobStatus
    # Exactly what the caller asked for, kept verbatim so a job can be read back
    # - or resubmitted - without the route that created it.
    params: JobParamsDTO = Field(default_factory=JobParamsDTO)
    # The pipeline's own answer, shaped by job type; None until it finishes.
    result: JobResultDTO | None = None
    error: str | None = None

    # The process group running this job. It is what a cancel signals, and it is
    # also how a restart tells a job that is still going from one that died with
    # the process that was running it - which is what pid_start is for, since a
    # pid on its own is reused and would eventually name somebody else's process.
    pid: int | None = None
    pid_start: int | None = None

    # Both are filled in as soon as the recorder has them, which is minutes
    # before the job produces a result - they are what makes an in-flight
    # recording findable at all.
    recording_id: str | None = None
    session_dir: str | None = None

    # The job that has to finish before this one starts, if any. Set on the
    # transcription half of a record-and-transcribe, which cannot run until the
    # recording it reads exists.
    depends_on: str | None = None

    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stop_requested_at: datetime | None = None


class JobUpdateDTO(JobPayloadDTO):
    """Only supplied fields are updated; an explicit None clears a nullable field."""

    type: JobType | None = None
    status: JobStatus | None = None
    params: JobParamsDTO | None = None
    result: JobResultDTO | None = None
    error: str | None = None
    pid: int | None = None
    pid_start: int | None = None
    recording_id: str | None = None
    session_dir: str | None = None
    depends_on: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stop_requested_at: datetime | None = None

    @model_validator(mode="after")
    def validate_non_nullable_columns(self) -> Self:
        for column in ("type", "status", "params", "created_at"):
            if column in self.model_fields_set and getattr(self, column) is None:
                raise ValueError(f"Job column {column} cannot be null")
        return self
