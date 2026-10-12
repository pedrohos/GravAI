"""Submitting work, watching it, and ending it.

Nothing this service does fits in a request. A recording lasts as long as the
meeting does; transcribing what it captured keeps whisper busy for minutes after
that. So a caller asks for work and gets an id, and the id is what they come back
to - for the result, for the log while it is still going, and to end it early.
"""

import os

from fastapi import APIRouter, HTTPException, Query, status

from gravai.api.routes.errors import handled
from gravai.api.schemas import JobLog
from gravai.config.logging_config import get_logger
from gravai.config.settings import get_settings
from gravai.jobs import runner, store
from gravai.jobs.models import Job, JobStatus, JobSubmission, JobType

logger = get_logger("api.jobs")

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=Job, status_code=status.HTTP_202_ACCEPTED)
def submit_job(submission: JobSubmission) -> Job:
    """Starts a job and returns it before it has done anything.

    What can be refused here is refused here: a URL no provider recognises, a
    provider that is not implemented, a recording directory that is not there.
    Everything after that is the job's own to report.
    """
    logger.info(f"Received job submission: {submission.model_dump(exclude_none=True)}")
    with handled("POST /jobs", submission.type.value):
        return runner.submit(submission)


@router.get("", response_model=list[Job])
def list_jobs(
    job_status: JobStatus | None = Query(default=None, alias="status"),
    job_type: JobType | None = Query(default=None, alias="type"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Job]:
    """Jobs, newest first."""
    with handled("GET /jobs", "the job list"):
        return store.list_jobs(status=job_status, job_type=job_type, limit=limit, offset=offset)


@router.get("/{job_id}", response_model=Job)
def get_job(job_id: str) -> Job:
    """One job: where it is, and its result once it has one."""
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job with id {job_id}.")
    return job


@router.post("/{job_id}/stop", response_model=Job)
def stop_job(job_id: str) -> Job:
    """Asks a recording to leave its meeting and finish normally.

    The meeting ends early; the recording does not. Whatever was captured up to
    that point is finalized, sliced and transcribed exactly as it would have been
    had everyone hung up, and the job goes on to succeed with a real result. The
    recording notices the request on its next pass around the call loop, so this
    returns while the job is still winding up - its status is `stopping` until it
    is done.

    A transcription has no meeting to leave and is refused here: use cancel.
    """
    logger.info(f"Received stop request for job {job_id}")
    with handled("POST /jobs/{job_id}/stop", job_id):
        return runner.stop(job_id)


@router.post("/{job_id}/cancel", response_model=Job)
def cancel_job(job_id: str) -> Job:
    """Kills the job's process group.

    Nothing is finalized: a recording cancelled mid-meeting leaves wav files
    whose headers were never closed, and there is no result at the end of it. For
    ending a meeting early and keeping what it captured, use stop.
    """
    logger.warning(f"Received cancel request for job {job_id}")
    with handled("POST /jobs/{job_id}/cancel", job_id):
        return runner.cancel(job_id)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: str) -> None:
    """Forgets a finished job. A job that is still running has to be ended first."""
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job with id {job_id}.")
    if not job.status.is_terminal:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Job {job_id} is {job.status.value}. Stop or cancel it before deleting it, "
                f"so that nothing is left running with nothing recording that it is."
            ),
        )
    store.delete_job(job_id)


@router.get("/{job_id}/log", response_model=JobLog)
def job_log(
    job_id: str,
    lines: int | None = Query(default=None, ge=1, le=10_000),
) -> JobLog:
    """The tail of the session log a job is writing.

    This is the only way to watch a recording while it happens - what it found in
    the green room, whether it was admitted, whether it is waiting on a CAPTCHA -
    since the job itself says nothing until it is finished. Empty until the
    recorder has a session directory, which is the first thing it makes.
    """
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job with id {job_id}.")

    if not job.session_dir:
        return JobLog(job_id=job_id)

    log_path = os.path.join(job.session_dir, "session.log")
    tail = lines or get_settings().JOB_LOG_TAIL_LINES
    return JobLog(
        job_id=job_id,
        session_dir=job.session_dir,
        log_path=log_path if os.path.exists(log_path) else None,
        lines=_tail(log_path, tail),
    )


def _tail(path: str, lines: int) -> list[str]:
    """The last `lines` lines of a file, without reading the whole of it.

    A meeting writes a long log and the interesting part is always the end, so
    the file is walked backwards a block at a time until enough newlines have
    gone by.
    """
    block_size = 8192
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            remaining = f.tell()
            found = 0
            chunks: list[bytes] = []
            while remaining > 0 and found <= lines:
                read_size = min(block_size, remaining)
                remaining -= read_size
                f.seek(remaining)
                chunk = f.read(read_size)
                found += chunk.count(b"\n")
                chunks.insert(0, chunk)
    except OSError:
        return []

    text = b"".join(chunks).decode("utf-8", "replace")
    return text.splitlines()[-lines:]
