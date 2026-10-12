"""Running a job in a process of its own, and being able to stop it.

Every job here holds real, long-lived resources: a Chrome that is sitting in a
meeting, an X display under it, an audio server writing wav files, an ffmpeg
pass, a whisper request that takes minutes. A thread cannot be taken away from
any of that, which is the whole reason a job is a process.

Each job process calls setsid() before it does anything else, so it becomes the
leader of a process group of its own and everything it starts joins that group.
That is what makes cancelling possible at all: one signal to the group reaches
the browser, the display and the audio server, and none of it reaches any other
job.

Two ways out are offered, and they are not the same thing:

- **stop** asks a recording to leave the meeting. It is a request, delivered
  through the session directory, that the call loop notices on its next pass -
  after which the recording finishes normally and is sliced and transcribed. The
  meeting is cut short; the recording is not lost.
- **cancel** signals the process group. Nothing is finalized, the wav headers are
  never closed, and there is no recording at the end of it. It is for a job that
  is stuck or unwanted, not for ending a meeting early.
"""

import multiprocessing as mp
import os
import signal
import time

from typing import Any
from uuid import uuid4

from gravai.api import pipeline
from gravai.config.logging_config import get_logger
from gravai.jobs import catalogue, stop_signal, store
from gravai.jobs.models import Job, JobStatus, JobSubmission, JobType
from gravai.models.common import RecordingType

logger = get_logger("jobs.runner")

# How long a cancelled process group is given to die on SIGTERM before SIGKILL.
# It is a Chrome and an ffmpeg being asked to go, not a graceful shutdown of a
# meeting - that is what stop is for - so the window is short.
_TERM_GRACE_S = 10.0
_TERM_POLL_INTERVAL_S = 0.25

# How often a waiting job asks whether the one it depends on has finished. The
# wait is measured in the length of a meeting, so a second between reads is
# already far finer than it needs to be and costs one small query.
_DEPENDENCY_POLL_INTERVAL_S = 1.0

class JobError(Exception):
    """A job request that cannot be carried out as asked."""


class JobNotFound(JobError):
    pass


class JobNotStoppable(JobError):
    """The job exists but there is nothing to stop, or nothing to stop gently."""


class _DependencyFailed(Exception):
    """The job this one was waiting for did not produce anything to work on."""


class _Cancelled(BaseException):
    """Raised in a job process by the SIGTERM a cancel sends.

    Deliberately not an Exception: the pipeline catches Exception in several
    places to turn a failure into a reported error, and a cancellation caught
    there would be reported as the job having failed on its own.
    """


# Spawned children are kept so that the process objects can be joined and their
# entries in the process table freed; a job's fate is read from the database, not
# from here, because another worker - or the API after a restart - has the
# database and does not have this.
_children: dict[str, mp.process.BaseProcess] = {}


def _reap() -> None:
    for job_id, process in list(_children.items()):
        if not process.is_alive():
            process.join(timeout=0)
            _children.pop(job_id, None)


def process_start_ticks(pid: int) -> int | None:
    """When a process started, in clock ticks since boot, or None off Linux.

    Read from /proc/<pid>/stat. The process name sits in that line in
    parentheses and is allowed to contain both spaces and parentheses of its own,
    so the fields are counted from the last closing one rather than by splitting
    the whole line.
    """
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            line = f.read()
    except OSError:
        return None
    try:
        # Fields from the third (state) onwards; start time is the 22nd overall.
        fields = line[line.rindex(b")") + 2 :].split()
        return int(fields[19])
    except (ValueError, IndexError):
        return None


def _process_alive(pid: int | None, pid_start: int | None = None) -> bool:
    """Whether the process that was running a job is still the process at that pid.

    Aliveness on its own is the wrong question. Pids are reused, and every job
    row read at startup was written by a process that has since gone, so a pid
    that now belongs to something else would read as a job still running - and
    that job would never be closed out. Comparing the recorded start time settles
    it exactly.

    A row with no start time recorded - written before this existed, or on a
    system with no /proc - falls back to aliveness, which errs towards leaving a
    job alone rather than failing one that is still going.
    """
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive and owned by somebody else, which a job of ours never is.
        return False

    if pid_start is None:
        return True
    current = process_start_ticks(pid)
    return current is None or current == pid_start


# --- submitting ------------------------------------------------------------


