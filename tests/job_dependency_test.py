"""A record-and-transcribe is two jobs, and the second waits for the first.

They fail for unrelated reasons - a meeting that refuses the bot has nothing to
do with whisper being down - and as one job either failure reported the whole
meeting as lost, with no way to retry the half that broke. Split, the recording
stands on its own and the transcription can be run again over the audio it
already left behind.

No test here submits a recording that would actually run: a RECORD job spawns a
browser and sits in a meeting, which is not something a unit test should start.
The recording half is either stubbed out of the process spawn entirely or
written straight into the database as a job that has already finished, which is
all the transcription waiting on it ever reads.
"""

import os
import time

import pytest

from gravai.jobs import runner, store
from gravai.jobs.models import Job, JobStatus, JobSubmission, JobType

MEETING_URL = "https://teams.microsoft.com/l/meetup-join/whatever"


def _wait_for(job_id, predicate, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = store.get_job(job_id)
        if job and predicate(job):
            return job
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} stayed at {store.get_job(job_id).status}")


class _FakeProcess:
    """Records what would have been spawned, and never spawns it.

    Carries this process's own pid so that a row written from it reads as a job
    whose process is alive - which is what reconcile checks, and what would
    otherwise fail every job this fixture creates.
    """

    def __init__(self, target=None, args=(), name=None):
        self.target, self.args, self.name = target, args, name
        self.pid = os.getpid()

    def start(self) -> None:
        pass

    def is_alive(self) -> bool:
        return True


class _FakeMp:
    class _Context:
        Process = _FakeProcess

    @staticmethod
    def get_context(_kind):
        return _FakeMp._Context


@pytest.fixture
def no_spawning(monkeypatch):
    """submit() without the processes, for the tests that are about the rows."""
    monkeypatch.setattr(runner, "mp", _FakeMp)
    monkeypatch.setattr(runner, "_children", {})


def _pair() -> tuple[Job, Job]:
    jobs = store.list_jobs()
    return (
        next(j for j in jobs if j.type is JobType.RECORD),
        next(j for j in jobs if j.type is JobType.TRANSCRIBE),
    )


# --- what a submission becomes ---------------------------------------------


def test_a_record_and_transcribe_becomes_two_jobs(database, no_spawning):
    returned = runner.submit(
        JobSubmission(type=JobType.RECORD_AND_TRANSCRIBE, meeting_url=MEETING_URL)
    )

    assert len(store.list_jobs()) == 2
    record, transcribe = _pair()
    # The recording is what comes back: it is the head of the chain, and it is
    # the one a caller has to be able to stop.
    assert returned.id == record.id
    assert record.depends_on is None
    assert transcribe.depends_on == record.id
    assert record.status is JobStatus.QUEUED
    assert transcribe.status is JobStatus.WAITING


def test_the_recording_half_does_not_slice_what_the_transcription_will(database, no_spawning):
    """Slicing runs again inside the transcription, so doing it twice is waste."""
    runner.submit(JobSubmission(type=JobType.RECORD_AND_TRANSCRIBE, meeting_url=MEETING_URL))
    record, transcribe = _pair()

    assert record.params["meeting_url"] == MEETING_URL
    assert record.params["slice_tracks"] is False
    # The transcription carries no directory, because there is none to carry
    # until the recorder has named one on its way into the meeting.
    assert "tracks_output_dir" not in transcribe.params
    assert transcribe.params["group_slices_by_name"] is True


def test_a_plain_record_job_is_still_one_job(database, no_spawning):
    returned = runner.submit(JobSubmission(type=JobType.RECORD, meeting_url=MEETING_URL))

    jobs = store.list_jobs()
    assert [job.type for job in jobs] == [JobType.RECORD]
    assert jobs[0].depends_on is None
    assert store.dependents_of(returned.id) == []


def test_a_plain_transcribe_job_is_still_one_job(database, no_spawning, tmp_path):
    runner.submit(JobSubmission(type=JobType.TRANSCRIBE, tracks_output_dir=str(tmp_path)))

    jobs = store.list_jobs()
    assert [job.type for job in jobs] == [JobType.TRANSCRIBE]
    assert jobs[0].depends_on is None
    assert jobs[0].status is JobStatus.QUEUED


# --- the wait itself --------------------------------------------------------


def _recording_row(job_id: str, status: JobStatus, **fields) -> Job:
    store.create_job(job_id, JobType.RECORD, {"meeting_url": MEETING_URL})
    if fields:
        store.update_job(job_id, **fields)
    if status.is_terminal:
        store.finish_job(job_id, status, result=fields.get("result"))
    else:
        store.update_job(job_id, status=status)
    return store.get_job(job_id)


def _waiting_transcription(depends_on: str) -> Job:
    """A real transcription process, waiting on a recording that is only a row."""
    return runner._start(
        JobType.TRANSCRIBE,
        {"group_slices_by_name": True},
        depends_on=depends_on,
        status=JobStatus.WAITING,
    )


