"""DTOs validate persisted data without losing JSON fields or update semantics."""

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from gravai.api.app import app
from gravai.jobs import store
from gravai.models.common import SpeechSegment
from gravai.models.database import (
    JobParamsDTO,
    JobResultDTO,
    JobStatus,
    JobType,
    JobUpdateDTO,
    ParticipantDTO,
    RecordingUpsertDTO,
    TranscriptSegmentDTO,
)


def test_job_json_retains_only_supplied_fields(database):
    job = store.create_job(
        "typed", JobType.TRANSCRIBE, JobParamsDTO(group_slices_by_name=False)
    )
    store.finish_job(
        job.id, JobStatus.SUCCEEDED, JobResultDTO(recording_path="/tmp/recording")
    )
    found = store.get_job(job.id)
    assert found is not None and found.result is not None
    assert found.params.group_slices_by_name is False
    assert found.result.recording_path == "/tmp/recording"
    payload = found.model_dump(mode="json")
    assert payload["params"] == {"group_slices_by_name": False}
    assert payload["result"] == {"recording_path": "/tmp/recording"}
    with store.connect() as conn:
        row = (
            conn.exec_driver_sql(
                "SELECT params, result FROM jobs WHERE id = ?", (job.id,)
            )
            .mappings()
            .one()
        )
    assert json.loads(row["params"]) == payload["params"]
    assert json.loads(row["result"]) == payload["result"]


def test_job_patch_distinguishes_omitted_fields_from_null(database):
    store.create_job(
        "patch",
        JobType.RECORD,
        JobParamsDTO(meeting_url="https://meet.google.com/example"),
    )
    store.update_job("patch", JobUpdateDTO(error="old error", pid=1234))
    store.update_job("patch", JobUpdateDTO(error=None))
    store.update_job("patch", JobUpdateDTO())
    job = store.get_job("patch")
    assert job is not None
    assert job.error is None and job.pid == 1234
    assert job.params.meeting_url == "https://meet.google.com/example"
    assert job.status is JobStatus.QUEUED


def test_job_patch_serializes_nested_dtos(database):
    store.create_job("patch", JobType.RECORD, JobParamsDTO())
    store.update_job(
        "patch",
        JobUpdateDTO(
            params=JobParamsDTO(slice_tracks=False),
            result=JobResultDTO(recording_path="/tmp/audio"),
        ),
    )
    job = store.get_job("patch")
    assert job is not None and job.result is not None
    assert job.params.slice_tracks is False
    assert job.result.recording_path == "/tmp/audio"
    store.update_job("patch", JobUpdateDTO(result=None))
    assert store.get_job("patch").result is None


def test_unrecognized_update_columns_are_rejected(database):
    store.create_job("patch", JobType.RECORD, JobParamsDTO())
    with pytest.raises(ValidationError):
        store.update_job(
            "patch", JobUpdateDTO.model_validate({"unexpected_column": "value"})
        )
    assert store.get_job("patch").status is JobStatus.QUEUED


def test_transcript_metadata_round_trips_through_sqlite_and_http(database, tmp_path):
    raw_segment = {
        "start": 1.0,
        "end": 2.0,
        "text": "example",
        "tokens": [42, 43],
        "confidence": 0.95,
        "metadata": {"language": "en", "flags": [True, None]},
    }
    segment = TranscriptSegmentDTO.model_validate(raw_segment)
    store.upsert_recording(
        RecordingUpsertDTO(
            recording_id="segments",
            session_dir=str(tmp_path),
            status="complete",
            meeting_transcript_segments=[segment],
        )
    )
    store.replace_participants(
        "segments",
        [
            ParticipantDTO(
                participant_id="participant",
                segments=[SpeechSegment(start=1, end=2)],
                transcript_segments=[segment],
            )
        ],
    )
    recording = store.get_recording("segments")
    assert recording is not None
    assert recording.participants[0].segments[0].start == 1
    assert recording.participants[0].transcript_segments[0].text == "example"
    with TestClient(app) as client:
        response = client.get("/recordings/segments")
    assert response.status_code == 200
    assert response.json()["meeting_transcript_segments"] == [raw_segment]
    assert response.json()["participants"][0]["transcript_segments"] == [raw_segment]


def test_replacing_participants_rolls_back_if_input_iteration_fails(database, tmp_path):
    store.upsert_recording(
        RecordingUpsertDTO(
            recording_id="atomic", session_dir=str(tmp_path), status="complete"
        )
    )
    store.replace_participants("atomic", [ParticipantDTO(participant_id="original")])

    def broken_input():
        yield ParticipantDTO(participant_id="replacement")
        raise RuntimeError("input failed")

    with pytest.raises(RuntimeError, match="input failed"):
        store.replace_participants("atomic", broken_input())
    assert [
        participant.participant_id
        for participant in store.get_recording("atomic").participants
    ] == ["original"]


def test_malformed_legacy_json_keeps_existing_fallbacks(database):
    store.create_job("legacy", JobType.RECORD, JobParamsDTO())
    with store.connect() as conn:
        conn.exec_driver_sql(
            "UPDATE jobs SET params = ?, result = ? WHERE id = ?",
            ("{broken", "{broken", "legacy"),
        )
    job = store.get_job("legacy")
    assert job is not None
    assert job.params == JobParamsDTO() and job.result is None


@pytest.mark.parametrize("column", ["type", "status", "params", "created_at"])
def test_job_patch_rejects_null_for_required_database_columns(column):
    with pytest.raises(ValidationError):
        JobUpdateDTO.model_validate({column: None})


def test_session_snapshot_round_trips_in_job_result(database):
    from datetime import UTC, datetime

    from gravai.models.common import ParticipantData, Session, Track

    now = datetime.now(UTC)
    session = Session[ParticipantData](
        session_id="session",
        session_start=now,
        session_end=now,
        tracks={
            "participant": ParticipantData(
                participant_id="participant",
                participant_name="Participant",
                track=Track(audio_file_path="/tmp/participant.flac"),
            )
        },
    )
    store.create_job("snapshot", JobType.RECORD, JobParamsDTO())
    store.finish_job("snapshot", JobStatus.SUCCEEDED, JobResultDTO(session=session))
    job = store.get_job("snapshot")
    assert job is not None and job.result is not None
    assert job.result.session == session
    assert job.model_dump(mode="json")["result"]["session"] == session.model_dump(
        mode="json"
    )