def _validate(submission: JobSubmission) -> dict:
    """Turns a request into the parameters a job runs on, or refuses it.

    Refusing here rather than in the job process is what lets a caller find out
    that a URL is not a meeting, or a directory does not exist, in the response
    to their submission - the alternative is a job row that exists only to report
    a mistake made before it started.
    """
    params: dict[str, Any] = {}
    if submission.type in (JobType.RECORD, JobType.RECORD_AND_TRANSCRIBE):
        if not submission.meeting_url:
            raise JobError(f"A {submission.type.value} job needs a meeting_url.")
        # Raises UnsupportedMeetingURL for a URL no provider claims, and
        # NotImplementedError for a provider that is registered but not built -
        # both of which the routes already map onto status codes.
        recording_type = pipeline.detect_recording_type(submission.meeting_url)
        params["meeting_url"] = submission.meeting_url
        params["provider"] = recording_type.value
        if submission.type is JobType.RECORD:
            params["slice_tracks"] = submission.slice_tracks
        else:
            # The transcription that follows slices the directory itself, so
            # slicing here would only be thrown away and done again.
            params["slice_tracks"] = False
            params["group_slices_by_name"] = submission.group_slices_by_name
    else:
        if not submission.tracks_output_dir:
            raise JobError("A transcribe job needs tracks_output_dir.")
        if not os.path.isdir(submission.tracks_output_dir):
            raise JobError(f"No such recording directory: {submission.tracks_output_dir}")
        params["tracks_output_dir"] = submission.tracks_output_dir
        params["group_slices_by_name"] = submission.group_slices_by_name
    return params


def submit(submission: JobSubmission) -> Job:
    """Accepts a request for work, starts it, and returns before it has done any.

    A record-and-transcribe is two jobs, not one: a recording, and a
    transcription waiting on it. They fail for unrelated reasons - a meeting
    that refuses the bot has nothing to do with whisper being down - and as one
    job either failure reported the whole meeting as lost, with no way to retry
    the half that was actually broken. Split, a whisper outage leaves a finished
    recording beside one failed transcription that can be run again over the
    audio it already has.

    The recording is what comes back, being the head of the chain and the job
    that is worth watching; the transcription is in the job list from the same
    moment, `waiting` and pointing at it through `depends_on`.
    """
    _reap()
    params = _validate(submission)

    if submission.type is not JobType.RECORD_AND_TRANSCRIBE:
        return _start(submission.type, params)

    record = _start(
        JobType.RECORD,
        {key: value for key, value in params.items() if key != "group_slices_by_name"},
    )
    _start(
        JobType.TRANSCRIBE,
        {"group_slices_by_name": params.get("group_slices_by_name", True)},
        depends_on=record.id,
        status=JobStatus.WAITING,
    )
    return record


def _start(
    job_type: JobType,
    params: dict,
    depends_on: str | None = None,
    status: JobStatus = JobStatus.QUEUED,
) -> Job:
    """Creates one job row and the process that runs it."""
    job_id = str(uuid4())
    job = store.create_job(job_id, job_type, params, depends_on=depends_on, status=status)

    context = mp.get_context("spawn")
    process = context.Process(target=_run_job, args=(job_id,), name=f"gravai-job-{job_id[:8]}")
    process.start()
    _children[job_id] = process
    # Written by the parent as well as by the job itself: a job that dies in its
    # first instants never gets to record its own pid, and without one nothing
    # can tell that row apart from a job that was never started.
    store.update_job(job_id, pid=process.pid, pid_start=process_start_ticks(process.pid))

    waiting_on = f" waiting on {depends_on}" if depends_on else ""
    logger.info(f"Submitted {job_type.value} job {job_id} as pid {process.pid}{waiting_on}")
    return job.model_copy(update={"pid": process.pid})


# --- the job process -------------------------------------------------------


