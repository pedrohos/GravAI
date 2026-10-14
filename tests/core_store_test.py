"""Core preserves legacy SQLite files and the store's concurrency guarantees."""

import json
import multiprocessing as mp
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier

import pytest
from sqlalchemy import event

from gravai.jobs import store
from gravai.models.database import (
    JobParamsDTO,
    JobResultDTO,
    JobStatus,
    JobType,
    ParticipantDTO,
    RecordingUpsertDTO,
)

# Deliberately created by sqlite3, without Core types or the two added job columns.
_LEGACY_SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, type TEXT NOT NULL, status TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}', result TEXT, error TEXT, pid INTEGER,
    recording_id TEXT, session_dir TEXT, created_at TEXT NOT NULL,
    started_at TEXT, finished_at TEXT, stop_requested_at TEXT
);
CREATE TABLE recordings (
    id TEXT PRIMARY KEY, meeting_url TEXT, provider TEXT,
    session_dir TEXT NOT NULL UNIQUE, status TEXT NOT NULL, started_at TEXT,
    ended_at TEXT, main_track_path TEXT, meeting_transcript_text TEXT,
    meeting_transcript_segments TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE participants (
    recording_id TEXT NOT NULL, participant_id TEXT NOT NULL, participant_name TEXT,
    track_path TEXT, speech_track_path TEXT, segments TEXT NOT NULL DEFAULT '[]',
    transcript_text TEXT, transcript_segments TEXT,
    PRIMARY KEY (recording_id, participant_id),
    FOREIGN KEY (recording_id) REFERENCES recordings(id) ON DELETE CASCADE
);
"""


def _create_legacy(path: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(_LEGACY_SCHEMA)


def _initialize_legacy(path: str, gate) -> None:
    gate.wait(timeout=20)
    store.init_db(path)
    store.dispose_engines()


def test_core_reads_and_updates_an_existing_sqlite_catalogue(database, tmp_path):
    _create_legacy(database)
    timestamp = "2026-01-01T12:00:00+02:00"
    segment = {"start": 1.0, "end": 2.0, "text": "example", "tokens": [42]}
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO jobs (id, type, status, params, result, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "legacy-job",
                "record",
                "succeeded",
                json.dumps({"slice_tracks": False}),
                json.dumps({"recording_path": str(tmp_path)}),
                timestamp,
            ),
        )
        connection.execute(
            "INSERT INTO recordings (id, session_dir, status, started_at, main_track_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy",
                str(tmp_path),
                "complete",
                timestamp,
                "/tmp/track_mainAudio.wav",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            "INSERT INTO participants (recording_id, participant_id, segments, transcript_segments) VALUES (?, ?, ?, ?)",
            ("legacy", "participant", '[{"start":1,"end":2}]', json.dumps([segment])),
        )
    job = store.get_job("legacy-job")
    assert job is not None and job.params.slice_tracks is False
    assert job.result is not None and job.result.recording_path == str(tmp_path)
    assert job.pid_start is None and job.depends_on is None
    recording = store.get_recording("legacy")
    assert recording is not None
    assert recording.started_at == datetime.fromisoformat(timestamp)
    assert recording.participants[0].transcript_segments[0].text == "example"
    store.upsert_recording(
        RecordingUpsertDTO(
            recording_id="legacy", session_dir=str(tmp_path), status="processing"
        )
    )
    found = store.get_recording("legacy")
    assert (
        found.status == "processing"
        and found.main_track_path == recording.main_track_path
    )
    assert (
        found.created_at == recording.created_at
        and found.started_at == recording.started_at
    )
    with store.connect() as connection:
        columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(jobs)")
        }
    assert {"pid_start", "depends_on"} <= columns
    with store.connect() as connection:
        indexes = {
            row[1] for row in connection.exec_driver_sql("PRAGMA index_list(jobs)")
        }
    assert {"jobs_status_idx", "jobs_created_idx"} <= indexes


def test_workers_can_initialize_the_same_legacy_database(database):
    _create_legacy(database)
    context = mp.get_context("spawn")
    gate = context.Barrier(2)
    processes = [
        context.Process(target=_initialize_legacy, args=(database, gate))
        for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=30)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
    store.create_job("migrated", JobType.RECORD, JobParamsDTO())
    assert store.get_job("migrated") is not None


def test_completion_and_cancellation_race_preserves_the_first_terminal_result(database):
    store.create_job("race", JobType.RECORD, JobParamsDTO())
    gate = Barrier(2)

    def finish(status):
        gate.wait(timeout=10)
        store.finish_job(
            "race",
            status,
            result=JobResultDTO(recording_path="/tmp/audio")
            if status is JobStatus.SUCCEEDED
            else None,
            error="cancelled" if status is JobStatus.CANCELLED else None,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(finish, status)
            for status in (JobStatus.SUCCEEDED, JobStatus.CANCELLED)
        ]
        for future in futures:
            future.result(timeout=15)
    job = store.get_job("race")
    assert job is not None
    if job.status is JobStatus.SUCCEEDED:
        assert (
            job.result == JobResultDTO(recording_path="/tmp/audio")
            and job.error is None
        )
    else:
        assert (
            job.status is JobStatus.CANCELLED
            and job.result is None
            and job.error == "cancelled"
        )


def test_every_connection_has_wal_foreign_keys_and_timeout(database):
    for _ in range(2):
        with store.connect() as connection:
            assert (
                connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
            )
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert (
                connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 30000
            )


def test_core_closes_physical_connections_after_each_operation(database):
    with store.connect() as connection:
        raw = connection.connection.dbapi_connection
        assert raw is not None
        raw.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        raw.execute("SELECT 1")


def test_recording_list_keeps_participant_reads_batched(database, tmp_path):
    for number in range(3):
        recording_id = f"recording-{number}"
        store.upsert_recording(
            RecordingUpsertDTO(
                recording_id=recording_id,
                session_dir=str(tmp_path / recording_id),
                status="complete",
            )
        )
        store.replace_participants(
            recording_id, [ParticipantDTO(participant_id=f"participant-{number}")]
        )
    with store.connect() as connection:
        engine = connection.engine
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        recordings = store.list_recordings()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(recordings) == 3 and all(
        len(recording.participants) == 1 for recording in recordings
    )
    assert len(statements) == 2


def test_core_database_urls_preserve_special_characters_in_paths(tmp_path):
    database = tmp_path / "catalogue ?# with spaces.sqlite"
    with store.connect(str(database)) as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM jobs").scalar_one() == 0
    assert database.is_file()
    store.dispose_engines()


def test_replacing_participants_with_an_empty_set_clears_them(database, tmp_path):
    store.upsert_recording(
        RecordingUpsertDTO(
            recording_id="empty", session_dir=str(tmp_path), status="complete"
        )
    )
    store.replace_participants("empty", [ParticipantDTO(participant_id="participant")])
    store.replace_participants("empty", [])
    assert store.get_recording("empty").participants == []
