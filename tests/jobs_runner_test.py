"""What a job process does, and the two ways of ending one.

The distinction these cover is the one that matters most in this service: a
stop leaves a recording that can be transcribed, and a cancel does not. They run
real processes, because the thing being tested is a process group - a test
against mocks would pass with the killing broken.
"""

import os
import time

import pytest

from gravai.jobs import runner, store, stop_signal
from gravai.jobs.models import JobStatus, JobSubmission, JobType


def _wait_for(job_id, predicate, timeout=25.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = store.get_job(job_id)
        if job and predicate(job):
            return job
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} stayed at {store.get_job(job_id).status}")


def test_a_url_no_provider_claims_is_refused_before_a_job_exists(database):
    with pytest.raises(Exception) as caught:
        runner.submit(JobSubmission(type=JobType.RECORD, meeting_url="https://example.com/x"))
    assert "No provider matches" in str(caught.value)
    assert store.list_jobs() == []


def test_a_recording_job_needs_a_url_and_a_transcription_needs_a_directory(database):
    with pytest.raises(runner.JobError):
        runner.submit(JobSubmission(type=JobType.RECORD_AND_TRANSCRIBE))
    with pytest.raises(runner.JobError):
        runner.submit(JobSubmission(type=JobType.TRANSCRIBE))
    with pytest.raises(runner.JobError):
        runner.submit(
            JobSubmission(type=JobType.TRANSCRIBE, tracks_output_dir="/no/such/directory")
        )


def test_a_job_that_fails_reports_why(database, tmp_path):
    """An empty directory has no main track, which slicing refuses at once."""
    job = runner.submit(JobSubmission(type=JobType.TRANSCRIBE, tracks_output_dir=str(tmp_path)))
    finished = _wait_for(job.id, lambda j: j.status.is_terminal)

    assert finished.status is JobStatus.FAILED
    assert "Missing main audio track" in finished.error


def test_cancelling_kills_the_whole_process_group(database, tmp_path):
    """A main track with no finalized sidecar makes slicing wait, which is a job
    that will not end on its own - so it is the one to kill."""
    (tmp_path / "track_mainAudio-1.wav").write_bytes(b"")
    job = runner.submit(JobSubmission(type=JobType.TRANSCRIBE, tracks_output_dir=str(tmp_path)))
    running = _wait_for(job.id, lambda j: j.status is JobStatus.RUNNING)
    group = os.getpgid(running.pid)
    assert group == running.pid, "a job has to lead a process group of its own to be killable"

    cancelled = runner.cancel(job.id)
    assert cancelled.status is JobStatus.CANCELLED

    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            break
        time.sleep(0.2)
    else:
        pytest.fail("the job's process group outlived the cancel")


def test_a_transcription_cannot_be_stopped_gently(database, tmp_path):
    """There is no point at which abandoning whisper leaves something usable, so
    stop refuses rather than quietly killing under a friendlier name."""
    (tmp_path / "track_mainAudio-1.wav").write_bytes(b"")
    job = runner.submit(JobSubmission(type=JobType.TRANSCRIBE, tracks_output_dir=str(tmp_path)))
    _wait_for(job.id, lambda j: j.status is JobStatus.RUNNING)

    with pytest.raises(runner.JobNotStoppable) as caught:
        runner.stop(job.id)
    assert "Cancel it instead" in str(caught.value)

    runner.cancel(job.id)


def test_a_finished_job_can_be_neither_stopped_nor_cancelled(database):
    store.create_job("done", JobType.RECORD, {})
    store.finish_job("done", JobStatus.SUCCEEDED, result={})

    with pytest.raises(runner.JobNotStoppable):
        runner.stop("done")
    with pytest.raises(runner.JobNotStoppable):
        runner.cancel("done")


def test_reconcile_fails_a_job_whose_process_is_gone(database):
    """A restart must not leave a row claiming to be recording for good."""
    store.create_job("orphan", JobType.RECORD, {})
    # A pid that cannot be running: the kernel never hands this one out.
    store.mark_started("orphan", pid=0x7FFFFFFF)

    assert runner.reconcile() == ["orphan"]
    orphan = store.get_job("orphan")
    assert orphan.status is JobStatus.FAILED
    assert "is gone" in orphan.error


def test_reconcile_leaves_a_job_that_is_still_running_alone(database):
    """A job outlives the API deliberately; a reload must not end a meeting."""
    store.create_job("live", JobType.RECORD, {})
    store.mark_started("live", os.getpid(), runner.process_start_ticks(os.getpid()))

    assert runner.reconcile() == []
    assert store.get_job("live").status is JobStatus.RUNNING


def test_a_reused_pid_is_not_mistaken_for_the_job_that_had_it(database):
    """The reason a start time is recorded at all: this pid is alive, but it is
    no longer the process the job was running in."""
    store.create_job("stale", JobType.RECORD, {})
    store.mark_started("stale", os.getpid(), pid_start=1)  # never this process' own

    assert runner.reconcile() == ["stale"]
    assert store.get_job("stale").status is JobStatus.FAILED


def test_a_process_that_leads_no_group_of_its_own_is_never_signalled(database):
    """The guard that stops a cancel from taking the whole service down."""
    assert runner._is_own_group(os.getpid()) is (os.getpgid(os.getpid()) == os.getpid())
    # A child sharing our group is exactly the case that must be refused.
    if os.getpgid(os.getpid()) != os.getpid():
        assert runner._is_own_group(os.getpid()) is False


def test_the_stop_request_is_a_file_both_sides_agree_on(tmp_path):
    """The recording process is not a child of the request that asks it to stop,
    so the channel between them is the session directory."""
    assert stop_signal.stop_requested(str(tmp_path)) is False
    stop_signal.request_stop(str(tmp_path), reason="because")
    assert stop_signal.stop_requested(str(tmp_path)) is True
    stop_signal.clear_stop_request(str(tmp_path))
    assert stop_signal.stop_requested(str(tmp_path)) is False
