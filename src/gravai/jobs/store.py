"""The SQLite catalogue of jobs and of what they recorded.

Two things live here and nothing else does. The **jobs** table is the queue and
its history: what was asked for, which process is running it, and how it ended.
The **recordings** table, with its **participants** rows, is the catalogue: one
meeting, when it ran, and per speaker the audio, the stretches they were talking
and what they said.

The audio and the transcripts themselves stay where the pipeline wrote them, in
SAVE_DIR, and are pointed at from here. What is copied into the database is the
part a caller wants to read rather than download - the segments and the text -
so that listing meetings and reading one does not mean opening dozens of files
per request.

Every connection is opened, used and closed inside the call that needs it, and
the database runs in WAL mode with a busy timeout: the writers are not threads
in one process but separate job processes, each holding a meeting for hours, and
a connection cached across that is a lock held across it too.
"""

import json
import os
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from gravai.config.logging_config import get_logger
from gravai.config.settings import get_settings
from gravai.jobs.models import Job, JobStatus, JobType

logger = get_logger("jobs.store")

# Long enough to outlast another process finishing a write, short enough that a
# genuinely stuck database is reported rather than waited on forever.
_BUSY_TIMEOUT_S = 30.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                  TEXT PRIMARY KEY,
    type                TEXT NOT NULL,
    status              TEXT NOT NULL,
    params              TEXT NOT NULL DEFAULT '{}',
    result              TEXT,
    error               TEXT,
    pid                 INTEGER,
    -- The pid alone does not identify a process across a restart: pids are
    -- reused, and every row read at startup was written by a process that is
    -- gone. This is /proc/<pid>/stat's start time, which together with the pid
    -- is unique for as long as the machine is up.
    pid_start           INTEGER,
    recording_id        TEXT,
    session_dir         TEXT,
    -- The job that must finish before this one may start. Not a foreign key:
    -- deleting a finished recording job should not take the transcription it
    -- led to with it, and by then the dependency is only provenance.
    depends_on          TEXT,
    created_at          TEXT NOT NULL,
    started_at          TEXT,
    finished_at         TEXT,
    stop_requested_at   TEXT
);

CREATE INDEX IF NOT EXISTS jobs_status_idx  ON jobs (status);
CREATE INDEX IF NOT EXISTS jobs_created_idx ON jobs (created_at DESC);

