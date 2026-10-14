"""The HTTP surface: what a caller can ask for and what comes back.

These do not run a meeting. What they cover is the contract around one - that a
bad request is refused before a job exists, that the two ways of ending a job
report differently, and that a recording reads back with the timings, the
speaking segments and the text a caller came for.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from gravai.api.app import app
from gravai.jobs import store
from gravai.jobs.models import JobStatus, JobType
from gravai.models.database import (
    JobParamsDTO,
    JobResultDTO,
    ParticipantDTO,
    RecordingUpsertDTO,
)


@pytest.fixture
def client(database):
    with TestClient(app) as test_client:
        yield test_client


def _seed_recording(recording_id="rec-1"):
    started = datetime.now(UTC) - timedelta(minutes=30)
    store.upsert_recording(
        RecordingUpsertDTO(
            recording_id=recording_id,
            session_dir=f"/tmp/{recording_id}_tracks",
            status="complete",
            meeting_url="https://meet.google.com/abc-defg-hij",
            provider="meet",
            started_at=started,
            ended_at=started + timedelta(minutes=25),
            main_track_path=f"/tmp/{recording_id}_tracks/track_mainAudio-1.wav",
            meeting_transcript_text="bom dia a todos",
            meeting_transcript_segments=[
                {"start": 1.0, "end": 2.5, "text": " bom dia a todos"}
            ],
        )
    )
    store.replace_participants(
        recording_id,
        [
            ParticipantDTO.model_validate(
                {
                    "participant_id": "Ana",
                    "participant_name": "Ana",
                    "track_path": f"/tmp/{recording_id}_tracks/track_Ana.wav",
                    "speech_track_path": f"/tmp/{recording_id}_tracks/track_Ana_speech.wav",
                    "segments": [
                        {"start": 1.0, "end": 4.0},
                        {"start": 61.0, "end": 66.5},
                    ],
                    "transcript_text": "bom dia",
                    "transcript_segments": [
                        {"start": 1.0, "end": 2.5, "text": " bom dia"}
                    ],
                }
            )
        ],
    )


def test_submitting_answers_with_a_job_and_not_a_result(client):
    response = client.post(
        "/jobs",
        json={
            "type": "record",
            "meeting_url": "https://meet.google.com/abc-defg-hij",
        },
    )

    assert response.status_code == 202
    job = response.json()
    assert job["status"] == "queued"
    assert job["params"]["meeting_url"] == "https://meet.google.com/abc-defg-hij"

    client.post(f"/jobs/{job['id']}/cancel")


def test_a_url_no_provider_claims_is_a_bad_request(client):
    response = client.post(
        "/jobs", json={"type": "record", "meeting_url": "https://example.com/x"}
    )

    assert response.status_code == 400
    assert "No provider matches" in response.json()["detail"]
    assert client.get("/jobs").json() == []


def test_a_recording_directory_that_is_not_there_is_a_bad_request(client):
    response = client.post(
        "/jobs", json={"type": "transcribe", "tracks_output_dir": "/no/such/directory"}
    )

    assert response.status_code == 400


def test_the_meeting_shorthands_submit_the_same_jobs(client):
    """Asking for both is two jobs now, and the recording is what comes back:
    it is the head of the chain and the one with a meeting to stop."""
    response = client.post(
        "/record_meeting_and_transcribe",
        params={"meeting_url": "https://meet.google.com/abc-defg-hij"},
    )

    assert response.status_code == 202
    recording = response.json()
    assert recording["type"] == "record"
    assert recording["depends_on"] is None

    jobs = client.get("/jobs").json()
    transcription = next(job for job in jobs if job["type"] == "transcribe")
    assert transcription["depends_on"] == recording["id"]

    client.post(f"/jobs/{transcription['id']}/cancel")
    client.post(f"/jobs/{recording['id']}/cancel")


def test_ending_a_job_that_has_already_ended_is_a_conflict(client):
    store.create_job("done", JobType.RECORD, JobParamsDTO.model_validate({}))
    store.finish_job(
        "done", JobStatus.SUCCEEDED, result=JobResultDTO.model_validate({})
    )

    assert client.post("/jobs/done/stop").status_code == 409
    assert client.post("/jobs/done/cancel").status_code == 409


def test_a_running_job_cannot_be_deleted_out_from_under_itself(client):
    store.create_job("live", JobType.RECORD, JobParamsDTO.model_validate({}))
    store.mark_started("live", pid=1234)

    assert client.delete("/jobs/live").status_code == 409

    store.finish_job("live", JobStatus.FAILED, error="x")
    assert client.delete("/jobs/live").status_code == 204
    assert client.get("/jobs/live").status_code == 404


def test_a_recording_reads_back_with_its_timings_segments_and_text(client):
    _seed_recording()

    recording = client.get("/recordings/rec-1").json()

    assert recording["duration_seconds"] == pytest.approx(25 * 60)
    assert recording["meeting_transcript_text"] == "bom dia a todos"
    assert recording["meeting_transcript_segments"][0]["text"] == " bom dia a todos"

    ana = recording["participants"][0]
    assert ana["participant_name"] == "Ana"
    assert ana["segments"] == [{"start": 1.0, "end": 4.0}, {"start": 61.0, "end": 66.5}]
    assert ana["transcript_text"] == "bom dia"


def test_the_recording_list_carries_its_speakers(client):
    _seed_recording("rec-1")
    _seed_recording("rec-2")

    listed = client.get("/recordings").json()

    assert {recording["id"] for recording in listed} == {"rec-1", "rec-2"}
    assert [p["participant_name"] for p in listed[0]["participants"]] == ["Ana"]


def test_audio_that_is_catalogued_but_gone_reports_as_gone(client):
    """SAVE_DIR defaults to /tmp; a row outliving its files is not a broken service."""
    _seed_recording()

    assert client.get("/recordings/rec-1/audio").status_code == 410
    assert client.get("/recordings/rec-1/participants/Ana/audio").status_code == 410
    assert client.get("/recordings/rec-1/participants/Nobody/audio").status_code == 404


def test_the_log_is_empty_until_a_recording_has_somewhere_to_write(client):
    store.create_job("fresh", JobType.RECORD, JobParamsDTO.model_validate({}))

    log = client.get("/jobs/fresh/log").json()
    assert log["lines"] == []
    assert log["session_dir"] is None


def test_the_log_comes_back_from_the_session_directory(client, tmp_path):
    store.create_job("logged", JobType.RECORD, JobParamsDTO.model_validate({}))
    store.attach_session("logged", "session-1", str(tmp_path))
    (tmp_path / "session.log").write_text("\n".join(f"line {n}" for n in range(500)))

    log = client.get("/jobs/logged/log", params={"lines": 3}).json()

    assert log["lines"] == ["line 497", "line 498", "line 499"]


def test_settings_are_readable_and_secrets_are_not(client):
    fields = {field["name"]: field for field in client.get("/config").json()["fields"]}

    assert "WHISPER_HOST" in fields
    assert "VAD_OBSERVER_MEET_JS_PATH" not in fields
    assert fields["GOOGLE_ACCOUNT_PASSWORD"]["secret"] is True
    assert "hunter" not in fields["GOOGLE_ACCOUNT_PASSWORD"]["value"]


# --- cross-origin access ---------------------------------------------------
#
# The shipped setup never uses this: the front end container calls the API from
# its own Next.js server over the Docker network, so no browser call is ever
# cross-origin and CORS_ALLOW_ORIGINS is empty. The mechanism is still here for
# a deployment that puts a browser on this API directly, and is still worth
# holding to - nothing here is authenticated, so the allowlist is the only thing
# deciding which pages may drive the service. conftest sets it explicitly.


def test_a_listed_origin_is_allowed(client):
    response = client.get("/jobs", headers={"Origin": "http://localhost:3000"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_an_unlisted_origin_gets_no_permission(client):
    """The response still arrives - it is the browser that refuses it - so what
    is actually being checked is that the header is absent."""
    response = client.get("/jobs", headers={"Origin": "http://evil.example"})

    assert "access-control-allow-origin" not in response.headers


def test_a_json_post_is_preflighted_successfully(client):
    """Submitting a job sends Content-Type: application/json, which is what makes
    the browser ask permission first."""
    response = client.options(
        "/jobs",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "DELETE" in response.headers["access-control-allow-methods"]


def test_the_allowlist_is_never_a_wildcard(client):
    """A wildcard would hand this API to every website its users have open."""
    response = client.get("/jobs", headers={"Origin": "http://localhost:3000"})

    assert response.headers.get("access-control-allow-origin") != "*"


@pytest.mark.parametrize(
    "configured, expected",
    [
        ("", []),
        ("   ", []),
        ("http://localhost:3000", ["http://localhost:3000"]),
        (
            "http://localhost:3000, http://127.0.0.1:3000",
            ["http://localhost:3000", "http://127.0.0.1:3000"],
        ),
        ("http://a:1,,http://b:2,", ["http://a:1", "http://b:2"]),
    ],
)
def test_the_origin_list_is_read_from_one_comma_separated_setting(
    configured, expected, monkeypatch
):
    """It is one environment variable, so it has to survive being typed by hand."""
    from gravai.config.settings import get_settings

    monkeypatch.setenv("CORS_ALLOW_ORIGINS", configured)
    get_settings.cache_clear()
    try:
        assert get_settings().cors_allow_origins == expected
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("both", [False, True])
def test_delete_transcription_and_optional_audio(client, tmp_path, both):
    track = tmp_path / "meeting.wav"
    track.write_bytes(b"audio")
    text = tmp_path / "meeting_transcription_text.txt"
    segments = tmp_path / "meeting_transcription_segments.json"
    text.write_text("hello")
    segments.write_text("[]")
    unrelated = tmp_path / "notes.txt"
    unrelated.write_text("keep")
    store.upsert_recording(
        RecordingUpsertDTO(
            recording_id="delete-me",
            session_dir=str(tmp_path),
            status="complete",
            main_track_path=str(track),
            meeting_transcript_text="hello",
        )
    )
    store.replace_participants(
        "delete-me",
        [
            ParticipantDTO.model_validate(
                {
                    "participant_id": "speaker",
                    "track_path": str(track),
                    "transcript_text": "hello",
                    "transcript_segments": [],
                    "segments": [{"start": 0, "end": 1}],
                }
            )
        ],
    )
    route = "files" if both else "transcription"
    assert client.delete(f"/recordings/delete-me/{route}").status_code == 204
    assert not text.exists() and not segments.exists()
    assert unrelated.exists()
    assert track.exists() is not both
    recording = store.get_recording("delete-me")
    if both:
        assert recording is None
    else:
        assert recording.meeting_transcript_text is None
        assert recording.participants[0].transcript_text is None
        assert recording.participants[0].transcript_segments is None
        assert [
            (segment.start, segment.end)
            for segment in recording.participants[0].segments
        ] == [(0, 1)]


@pytest.mark.parametrize("route", ["files", "transcription"])
def test_delete_content_missing_or_active_recording(client, tmp_path, route):
    assert client.delete(f"/recordings/missing/{route}").status_code == 404
    store.upsert_recording(
        RecordingUpsertDTO(
            recording_id="busy", session_dir=str(tmp_path), status="recording"
        )
    )
    assert client.delete(f"/recordings/busy/{route}").status_code == 409


@pytest.mark.parametrize(
    "suffix,mime", [(".wav", "audio/wav"), (".flac", "audio/flac")]
)
@pytest.mark.parametrize("speech_only", [False, True])
def test_participant_audio_serves_stored_format(
    client, tmp_path, suffix, mime, speech_only
):
    track = tmp_path / f"participant{suffix}"
    speech = tmp_path / f"participant_speech{suffix}"
    track.write_bytes(b"aligned audio")
    speech.write_bytes(b"speech audio")
    store.upsert_recording(
        RecordingUpsertDTO(
            recording_id="formats", session_dir=str(tmp_path), status="complete"
        )
    )
    store.replace_participants(
        "formats",
        [
            ParticipantDTO.model_validate(
                {
                    "participant_id": "participant",
                    "participant_name": "Participant",
                    "track_path": str(track),
                    "speech_track_path": str(speech),
                }
            )
        ],
    )
    response = client.get(
        "/recordings/formats/participants/participant/audio",
        params={"speech_only": speech_only},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == mime
    assert f'filename="participant{suffix}"' in response.headers["content-disposition"]
    assert response.content == (b"speech audio" if speech_only else b"aligned audio")
