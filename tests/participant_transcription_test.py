"""A speaker retry must not rewrite the mix, other speakers, or recording status."""

from datetime import UTC, datetime

import pytest

from gravai.api import pipeline
from gravai.jobs import runner, store
from gravai.jobs.models import JobSubmission, JobType
from gravai.models.common import SpeechSegment
from gravai.models.database import JobParamsDTO, ParticipantDTO, RecordingUpsertDTO, TranscriptSegmentDTO
from gravai.transcribe import base


@pytest.fixture
def recording(database, tmp_path):
    for filename in ("mix.wav", "alice.wav", "alice_speech.wav", "bob.wav", "bob_transcription.txt"):
        (tmp_path / filename).write_bytes(b"keep existing content")
    store.upsert_recording(RecordingUpsertDTO(
        recording_id="meeting", session_dir=str(tmp_path), status="complete",
        started_at=datetime.now(UTC), ended_at=datetime.now(UTC),
        main_track_path=str(tmp_path / "mix.wav"),
        meeting_transcript_text="Existing mixed transcript",
        meeting_transcript_segments=[TranscriptSegmentDTO(start=0, end=1, text="Existing mixed transcript")],
    ))
    store.replace_participants("meeting", [
        ParticipantDTO(participant_id="alice", participant_name="Alice",
            track_path=str(tmp_path / "alice.wav"), speech_track_path=str(tmp_path / "alice_speech.wav"),
            segments=[SpeechSegment(start=10, end=15)], transcript_text="Old Alice"),
        ParticipantDTO(participant_id="bob", participant_name="Bob", track_path=str(tmp_path / "bob.wav"),
            segments=[SpeechSegment(start=20, end=25)], transcript_text="Keep Bob",
            transcript_segments=[TranscriptSegmentDTO(start=20, end=21, text="Keep Bob")]),
    ])
    return store.get_recording("meeting")


def test_retry_transcribes_only_selected_speech_and_preserves_everything_else(recording, tmp_path, monkeypatch):
    calls = []

    def transcribe(source, *args):
        calls.append(source)
        return {"text": "New Alice", "segments": [{"start": 0, "end": 1, "text": "New Alice"}]}

    def forbidden(*args, **kwargs):
        pytest.fail("A speaker retry must not slice or transcribe the mixed track")

    monkeypatch.setattr(base, "_transcribe_or_skip_silence", transcribe)
    monkeypatch.setattr(base, "_transcribe_meeting", forbidden)
    monkeypatch.setattr(pipeline, "service_slice_track", forbidden)
    params = runner._validate(JobSubmission(type=JobType.TRANSCRIBE,
        tracks_output_dir=recording.session_dir, participant_id="alice"))
    job = store.create_job("speaker-job", JobType.TRANSCRIBE, params)
    assert store.get_job(job.id).params.participant_id == "alice"
    result = runner._run_transcribe(job, params)
    after = store.get_recording(recording.id)
    assert calls == [str(tmp_path / "alice_speech.wav")]
    assert after.participants[0].transcript_text == "New Alice"
    assert after.participants[0].transcript_segments[0].start == 10
    assert after.participants[0].transcript_segments[0].end == 11
    assert after.participants[1] == recording.participants[1]
    assert after.meeting_transcript_text == recording.meeting_transcript_text
    assert after.meeting_transcript_segments == recording.meeting_transcript_segments
    assert after.status == "complete"
    assert (tmp_path / "mix.wav").read_bytes() == b"keep existing content"
    assert (tmp_path / "bob_transcription.txt").read_bytes() == b"keep existing content"
    assert result.recording_id == recording.id
    assert store.get_job(job.id).recording_id == recording.id


def test_failed_speaker_retry_keeps_recording_and_transcripts(recording, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("Whisper is unavailable")

    monkeypatch.setattr(pipeline, "transcribe_participant", fail)
    params = JobParamsDTO(tracks_output_dir=recording.session_dir, participant_id="alice")
    job = store.create_job("failed-speaker", JobType.TRANSCRIBE, params)
    with pytest.raises(RuntimeError, match="Whisper"):
        runner._run_transcribe(job, params)
    runner._mark_recording_failed(job.id)
    assert store.get_recording(recording.id) == recording


def test_unknown_speaker_is_rejected(recording):
    with pytest.raises(runner.JobError, match="No speaker"):
        runner._validate(JobSubmission(type=JobType.TRANSCRIBE,
            tracks_output_dir=recording.session_dir, participant_id="missing"))


def test_scope_is_not_silently_ignored_for_record_jobs():
    with pytest.raises(runner.JobError, match="only valid"):
        runner._validate(JobSubmission(type=JobType.RECORD,
            meeting_url="https://meet.google.com/abc-defg-hij", participant_id="alice"))


def test_scopes_allow_other_speakers_but_block_duplicate_and_whole_meeting_jobs(recording):
    store.create_job("active-alice", JobType.TRANSCRIBE,
        JobParamsDTO(tracks_output_dir=recording.session_dir, participant_id="alice"))
    for participant_id in ("alice", None):
        with pytest.raises(runner.JobError, match="already working"):
            runner._validate(JobSubmission(type=JobType.TRANSCRIBE,
                tracks_output_dir=recording.session_dir, participant_id=participant_id))
    params = runner._validate(JobSubmission(type=JobType.TRANSCRIBE,
        tracks_output_dir=recording.session_dir, participant_id="bob"))
    assert params.participant_id == "bob"
