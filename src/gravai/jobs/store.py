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

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from sqlite3 import SQLITE_BUSY, SQLITE_LOCKED
from sqlite3 import Connection as SQLiteConnection
from sqlite3 import Cursor as SQLiteCursor
from sqlite3 import OperationalError as SQLiteOperationalError

from sqlalchemy import (
    create_engine,
    delete,
    event,
    insert,
    inspect,
    literal_column,
    select,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import URL, Connection, Engine, RowMapping
from sqlalchemy.pool import ConnectionPoolEntry, NullPool

from gravai.config.logging_config import get_logger
from gravai.config.settings import get_settings
from gravai.jobs.tables import jobs, metadata, recordings
from gravai.jobs.tables import participants as participants_table
from gravai.models.database import (
    Job,
    JobParamsDTO,
    JobResultDTO,
    JobStatus,
    JobType,
    JobUpdateDTO,
    ParticipantDTO,
    RecordingDTO,
    RecordingUpsertDTO,
    TranscriptSegmentDTO,
)

logger = get_logger("jobs.store")
_BUSY_TIMEOUT_S = 30.0
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "jobs": {"pid_start": "INTEGER", "depends_on": "TEXT"},
}

_init_lock = threading.Lock()
_initialized_paths: set[str] = set()
# Only engines and compiled statements are cached. NullPool closes every DBAPI
# connection on release, and the pid key prevents an inherited engine being used.
_engines: dict[tuple[int, str], Engine] = {}
_JOB_ROWID = literal_column("jobs.rowid")
_RECORDING_ROWID = literal_column("recordings.rowid")


def database_path() -> str:
    return os.path.abspath(get_settings().DATABASE_PATH)


def _now() -> datetime:
    return datetime.now(UTC)


def _enable_wal(cursor: SQLiteCursor) -> None:
    # Switching journal modes can return BUSY immediately despite busy_timeout
    # when another process is initializing the same file. Retry only lock errors,
    # outside any transaction, within the same configured timeout budget.
    deadline = time.monotonic() + _BUSY_TIMEOUT_S
    while True:
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            return
        except SQLiteOperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", None)
            if code is None or code & 0xFF not in (SQLITE_BUSY, SQLITE_LOCKED):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(0.05, remaining))


def _configure_connection(
    dbapi_connection: SQLiteConnection, _record: ConnectionPoolEntry
) -> None:
    # Configure before a transaction starts. Explicit BEGIN also makes reads
    # and schema changes transactional rather than using sqlite3 legacy mode.
    dbapi_connection.isolation_level = None
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        _enable_wal(cursor)
    finally:
        cursor.close()


def _begin_transaction(connection: Connection) -> None:
    command = (
        "BEGIN IMMEDIATE"
        if connection.get_execution_options().get("sqlite_immediate")
        else "BEGIN"
    )
    connection.exec_driver_sql(command)


def _engine_for(path: str) -> Engine:
    """Called with _init_lock held; each process owns its engine."""
    key = (os.getpid(), path)
    engine = _engines.get(key)
    if engine is None:
        engine = create_engine(
            URL.create("sqlite+pysqlite", database=path),
            connect_args={"timeout": _BUSY_TIMEOUT_S},
            poolclass=NullPool,
        )
        event.listen(engine, "connect", _configure_connection)
        event.listen(engine, "begin", _begin_transaction)
        _engines[key] = engine
    return engine


def dispose_engines() -> None:
    """Release cached engine resources when the API or a test shuts down."""
    with _init_lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()
        _initialized_paths.clear()


def init_db(path: str | None = None) -> str:
    """Create the Core schema and upgrade nullable legacy columns in one transaction."""
    db_path = os.path.abspath(path) if path is not None else database_path()
    with _init_lock:
        engine = _engine_for(db_path)
        if db_path in _initialized_paths:
            return db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        # Serialize initialization across worker processes before inspecting
        # existing tables, so migrations never race to add the same column.
        with engine.connect().execution_options(sqlite_immediate=True) as connection:
            with connection.begin():
                metadata.create_all(connection)
                _add_missing_columns(connection)
                # create_all skips indexes on tables that already exist.
                for table in metadata.sorted_tables:
                    for index in table.indexes:
                        index.create(connection, checkfirst=True)
        _initialized_paths.add(db_path)
    return db_path