CREATE TABLE IF NOT EXISTS recordings (
    id                  TEXT PRIMARY KEY,
    meeting_url         TEXT,
    provider            TEXT,
    session_dir         TEXT NOT NULL UNIQUE,
    status              TEXT NOT NULL,
    started_at          TEXT,
    ended_at            TEXT,
    main_track_path     TEXT,
    meeting_transcript_text          TEXT,
    meeting_transcript_segments      TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS recordings_created_idx ON recordings (created_at DESC);

CREATE TABLE IF NOT EXISTS participants (
    recording_id        TEXT NOT NULL,
    participant_id      TEXT NOT NULL,
    participant_name    TEXT,
    track_path          TEXT,
    speech_track_path   TEXT,
    segments            TEXT NOT NULL DEFAULT '[]',
    transcript_text     TEXT,
    transcript_segments TEXT,
    PRIMARY KEY (recording_id, participant_id),
    FOREIGN KEY (recording_id) REFERENCES recordings (id) ON DELETE CASCADE
);
"""

#: Columns added to the schema after it first shipped.
#:
#: CREATE TABLE IF NOT EXISTS does nothing at all to a table that already
#: exists, so a database from an earlier version keeps the columns it was
#: created with and every read of that table then fails on the missing one. New
#: columns therefore have to be listed here as well as in _SCHEMA: the schema is
#: what a fresh database gets, and this is what an existing one is brought up to.
#: They must be nullable, since the rows already in the table have no value for
#: them.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "jobs": {"pid_start": "INTEGER", "depends_on": "TEXT"},
}

_init_lock = threading.Lock()
_initialized_paths: set[str] = set()


def database_path() -> str:
    return os.path.abspath(get_settings().DATABASE_PATH)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def init_db(path: str | None = None) -> str:
    """Creates the database and its schema if they are not there yet.

    Idempotent, and cheap enough to call from every connection: a job process is
    spawned fresh and has never seen the file, so the alternative is a startup
    hook that a job process does not run.
    """
    db_path = path or database_path()
    with _init_lock:
        if db_path in _initialized_paths:
            return db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=_BUSY_TIMEOUT_S)
        try:
            # WAL is what lets a reader (the API answering a poll) and a writer
            # (a job reporting progress) touch the file at the same time instead
            # of one of them getting SQLITE_BUSY.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(_SCHEMA)
            _add_missing_columns(conn)
            conn.commit()
        finally:
            conn.close()
        _initialized_paths.add(db_path)
    return db_path


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Brings a database created by an earlier version up to the current schema."""
    for table, columns in _ADDED_COLUMNS.items():
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, declaration in columns.items():
            if column in present:
                continue
            logger.info(f"Adding column {table}.{column} to an existing database")
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


@contextmanager
def connect(path: str | None = None) -> Iterator[sqlite3.Connection]:
    db_path = init_db(path)
    conn = sqlite3.connect(db_path, timeout=_BUSY_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _job_from_row(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        type=JobType(row["type"]),
        status=JobStatus(row["status"]),
        params=_loads(row["params"], {}),
        result=_loads(row["result"], None),
        error=row["error"],
        pid=row["pid"],
        pid_start=row["pid_start"],
        recording_id=row["recording_id"],
        session_dir=row["session_dir"],
        depends_on=row["depends_on"],
        created_at=_dt(row["created_at"]) or datetime.now(UTC),
        started_at=_dt(row["started_at"]),
        finished_at=_dt(row["finished_at"]),
        stop_requested_at=_dt(row["stop_requested_at"]),
    )


# --- jobs ------------------------------------------------------------------


def create_job(
    job_id: str,
    job_type: JobType,
    params: dict,
    depends_on: str | None = None,
    status: JobStatus = JobStatus.QUEUED,
) -> Job:
    with connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, type, status, params, depends_on, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                job_id,
                job_type.value,
                status.value,
                json.dumps(params),
                depends_on,
                _now(),
            ),
        )
    job = get_job(job_id)
    assert job is not None  # just inserted, inside the same database
    return job


def get_job(job_id: str) -> Job | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _job_from_row(row) if row else None


def list_jobs(
    status: JobStatus | None = None,
    job_type: JobType | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Job]:
    clauses, args = [], []
    if status is not None:
        clauses.append("status = ?")
        args.append(status.value)
    if job_type is not None:
        clauses.append("type = ?")
        args.append(job_type.value)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM jobs{where} ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()
    return [_job_from_row(row) for row in rows]


def active_jobs() -> list[Job]:
    """Every job that has not reached an end, in the order they were asked for.

    Oldest first, unlike list_jobs: the callers are the ones that walk all of
    them - reconciling after a restart, or refusing a shutdown - not ones showing
    a page.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status IN (?, ?, ?, ?) ORDER BY created_at",
            (
                JobStatus.QUEUED.value,
                JobStatus.WAITING.value,
                JobStatus.RUNNING.value,
                JobStatus.STOPPING.value,
            ),
        ).fetchall()
    return [_job_from_row(row) for row in rows]


def dependents_of(job_id: str) -> list[Job]:
    """The jobs waiting for this one, oldest first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE depends_on = ? ORDER BY created_at", (job_id,)
        ).fetchall()
    return [_job_from_row(row) for row in rows]


def update_job(job_id: str, **fields: Any) -> None:
    """Writes the given columns of a job, JSON-encoding params and result."""
    if not fields:
        return
    encoded: dict[str, Any] = {}
    for key, value in fields.items():
        if key in ("params", "result") and value is not None and not isinstance(value, str):
            encoded[key] = json.dumps(value, default=str)
        elif isinstance(value, JobStatus) or isinstance(value, JobType):
            encoded[key] = value.value
        elif isinstance(value, datetime):
            encoded[key] = value.isoformat()
        else:
            encoded[key] = value

    assignments = ", ".join(f"{key} = ?" for key in encoded)
    with connect() as conn:
        conn.execute(
            f"UPDATE jobs SET {assignments} WHERE id = ?", (*encoded.values(), job_id)
        )