def _run_job(job_id: str) -> None:
    """Entry point of a job process. Runs in its own process group."""
    try:
        os.setsid()
    except OSError:
        # Already a group leader, which is fine - the point is only that the
        # group is ours and not the API's.
        pass

    signal.signal(signal.SIGTERM, _raise_cancelled)

    own_pid = os.getpid()
    job = store.get_job(job_id)
    if job is None:
        return

    if job.depends_on:
        # Its own pid is recorded before the wait, not after: for the length of
        # a meeting this row is `waiting` rather than `running`, and without a
        # live pid on it a restart's reconcile would read it as a job whose
        # process died and fail it.
        store.update_job(job_id, pid=own_pid, pid_start=process_start_ticks(own_pid))
        try:
            _await_dependency(job)
        except _Cancelled:
            raise
        except _DependencyFailed as exc:
            logger.warning(f"Job {job_id} will not run: {exc}")
            store.finish_job(job_id, JobStatus.CANCELLED, error=str(exc))
            return
        job = store.get_job(job_id) or job

    store.mark_started(job_id, own_pid, process_start_ticks(own_pid))

    logger.info(f"Job {job_id} ({job.type.value}) started in pid {os.getpid()}")
    try:
        result = _dispatch(job)
    except _Cancelled:
        logger.warning(f"Job {job_id} was cancelled")
        store.finish_job(job_id, JobStatus.CANCELLED, error="The job was cancelled.")
        _mark_recording_failed(job_id)
        # Straight out, and with the exit code a SIGTERM conventionally leaves:
        # the pipeline's cleanup has already been unwound by the exception, and
        # everything else in this process group is being killed anyway.
        os._exit(128 + signal.SIGTERM)
    except BaseException as exc:
        logger.exception(f"Job {job_id} failed")
        store.finish_job(job_id, JobStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
        _mark_recording_failed(job_id)
        return

    store.finish_job(job_id, JobStatus.SUCCEEDED, result=result)
    logger.info(f"Job {job_id} finished")


def _raise_cancelled(signum, frame) -> None:
    raise _Cancelled()


def _await_dependency(job: Job) -> None:
    """Blocks until the job this one depends on has finished, or refuses to run.

    The waiting is done by the dependent job's own process rather than by a
    scheduler in the API. A job here is already a process - it is what cancel
    signals and what reconcile checks for after a restart - and a queue of
    pending jobs with no process behind them would need all of that built again
    for the one case of a transcription waiting on a recording. Sleeping for the
    length of a meeting costs an idle process, which is what the recording it is
    waiting for is anyway.

    A dependency that did not succeed is not something to wait longer for: the
    recording either has no directory or has one nothing finished writing, and
    transcribing it would fail on its own a moment later with a worse
    explanation than this one.
    """
    logger.info(f"Job {job.id} is waiting for job {job.depends_on} to finish")
    while True:
        dependency = store.get_job(job.depends_on) if job.depends_on else None
        if dependency is None:
            raise _DependencyFailed(
                f"The job it was waiting for ({job.depends_on}) no longer exists."
            )
        if dependency.status is JobStatus.SUCCEEDED:
            logger.info(f"Job {job.id} is starting: {job.depends_on} succeeded")
            return
        if dependency.status.is_terminal:
            raise _DependencyFailed(
                f"The recording it was waiting for ({dependency.id}) ended as "
                f"{dependency.status.value}, so there is nothing to transcribe."
            )
        time.sleep(_DEPENDENCY_POLL_INTERVAL_S)


def _mark_recording_failed(job_id: str) -> None:
    """Leaves the meeting this job was producing marked as failed, if there is one."""
    job = store.get_job(job_id)
    if job is None or not job.recording_id or not job.session_dir:
        return
    store.upsert_recording(
        recording_id=job.recording_id,
        session_dir=job.session_dir,
        status=catalogue.STATUS_FAILED,
    )


def _sliced_hook(job_id: str, session_dir: str, meeting_url: str | None, provider: str | None):
    """Callback that catalogues a recording the moment slicing is done with it.

    Transcription is the one stage of this pipeline that fails for reasons that
    have nothing to do with the meeting - whisper is a separate service and it
    is routinely not running - and until now a failure there took the whole
    catalogue write with it, because the only write happened afterwards. The
    audio was on disk the entire time and the recording read as if it held
    nothing: no mixed track, no speakers, no turns.

    None of that came from whisper, so none of it waits for whisper. The row is
    written here with what slicing produced and rewritten after transcription
    with the text on top; a second write costs one statement and is the reason a
    failed transcription now leaves a recording somebody can still listen to.
    """

    def on_sliced(_metadata_path: str, session) -> None:
        catalogue.save_session(
            session,
            session_dir,
            meeting_url,
            provider,
            catalogue.STATUS_PROCESSING,
        )
        # Slicing is the first thing to name the session, so a transcription of a
        # directory the catalogue had never seen gets its recording linked to the
        # job here. Without it a failure a moment later has no row to mark.
        store.attach_session(job_id, session.session_id, session_dir)

    return on_sliced


def _session_hook(job_id: str, meeting_url: str | None, provider: str | None):
    """Callback the recorder runs as soon as it has a session directory.

    This is the only moment at which the job, the meeting and the directory are
    all known at once, and it happens minutes before the job produces anything -
    so it is what makes a recording in progress visible in the catalogue and,
    through session_dir, what makes it possible to ask it to stop.
    """

    def on_session_start(session_id: str, session_dir: str) -> None:
        store.attach_session(job_id, session_id, session_dir)
        catalogue.register_recording(
            recording_id=session_id,
            session_dir=session_dir,
            meeting_url=meeting_url,
            provider=provider,
            status=catalogue.STATUS_RECORDING,
        )
        logger.info(f"Job {job_id} is recording session {session_id} into {session_dir}")

    return on_session_start


def _dispatch(job: Job) -> dict:
    params = job.params
    if job.type is JobType.RECORD:
        return _run_record(job, params)
    if job.type is JobType.RECORD_AND_TRANSCRIBE:
        # Never reached for anything submitted now: submit() expands the ask
        # into a record job and a transcribe job waiting on it. Only a row
        # written by an older version can still carry this type.
        raise JobError(
            f"Job {job.id} is a {job.type.value}, which is no longer run as one job. "
            f"Submit it again to get a recording and a transcription."
        )
    return _run_transcribe(job, params)


def _run_record(job: Job, params: dict) -> dict:
    meeting_url = params["meeting_url"]
    provider = params.get("provider")
    tracks_output_dir, metadata_path, session = pipeline.record(
        meeting_url,
        slice_tracks=params.get("slice_tracks", True),
        on_session_start=_session_hook(job.id, meeting_url, provider),
    )

    if session is not None:
        catalogue.save_session(
            session, tracks_output_dir, meeting_url, provider, catalogue.STATUS_COMPLETE
        )
    elif job.recording_id:
        # Nothing was sliced, so there are no participants to catalogue - but the
        # meeting was recorded and its directory is real, and a row that stayed
        # on "recording" forever would say otherwise.
        #
        # Complete only if this is the end of it. With a transcription waiting on
        # this recording there is more to come, and saying "complete" now would
        # mean the meeting reads as finished with no speakers for as long as
        # whisper takes - and then changes its mind.
        store.upsert_recording(
            recording_id=job.recording_id,
            session_dir=tracks_output_dir,
            status=(
                catalogue.STATUS_PROCESSING
                if store.dependents_of(job.id)
                else catalogue.STATUS_COMPLETE
            ),
        )

    return {
        "recording_path": tracks_output_dir,
        "session_metadata_path": metadata_path,
        "recording_id": job.recording_id,
        "session": session.model_dump(mode="json") if session is not None else None,
    }


def _recorded_directory(job: Job) -> str:
    """Where the recording this job waited for put its audio.

    Not known when the job was created: the directory is named by the recorder
    from a session id it makes on its way into the meeting, minutes after
    anybody asked for the transcription. So it is read from the finished
    recording rather than passed in, which is also why a transcription created
    this way carries no tracks_output_dir of its own.
    """
    dependency = store.get_job(job.depends_on) if job.depends_on else None
    if dependency is None:
        raise JobError(f"Job {job.id} has no recording to transcribe.")
    directory = dependency.session_dir or (dependency.result or {}).get("recording_path")
    if not directory:
        raise JobError(
            f"The recording job {dependency.id} finished without a directory, so there is "
            f"nothing for this transcription to read."
        )
    return directory


def _run_transcribe(job: Job, params: dict) -> dict:
    tracks_output_dir = params.get("tracks_output_dir") or _recorded_directory(job)

    # A directory that has been recorded before is already a meeting in the
    # catalogue, and it is the only thing that still knows which provider it came
    # from and what URL it was - neither survives into the files on disk.
    existing = store.get_recording_by_session_dir(tracks_output_dir)
    meeting_url = existing.get("meeting_url") if existing else None
    provider = existing.get("provider") if existing else None
    recording_type = RecordingType(provider) if provider else RecordingType.TEAMS

    if existing:
        store.attach_session(job.id, existing["id"], tracks_output_dir)
        store.upsert_recording(
            recording_id=existing["id"],
            session_dir=tracks_output_dir,
            status=catalogue.STATUS_PROCESSING,
        )

    metadata_path, transcribed = pipeline.transcribe_tracks(
        tracks_output_dir,
        recording_type=recording_type,
        group_slices_by_name=params.get("group_slices_by_name", True),
        on_sliced=_sliced_hook(job.id, tracks_output_dir, meeting_url, provider),
    )
    catalogue.save_session(
        transcribed, tracks_output_dir, meeting_url, provider, catalogue.STATUS_COMPLETE
    )
    store.attach_session(job.id, transcribed.session_id, tracks_output_dir)

    return {
        "recording_path": tracks_output_dir,
        "session_metadata_path": metadata_path,
        "recording_id": transcribed.session_id,
        "transcribed_slices_session": transcribed.model_dump(mode="json"),
    }


# --- stopping and cancelling ----------------------------------------------


def _require_job(job_id: str) -> Job:
    job = store.get_job(job_id)
    if job is None:
        raise JobNotFound(f"No job with id {job_id}.")
    return job


def stop(job_id: str) -> Job:
    """Asks a recording job to leave its meeting and finish what it has.

    Only a job that is actually in a meeting can be stopped this way: a
    transcription has no meeting to leave and there is no point at which
    abandoning it leaves something usable, so it is refused here and has to be
    cancelled deliberately rather than killed under the friendlier name.
    """
    job = _require_job(job_id)

    if job.status.is_terminal:
        raise JobNotStoppable(f"Job {job_id} already finished as {job.status.value}.")
    if job.status is JobStatus.STOPPING:
        return job
    if job.type is JobType.TRANSCRIBE:
        raise JobNotStoppable(
            f"Job {job_id} is a transcription: it has no meeting to leave, and stopping it "
            f"part-way leaves nothing usable. Cancel it instead."
        )
    if not job.session_dir:
        raise JobNotStoppable(
            f"Job {job_id} has not started recording yet, so there is no meeting to leave. "
            f"Try again in a moment, or cancel it."
        )

    stop_signal.request_stop(job.session_dir, reason=f"stop requested for job {job_id}")
    store.mark_stop_requested(job_id)
    logger.info(f"Asked job {job_id} to leave its meeting ({job.session_dir})")
    return _require_job(job_id)


def cancel(job_id: str) -> Job:
    """Kills a job's process group. Nothing it was producing is finalized."""
    job = _require_job(job_id)
    if job.status.is_terminal:
        raise JobNotStoppable(f"Job {job_id} already finished as {job.status.value}.")

    if _process_alive(job.pid, job.pid_start):
        _kill_group(job.pid)  # type: ignore[arg-type]
    else:
        logger.warning(f"Job {job_id} has no live process (pid {job.pid}); marking it cancelled")

    store.finish_job(job_id, JobStatus.CANCELLED, error="The job was cancelled.")
    if job.recording_id and job.session_dir:
        store.upsert_recording(
            recording_id=job.recording_id,
            session_dir=job.session_dir,
            status=catalogue.STATUS_FAILED,
        )
    _reap()
    return _require_job(job_id)


def _kill_group(pid: int) -> None:
    """SIGTERM the job's process group, then SIGKILL whatever is left of it."""
    if not _is_own_group(pid):
        return

    for signal_number in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, signal_number)
        except ProcessLookupError:
            return
        except OSError as exc:
            logger.warning(f"Could not signal process group {pid}: {exc}")
            return

        if signal_number is signal.SIGKILL:
            return
        deadline = time.time() + _TERM_GRACE_S
        while time.time() < deadline:
            if not _process_alive(pid):
                return
            time.sleep(_TERM_POLL_INTERVAL_S)
        logger.warning(f"Process group {pid} did not stop on SIGTERM; killing it")