def _add_missing_columns(connection: Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        present = {column["name"] for column in inspect(connection).get_columns(table)}
        quote = connection.dialect.identifier_preparer.quote
        for column, declaration in columns.items():
            if column in present:
                continue
            logger.info(f"Adding column {table}.{column} to an existing database")
            connection.exec_driver_sql(
                f"ALTER TABLE {quote(table)} ADD COLUMN {quote(column)} {declaration}"
            )


@contextmanager
def connect(path: str | None = None) -> Iterator[Connection]:
    db_path = init_db(path)
    with _init_lock:
        engine = _engine_for(db_path)
    with engine.begin() as connection:
        yield connection


def _job_from_row(row: RowMapping) -> Job:
    return Job.model_validate({**row, "created_at": row["created_at"] or _now()})


def _recording_from_row(
    row: RowMapping, speakers: list[ParticipantDTO]
) -> RecordingDTO:
    return RecordingDTO.model_validate({**row, "participants": speakers})


def _participant_from_row(row: RowMapping) -> ParticipantDTO:
    return ParticipantDTO.model_validate(
        {key: value for key, value in row.items() if key != "recording_id"}
    )


def create_job(
    job_id: str,
    job_type: JobType,
    params: JobParamsDTO,
    depends_on: str | None = None,
    status: JobStatus = JobStatus.QUEUED,
) -> Job:
    with connect() as connection:
        connection.execute(
            insert(jobs).values(
                id=job_id,
                type=job_type.value,
                status=status.value,
                params=params,
                depends_on=depends_on,
                created_at=_now(),
            )
        )
    job = get_job(job_id)
    assert job is not None
    return job


def get_job(job_id: str) -> Job | None:
    with connect() as connection:
        row = (
            connection.execute(select(jobs).where(jobs.c.id == job_id))
            .mappings()
            .first()
        )
    return _job_from_row(row) if row is not None else None


def list_jobs(
    status: JobStatus | None = None,
    job_type: JobType | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Job]:
    statement = (
        select(jobs)
        .order_by(jobs.c.created_at.desc(), _JOB_ROWID.desc())
        .limit(limit)
        .offset(offset)
    )
    if status is not None:
        statement = statement.where(jobs.c.status == status.value)
    if job_type is not None:
        statement = statement.where(jobs.c.type == job_type.value)
    with connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_job_from_row(row) for row in rows]


def active_jobs() -> list[Job]:
    """Every unfinished job, oldest first."""
    statement = (
        select(jobs)
        .where(
            jobs.c.status.in_(
                [
                    JobStatus.QUEUED.value,
                    JobStatus.WAITING.value,
                    JobStatus.RUNNING.value,
                    JobStatus.STOPPING.value,
                ]
            )
        )
        .order_by(jobs.c.created_at)
    )
    with connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_job_from_row(row) for row in rows]


def dependents_of(job_id: str) -> list[Job]:
    with connect() as connection:
        rows = (
            connection.execute(
                select(jobs)
                .where(jobs.c.depends_on == job_id)
                .order_by(jobs.c.created_at)
            )
            .mappings()
            .all()
        )
    return [_job_from_row(row) for row in rows]


def update_job(job_id: str, update: JobUpdateDTO) -> None:
    """Only supplied fields are changed, including explicit nullable clears."""
    values = update.model_dump(mode="python", exclude_unset=True)
    if not values:
        return
    with connect() as connection:
        connection.execute(jobs.update().where(jobs.c.id == job_id).values(values))


def finish_job(
    job_id: str,
    status: JobStatus,
    result: JobResultDTO | None = None,
    error: str | None = None,
) -> None:
    # Keep the conditional UPDATE atomic: cancelling cannot overwrite success,
    # and a worker finishing after cancellation cannot reopen its job.
    statement = (
        update(jobs)
        .where(
            jobs.c.id == job_id,
            jobs.c.status.not_in(
                [
                    JobStatus.SUCCEEDED.value,
                    JobStatus.FAILED.value,
                    JobStatus.CANCELLED.value,
                ]
            ),
        )
        .values(status=status.value, result=result, error=error, finished_at=_now())
    )
    with connect() as connection:
        connection.execute(statement)


def mark_started(job_id: str, pid: int, pid_start: int | None = None) -> None:
    with connect() as connection:
        connection.execute(
            update(jobs)
            .where(
                jobs.c.id == job_id,
                jobs.c.status == JobStatus.QUEUED.value,
            )
            .values(
                status=JobStatus.RUNNING.value,
                pid=pid,
                pid_start=pid_start,
                started_at=_now(),
            )
        )


def mark_stop_requested(job_id: str) -> None:
    with connect() as connection:
        connection.execute(
            update(jobs)
            .where(
                jobs.c.id == job_id,
                jobs.c.status == JobStatus.RUNNING.value,
            )
            .values(status=JobStatus.STOPPING.value, stop_requested_at=_now())
        )


def attach_session(job_id: str, recording_id: str, session_dir: str) -> None:
    with connect() as connection:
        connection.execute(
            update(jobs)
            .where(jobs.c.id == job_id)
            .values(
                recording_id=recording_id,
                session_dir=session_dir,
            )
        )


