"""SQLAlchemy Core schema for jobs, recordings, and participants."""

from pydantic import TypeAdapter
from sqlalchemy import Column, ForeignKey, Index, Integer, MetaData, Table, Text

from gravai.jobs.database_types import DTOJSON, ISODateTime
from gravai.models.common import SpeechSegment
from gravai.models.database import JobParamsDTO, JobResultDTO, TranscriptSegmentDTO

metadata = MetaData()

# Legacy SQLite TEXT primary keys were nullable in the physical schema.
# DTOs still require string IDs; retain that schema for existing catalogues.


def _no_result() -> None:
    return None


jobs = Table(
    "jobs",
    metadata,
    Column("id", Text, primary_key=True, nullable=True),
    Column("type", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column(
        "params",
        DTOJSON(TypeAdapter(JobParamsDTO), JobParamsDTO),
        nullable=False,
        server_default="{}",
    ),
    Column("result", DTOJSON(TypeAdapter(JobResultDTO | None), _no_result)),
    Column("error", Text),
    Column("pid", Integer),
    # /proc start ticks distinguish a process from a later reuse of its pid.
    Column("pid_start", Integer),
    Column("recording_id", Text),
    Column("session_dir", Text),
    # Provenance survives deletion of the recording job: intentionally not a FK.
    Column("depends_on", Text),
    Column("created_at", ISODateTime(), nullable=False),
    Column("started_at", ISODateTime()),
    Column("finished_at", ISODateTime()),
    Column("stop_requested_at", ISODateTime()),
)
Index("jobs_status_idx", jobs.c.status)
Index("jobs_created_idx", jobs.c.created_at.desc())

recordings = Table(
    "recordings",
    metadata,
    Column("id", Text, primary_key=True, nullable=True),
    Column("meeting_url", Text),
    Column("provider", Text),
    Column("session_dir", Text, nullable=False, unique=True),
    Column("status", Text, nullable=False),
    Column("started_at", ISODateTime()),
    Column("ended_at", ISODateTime()),
    Column("main_track_path", Text),
    Column("meeting_transcript_text", Text),
    Column(
        "meeting_transcript_segments",
        DTOJSON(TypeAdapter(list[TranscriptSegmentDTO] | None), _no_result),
    ),
    Column("created_at", ISODateTime(), nullable=False),
    Column("updated_at", ISODateTime(), nullable=False),
)
Index("recordings_created_idx", recordings.c.created_at.desc())

participants = Table(
    "participants",
    metadata,
    Column(
        "recording_id",
        Text,
        ForeignKey("recordings.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    ),
    Column("participant_id", Text, primary_key=True, nullable=False),
    Column("participant_name", Text),
    Column("track_path", Text),
    Column("speech_track_path", Text),
    Column(
        "segments",
        DTOJSON(TypeAdapter(list[SpeechSegment]), list),
        nullable=False,
        server_default="[]",
    ),
    Column("transcript_text", Text),
    Column(
        "transcript_segments",
        DTOJSON(TypeAdapter(list[TranscriptSegmentDTO] | None), _no_result),
    ),
)