def _is_own_group(pid: int) -> bool:
    """Checks that pid really leads a group of its own before signalling it.

    A job process calls setsid() so that its group holds nothing but itself and
    what it started. If that ever did not happen, its group would be the API's,
    and a cancel would take the whole service down along with every other
    recording running in it - so the group is confirmed here rather than assumed,
    and a job that is somehow not in one is left alone.
    """
    try:
        group = os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return False

    if group != pid:
        logger.error(
            f"Refusing to signal process group {group}: pid {pid} does not lead a group of its "
            f"own, so killing it would reach processes that are not this job."
        )
        return False
    return True


def reconcile() -> list[str]:
    """Closes out jobs whose process did not survive.

    Called when the API starts. A job process outlives the API that started it -
    it is in its own session, and a reload of the server has no business ending a
    meeting - so a job whose process is still there is left exactly as it is. One
    whose process is gone was killed with the machine or with the container, and
    nothing is ever going to finish it or report it, so it is failed here rather
    than left claiming to be running for good.
    """
    failed = []
    for job in store.active_jobs():
        if _process_alive(job.pid, job.pid_start):
            continue
        store.finish_job(
            job.id,
            JobStatus.FAILED,
            error=(
                f"The process running this job (pid {job.pid}) is gone, and the job was still "
                f"{job.status.value}. It was most likely killed when the service stopped."
            ),
        )
        if job.recording_id and job.session_dir:
            store.upsert_recording(
                recording_id=job.recording_id,
                session_dir=job.session_dir,
                status=catalogue.STATUS_FAILED,
            )
        failed.append(job.id)

    if failed:
        logger.warning(f"Failed {len(failed)} job(s) left running by a previous process: {failed}")
    return failed