def test_it_waits_while_the_recording_is_still_going(database):
    _recording_row("rec", JobStatus.RUNNING)
    transcribe = _waiting_transcription("rec")

    waiting = _wait_for(transcribe.id, lambda j: j.status is JobStatus.WAITING)
    # A live process of its own throughout, which is what makes it cancellable
    # and what stops a restart's reconcile from failing it.
    assert waiting.pid and runner._process_alive(waiting.pid, waiting.pid_start)
    assert waiting.started_at is None

    time.sleep(2)
    assert store.get_job(transcribe.id).status is JobStatus.WAITING

    runner.cancel(transcribe.id)


def test_it_starts_once_the_recording_succeeds(database, tmp_path):
    """The directory is read from the finished recording, since nobody knew it
    when the transcription was created."""
    _recording_row("rec", JobStatus.RUNNING)
    transcribe = _waiting_transcription("rec")
    _wait_for(transcribe.id, lambda j: j.status is JobStatus.WAITING)

    store.update_job("rec", session_dir=str(tmp_path))
    store.finish_job("rec", JobStatus.SUCCEEDED, result={"recording_path": str(tmp_path)})

    # An empty directory, so it fails the instant it reads one - which is all
    # this needs to show that it started and looked in the right place.
    ran = _wait_for(transcribe.id, lambda j: j.status.is_terminal)
    assert ran.status is JobStatus.FAILED
    assert "Missing main audio track" in ran.error, ran.error


def test_it_is_given_up_when_the_recording_does_not_finish(database):
    """No directory to read, so waiting longer would only end worse and later."""
    _recording_row("rec", JobStatus.CANCELLED)
    transcribe = _waiting_transcription("rec")

    given_up = _wait_for(transcribe.id, lambda j: j.status.is_terminal)
    assert given_up.status is JobStatus.CANCELLED
    assert "rec" in given_up.error
    assert "nothing to transcribe" in given_up.error


def test_it_is_given_up_when_the_recording_failed(database):
    _recording_row("rec", JobStatus.FAILED)
    transcribe = _waiting_transcription("rec")

    given_up = _wait_for(transcribe.id, lambda j: j.status.is_terminal)
    assert given_up.status is JobStatus.CANCELLED
    assert "failed" in given_up.error


def test_a_waiting_transcription_can_be_cancelled_on_its_own(database):
    _recording_row("rec", JobStatus.RUNNING)
    transcribe = _waiting_transcription("rec")
    _wait_for(transcribe.id, lambda j: j.status is JobStatus.WAITING)

    cancelled = runner.cancel(transcribe.id)
    assert cancelled.status is JobStatus.CANCELLED
    # Dropping the transcription is not a reason to walk out of the meeting.
    assert not store.get_job("rec").status.is_terminal


def test_a_waiting_transcription_is_still_refused_a_gentle_stop(database):
    _recording_row("rec", JobStatus.RUNNING)
    transcribe = _waiting_transcription("rec")
    _wait_for(transcribe.id, lambda j: j.status is JobStatus.WAITING)

    with pytest.raises(runner.JobNotStoppable) as caught:
        runner.stop(transcribe.id)
    assert "Cancel it instead" in str(caught.value)

    runner.cancel(transcribe.id)


def test_reconcile_leaves_a_waiting_job_with_a_live_process_alone(database):
    """Waiting is active but not running, and its process is real - a restart
    must not mistake it for one whose process died."""
    _recording_row("rec", JobStatus.RUNNING)
    transcribe = _waiting_transcription("rec")
    _wait_for(transcribe.id, lambda j: j.status is JobStatus.WAITING)

    assert transcribe.id not in runner.reconcile()
    assert store.get_job(transcribe.id).status is JobStatus.WAITING

    runner.cancel(transcribe.id)


# --- resolving the directory ------------------------------------------------


def test_the_directory_comes_from_the_recording_that_ran(database, tmp_path):
    _recording_row("rec", JobStatus.SUCCEEDED, session_dir=str(tmp_path))
    job = store.create_job("t", JobType.TRANSCRIBE, {}, depends_on="rec")

    assert runner._recorded_directory(job) == str(tmp_path)


def test_the_directory_falls_back_to_the_recordings_result(database, tmp_path):
    """session_dir is written by the recorder's own hook; a job that somehow got
    to a result without it still says where it wrote."""
    store.create_job("rec", JobType.RECORD, {})
    store.finish_job("rec", JobStatus.SUCCEEDED, result={"recording_path": str(tmp_path)})
    job = store.create_job("t", JobType.TRANSCRIBE, {}, depends_on="rec")

    assert runner._recorded_directory(job) == str(tmp_path)


def test_a_recording_that_left_no_directory_is_refused_clearly(database):
    store.create_job("rec", JobType.RECORD, {})
    store.finish_job("rec", JobStatus.SUCCEEDED, result={})
    job = store.create_job("t", JobType.TRANSCRIBE, {}, depends_on="rec")

    with pytest.raises(runner.JobError) as caught:
        runner._recorded_directory(job)
    assert "nothing for this transcription to read" in str(caught.value)