def finish_job(
    job_id: str,
    status: JobStatus,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    """Closes a job out, unless something already did.

    The WHERE clause is what makes a cancel and a job finishing on its own safe
    to race: a job that was killed a moment after it succeeded keeps the success
    it actually had, and one that finished after being marked cancelled does not
    overwrite that.
    """
    with connect() as conn:
        conn.execute(
            """
            UPDATE jobs
               SET status = ?, result = ?, error = ?, finished_at = ?
             WHERE id = ? AND status NOT IN (?, ?, ?)
            """,
            (
                status.value,
                json.dumps(result, default=str) if result is not None else None,
                error,
                _now(),
                job_id,
                JobStatus.SUCCEEDED.value,
                JobStatus.FAILED.value,
                JobStatus.CANCELLED.value,
            ),
        )


def mark_started(job_id: str, pid: int, pid_start: int | None = None) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE jobs SET status = ?, pid = ?, pid_start = ?, started_at = ?
             WHERE id = ? AND status = ?
            """,
            (JobStatus.RUNNING.value, pid, pid_start, _now(), job_id, JobStatus.QUEUED.value),
        )


def mark_stop_requested(job_id: str) -> None:
    """Moves a running job into STOPPING; a job past that point is left alone."""
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = ?, stop_requested_at = ? WHERE id = ? AND status = ?",
            (JobStatus.STOPPING.value, _now(), job_id, JobStatus.RUNNING.value),
        )


def attach_session(job_id: str, recording_id: str, session_dir: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET recording_id = ?, session_dir = ? WHERE id = ?",
            (recording_id, session_dir, job_id),
        )


def delete_job(job_id: str) -> bool:
    with connect() as conn:
        cursor = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    return cursor.rowcount > 0


# --- recordings ------------------------------------------------------------


def upsert_recording(
    recording_id: str,
    session_dir: str,
    status: str,
    meeting_url: str | None = None,
    provider: str | None = None,
    started_at: datetime | str | None = None,
    ended_at: datetime | str | None = None,
    main_track_path: str | None = None,
    meeting_transcript_text: str | None = None,
    meeting_transcript_segments: Any = None,
) -> None:
    """Creates or updates one meeting's row, leaving unknown fields as they were.

    A recording is written twice: once the moment the recorder has a directory,
    which is what makes a meeting in progress visible at all, and again when the
    pipeline is done with it. The second write knows things the first could not,
    and the first knows the meeting URL, which nothing downstream carries - so
    each column keeps its old value when the new one is None rather than being
    blanked by whichever write came last.
    """
    now = _now()
    # Normalized on the way in, because session_dir is the key a transcription
    # job looks a recording up by and the pipeline builds it from SAVE_DIR, which
    # is free to be relative. Two spellings of one directory would otherwise be
    # two meetings.
    session_dir = os.path.abspath(session_dir)
    values = {
        "meeting_url": meeting_url,
        "provider": provider,
        "started_at": started_at.isoformat() if isinstance(started_at, datetime) else started_at,
        "ended_at": ended_at.isoformat() if isinstance(ended_at, datetime) else ended_at,
        "main_track_path": main_track_path,
        "meeting_transcript_text": meeting_transcript_text,
        "meeting_transcript_segments": (
            json.dumps(meeting_transcript_segments, default=str)
            if meeting_transcript_segments is not None
            else None
        ),
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO recordings (id, session_dir, status, created_at, updated_at)
                 VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET status = excluded.status,
                                          updated_at = excluded.updated_at
            """,
            (recording_id, session_dir, status, now, now),
        )
        for column, value in values.items():
            if value is None:
                continue
            conn.execute(
                f"UPDATE recordings SET {column} = ?, updated_at = ? WHERE id = ?",
                (value, now, recording_id),
            )