def delete_job(job_id: str) -> bool:
    with connect() as connection:
        result = connection.execute(delete(jobs).where(jobs.c.id == job_id))
    return result.rowcount > 0


def upsert_recording(recording: RecordingUpsertDTO) -> None:
    """One atomic upsert; missing optional values preserve previously known data."""
    now = _now()
    optional = recording.to_json()
    statement = sqlite_insert(recordings).values(
        id=recording.recording_id,
        session_dir=os.path.abspath(recording.session_dir),
        status=recording.status,
        created_at=now,
        updated_at=now,
        **optional,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[recordings.c.id],
        set_={
            "status": statement.excluded.status,
            "updated_at": statement.excluded.updated_at,
            **{column: statement.excluded[column] for column in optional},
        },
    )
    with connect() as connection:
        connection.execute(statement)


def replace_participants(
    recording_id: str, participants: Iterable[ParticipantDTO]
) -> None:
    """Delete and batch-insert speakers in one transaction, including an empty set."""
    with connect() as connection:
        connection.execute(
            delete(participants_table).where(
                participants_table.c.recording_id == recording_id
            )
        )
        values = [
            {"recording_id": recording_id, **speaker.model_dump(mode="python")}
            for speaker in participants
        ]
        if values:
            connection.execute(insert(participants_table), values)


def update_participant_transcription(
    recording_id: str,
    participant_id: str,
    text: str | None,
    segments: list[TranscriptSegmentDTO] | None,
) -> None:
    """Update only the selected speaker; preserve every other transcript and audio path."""
    with connect() as connection:
        connection.execute(
            update(participants_table)
            .where(
                participants_table.c.recording_id == recording_id,
                participants_table.c.participant_id == participant_id,
            )
            .values(transcript_text=text, transcript_segments=segments)
        )


def list_recordings(limit: int = 100, offset: int = 0) -> list[RecordingDTO]:
    """Read recordings and their speakers in two queries, never one per recording."""
    with connect() as connection:
        rows = (
            connection.execute(
                select(recordings)
                .order_by(
                    recordings.c.created_at.desc(),
                    _RECORDING_ROWID.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
            .mappings()
            .all()
        )
        if not rows:
            return []
        ids = [row["id"] for row in rows]
        speakers = (
            connection.execute(
                select(participants_table)
                .where(
                    participants_table.c.recording_id.in_(ids),
                )
                .order_by(
                    participants_table.c.participant_name,
                    participants_table.c.participant_id,
                )
            )
            .mappings()
            .all()
        )
    by_recording: dict[str, list[ParticipantDTO]] = {
        recording_id: [] for recording_id in ids
    }
    for row in speakers:
        by_recording[row["recording_id"]].append(_participant_from_row(row))
    return [_recording_from_row(row, by_recording[row["id"]]) for row in rows]


def get_recording(recording_id: str) -> RecordingDTO | None:
    with connect() as connection:
        row = (
            connection.execute(
                select(recordings).where(recordings.c.id == recording_id)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        speakers = (
            connection.execute(
                select(participants_table)
                .where(
                    participants_table.c.recording_id == recording_id,
                )
                .order_by(
                    participants_table.c.participant_name,
                    participants_table.c.participant_id,
                )
            )
            .mappings()
            .all()
        )
    return _recording_from_row(
        row, [_participant_from_row(speaker) for speaker in speakers]
    )


def get_recording_by_session_dir(session_dir: str) -> RecordingDTO | None:
    with connect() as connection:
        recording_id = connection.execute(
            select(recordings.c.id).where(
                recordings.c.session_dir == os.path.abspath(session_dir),
            )
        ).scalar_one_or_none()
    return get_recording(recording_id) if recording_id is not None else None


def jobs_for_recording(recording_id: str) -> list[Job]:
    with connect() as connection:
        rows = (
            connection.execute(
                select(jobs)
                .where(
                    jobs.c.recording_id == recording_id,
                )
                .order_by(jobs.c.created_at)
            )
            .mappings()
            .all()
        )
    return [_job_from_row(row) for row in rows]


def delete_recording(recording_id: str) -> bool:
    """Forget the recording and cascade speakers, leaving files alone."""
    with connect() as connection:
        result = connection.execute(
            delete(recordings).where(recordings.c.id == recording_id)
        )
    return result.rowcount > 0


def delete_transcripts(recording_id: str) -> bool:
    with connect() as connection:
        result = connection.execute(
            update(recordings)
            .where(
                recordings.c.id == recording_id,
            )
            .values(
                meeting_transcript_text=None,
                meeting_transcript_segments=None,
                updated_at=_now(),
            )
        )
        connection.execute(
            update(participants_table)
            .where(
                participants_table.c.recording_id == recording_id,
            )
            .values(transcript_text=None, transcript_segments=None)
        )
    return result.rowcount > 0
