"""What a job is, and the states one can be in.

Everything this service does to a meeting takes minutes to hours: a recording
runs for the length of the call, and transcribing an hour of audio keeps whisper
busy long after that. None of it fits inside an HTTP request, so nothing is
attempted inside one. A caller asks for work, gets an id back immediately, and
comes back to that id for the answer.
"""

from pydantic import BaseModel

from gravai.models.database import Job, JobStatus, JobType

__all__ = ["Job", "JobStatus", "JobType", "JobSubmission"]


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