def replace_participants(recording_id: str, participants: Iterable[dict]) -> None:
    """Rewrites a recording's speakers wholesale.

    Re-slicing the same directory can produce a different set of them - grouping
    by name rather than by element id merges two tiles of one person into one
    speaker - so the new set replaces the old rather than being merged into it,
    which would leave the previous grouping behind as phantom speakers.
    """
    with connect() as conn:
        conn.execute("DELETE FROM participants WHERE recording_id = ?", (recording_id,))
        conn.executemany(
            """
            INSERT INTO participants (
                recording_id, participant_id, participant_name, track_path,
                speech_track_path, segments, transcript_text, transcript_segments
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    recording_id,
                    participant["participant_id"],
                    participant.get("participant_name"),
                    participant.get("track_path"),
                    participant.get("speech_track_path"),
                    json.dumps(participant.get("segments") or [], default=str),
                    participant.get("transcript_text"),
                    json.dumps(participant["transcript_segments"], default=str)
                    if participant.get("transcript_segments") is not None
                    else None,
                )
                for participant in participants
            ],
        )


def _recording_from_row(row: sqlite3.Row, participants: list[dict]) -> dict:
    return {
        "id": row["id"],
        "meeting_url": row["meeting_url"],
        "provider": row["provider"],
        "session_dir": row["session_dir"],
        "status": row["status"],
        "started_at": _dt(row["started_at"]),
        "ended_at": _dt(row["ended_at"]),
        "main_track_path": row["main_track_path"],
        "meeting_transcript_text": row["meeting_transcript_text"],
        "meeting_transcript_segments": _loads(row["meeting_transcript_segments"], None),
        "created_at": _dt(row["created_at"]),
        "updated_at": _dt(row["updated_at"]),
        "participants": participants,
    }


def _participant_from_row(row: sqlite3.Row) -> dict:
    return {
        "participant_id": row["participant_id"],
        "participant_name": row["participant_name"],
        "track_path": row["track_path"],
        "speech_track_path": row["speech_track_path"],
        "segments": _loads(row["segments"], []),
        "transcript_text": row["transcript_text"],
        "transcript_segments": _loads(row["transcript_segments"], None),
    }


def list_recordings(limit: int = 100, offset: int = 0) -> list[dict]:
    """Every meeting, newest first, each with its speakers.

    Speakers come along because the only reason to list meetings is to choose
    one, and who was in it is what that choice is made on. They are fetched in
    one query rather than one per recording, so a long list stays a constant
    number of round trips.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM recordings ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        if not rows:
            return []
        ids = [row["id"] for row in rows]
        placeholders = ",".join("?" for _ in ids)
        participant_rows = conn.execute(
            f"SELECT * FROM participants WHERE recording_id IN ({placeholders}) "
            f"ORDER BY participant_name, participant_id",
            ids,
        ).fetchall()

    by_recording: dict[str, list[dict]] = {recording_id: [] for recording_id in ids}
    for row in participant_rows:
        by_recording[row["recording_id"]].append(_participant_from_row(row))
    return [_recording_from_row(row, by_recording[row["id"]]) for row in rows]


def get_recording(recording_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
        if row is None:
            return None
        participant_rows = conn.execute(
            "SELECT * FROM participants WHERE recording_id = ? "
            "ORDER BY participant_name, participant_id",
            (recording_id,),
        ).fetchall()
    return _recording_from_row(row, [_participant_from_row(r) for r in participant_rows])


def get_recording_by_session_dir(session_dir: str) -> dict | None:
    """The meeting recorded into a directory, when one is already catalogued.

    A transcription job is given a directory and nothing else, so this is how it
    finds the meeting URL and the provider that produced it - neither of which is
    recoverable from the files - and files its results under the same recording
    instead of a second one.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM recordings WHERE session_dir = ?", (os.path.abspath(session_dir),)
        ).fetchone()
    return get_recording(row["id"]) if row else None


def jobs_for_recording(recording_id: str) -> list[Job]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE recording_id = ? ORDER BY created_at",
            (recording_id,),
        ).fetchall()
    return [_job_from_row(row) for row in rows]


def delete_recording(recording_id: str) -> bool:
    """Forgets a meeting. Its audio on disk is not touched.

    Deleting the files is deliberately not part of this: a row is cheap to
    recreate by re-slicing the directory, and hours of meeting audio removed by
    a catalogue call is not something to do as a side effect.
    """
    with connect() as conn:
        cursor = conn.execute("DELETE FROM recordings WHERE id = ?", (recording_id,))
    return cursor.rowcount > 0
