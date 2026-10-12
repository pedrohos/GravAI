"""The catalogue, and the two races it exists to survive.

A job process and the API touch the same SQLite file from different processes,
and a cancel arriving a moment after a job succeeded is ordinary rather than
exceptional. Both of the guards that make that safe fail silently when they
break - one by losing a result, the other by inventing speakers - so they are
checked here directly.
"""

from datetime import UTC, datetime, timedelta

from gravai.jobs import store
from gravai.jobs.models import JobStatus, JobType


def _job(job_id="job-1", job_type=JobType.RECORD, **params):
    return store.create_job(job_id, job_type, {"meeting_url": "https://meet.google.com/x", **params})


def test_a_job_round_trips(database):
    created = _job()
    assert created.status is JobStatus.QUEUED

    store.mark_started("job-1", pid=4242)
    started = store.get_job("job-1")
    assert started.status is JobStatus.RUNNING
    assert started.pid == 4242
    assert started.params["meeting_url"] == "https://meet.google.com/x"

    store.finish_job("job-1", JobStatus.SUCCEEDED, result={"recording_path": "/tmp/x"})
    finished = store.get_job("job-1")
    assert finished.status is JobStatus.SUCCEEDED
    assert finished.result == {"recording_path": "/tmp/x"}
    assert finished.finished_at is not None


def test_a_finished_job_is_not_reopened(database):
    """A cancel that lands after the job succeeded must not erase the result."""
    _job()
    store.mark_started("job-1", pid=1)
    store.finish_job("job-1", JobStatus.SUCCEEDED, result={"recording_path": "/tmp/x"})
    store.finish_job("job-1", JobStatus.CANCELLED, error="too late")

    job = store.get_job("job-1")
    assert job.status is JobStatus.SUCCEEDED
    assert job.result == {"recording_path": "/tmp/x"}


def test_only_a_running_job_moves_to_stopping(database):
    _job()
    store.mark_stop_requested("job-1")
    assert store.get_job("job-1").status is JobStatus.QUEUED

    store.mark_started("job-1", pid=1)
    store.mark_stop_requested("job-1")
    assert store.get_job("job-1").status is JobStatus.STOPPING


def test_active_jobs_are_the_unfinished_ones(database):
    _job("a")
    _job("b")
    _job("c")
    store.mark_started("b", pid=1)
    store.finish_job("c", JobStatus.FAILED, error="nope")

    assert {job.id for job in store.active_jobs()} == {"a", "b"}


def test_filters_and_ordering(database):
    _job("a", JobType.RECORD)
    _job("b", JobType.TRANSCRIBE)
    store.finish_job("a", JobStatus.SUCCEEDED)

    assert [job.id for job in store.list_jobs(job_type=JobType.TRANSCRIBE)] == ["b"]
    assert [job.id for job in store.list_jobs(status=JobStatus.SUCCEEDED)] == ["a"]


def test_a_recording_keeps_what_a_later_write_does_not_know(database):
    """The first write knows the URL; the second knows the timings. Both survive."""
    store.upsert_recording(
        "rec-1", "/tmp/rec-1", "recording",
        meeting_url="https://meet.google.com/x", provider="meet",
    )
    ended = datetime.now(UTC)
    store.upsert_recording(
        "rec-1", "/tmp/rec-1", "complete",
        started_at=ended - timedelta(minutes=30), ended_at=ended,
        main_track_path="/tmp/rec-1/track_mainAudio-1.wav",
    )

    recording = store.get_recording("rec-1")
    assert recording["meeting_url"] == "https://meet.google.com/x"
    assert recording["provider"] == "meet"
    assert recording["status"] == "complete"
    assert recording["main_track_path"].endswith("track_mainAudio-1.wav")


def test_re_slicing_replaces_speakers_rather_than_adding_to_them(database):
    """Slicing by element id and by name produce different speakers for one
    meeting; the second run's set is the meeting's, not the union."""
    store.upsert_recording("rec-1", "/tmp/rec-1", "complete")
    store.replace_participants("rec-1", [
        {"participant_id": "tile-1", "participant_name": "Ana", "segments": [{"start": 1, "end": 2}]},
        {"participant_id": "tile-2", "participant_name": "Ana", "segments": []},
    ])
    store.replace_participants("rec-1", [
        {"participant_id": "Ana", "participant_name": "Ana",
         "segments": [{"start": 1, "end": 2}], "transcript_text": "bom dia"},
    ])

    participants = store.get_recording("rec-1")["participants"]
    assert [p["participant_id"] for p in participants] == ["Ana"]
    assert participants[0]["transcript_text"] == "bom dia"
    assert participants[0]["segments"] == [{"start": 1, "end": 2}]


def test_a_recording_is_found_by_its_directory_however_it_is_spelled(database, tmp_path):
    """A transcribe job is handed a path, not an id, and paths have spellings."""
    directory = tmp_path / "session_tracks"
    directory.mkdir()
    store.upsert_recording("rec-1", str(directory), "complete", meeting_url="https://meet.google.com/x")

    found = store.get_recording_by_session_dir(f"{directory}/../{directory.name}")
    assert found is not None and found["id"] == "rec-1"


def test_forgetting_a_recording_takes_its_speakers_with_it(database):
    store.upsert_recording("rec-1", "/tmp/rec-1", "complete")
    store.replace_participants("rec-1", [{"participant_id": "Ana", "participant_name": "Ana"}])

    assert store.delete_recording("rec-1") is True
    assert store.get_recording("rec-1") is None
    assert store.delete_recording("rec-1") is False


def test_a_database_from_an_earlier_version_gains_the_columns_it_lacks(database):
    """CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so
    without this an upgrade turns every read of that table into a 500."""

    store.init_db()
    with store.connect() as conn:
        conn.execute("ALTER TABLE jobs DROP COLUMN pid_start")
    store._initialized_paths.clear()

    store.init_db()
    with store.connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    assert "pid_start" in columns

    _job()
    store.mark_started("job-1", 4242, pid_start=99)
    assert store.get_job("job-1").pid_start == 99
