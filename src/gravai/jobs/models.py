"""What a job is, and the states one can be in.

Everything this service does to a meeting takes minutes to hours: a recording
runs for the length of the call, and transcribing an hour of audio keeps whisper
busy long after that. None of it fits inside an HTTP request, so nothing is
attempted inside one. A caller asks for work, gets an id back immediately, and
comes back to that id for the answer.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


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


class Job(BaseModel):
    """One unit of asked-for work and whatever has come of it so far."""

    id: str
    type: JobType
    status: JobStatus
    # Exactly what the caller asked for, kept verbatim so a job can be read back
    # - or resubmitted - without the route that created it.
    params: dict = Field(default_factory=dict)
    # The pipeline's own answer, shaped by job type; None until it finishes.
    result: dict | None = None
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


class JobSubmission(BaseModel):
    """A request for work.

    One shape for all three job types rather than three routes: which fields
    matter follows from `type`, and the ones that do not are ignored. Validation
    of the combination happens where the job is submitted, so a bad request is
    refused before a row exists.
    """

    type: JobType
    meeting_url: str | None = None
    # For TRANSCRIBE: a directory a previous recording wrote.
    tracks_output_dir: str | None = None
    slice_tracks: bool = True
    group_slices_by_name: bool = True
