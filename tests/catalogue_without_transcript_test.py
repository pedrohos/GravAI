"""What survives a transcription that fails.

Whisper runs in a service of its own, and that service is routinely not up. The
recording is finished by then and so is slicing: the mixed track, one track per
speaker and the turns behind them are all on disk and none of them came from
whisper. They belong in the catalogue whatever whisper does next, and until the
catalogue write was moved ahead of transcription they were lost with it - the
meeting read back as if nothing had been recorded at all.
"""

from datetime import UTC, datetime

import pytest

from gravai.api import pipeline
from gravai.jobs import runner, store
from gravai.jobs.models import JobSubmission, JobType
from gravai.models.common import ParticipantData, Session, SpeechSegment, Track


class WhisperIsDown(RuntimeError):
    """What a transcription against a whisper that is not running looks like."""


def _sliced_session(tmp_path) -> Session:
    """What slicing leaves behind: a mix, a speaker and their turns. No transcript."""
    main = tmp_path / "track_mainAudio-1.wav"
    main.write_bytes(b"")
    aligned = tmp_path / "track_Ana.wav"
    aligned.write_bytes(b"")
    speech = tmp_path / "track_Ana_speech.wav"
    speech.write_bytes(b"")
    return Session(
        session_id="session-1",
        session_start=datetime.now(UTC),
        session_end=datetime.now(UTC),
        main_track_path=str(main),
        tracks={
            "Ana": ParticipantData(
                participant_id="Ana",
                participant_name="Ana",
                track=Track(
                    wav_file_path=str(aligned),
                    speech_wav_file_path=str(speech),
                    speech_segments=[SpeechSegment(start=1.0, end=4.5)],
                ),
            )
        },
    )


@pytest.fixture
def whisper_is_down(monkeypatch, tmp_path):
    """Slicing works, transcription does not - the failure this is all about."""
    session = _sliced_session(tmp_path)

    def slice_track(recording_type, tracks_output_dir, group_slices_by_name=True):
        return str(tmp_path / "session_metadata.json"), session

    def transcribe(_session):
        raise WhisperIsDown("connection refused")

    monkeypatch.setattr(pipeline, "service_slice_track", slice_track)
    monkeypatch.setattr(pipeline, "service_transcribe_meeting_tracks", transcribe)
    return session


def test_the_hook_runs_before_transcription_rather_than_after(whisper_is_down, tmp_path):
    """Ordering is the whole point: after a failure there is no 'after'."""
    seen = []

    with pytest.raises(WhisperIsDown):
        pipeline.transcribe_tracks(
            str(tmp_path),
            on_sliced=lambda path, session: seen.append(session.session_id),
        )

    assert seen == ["session-1"]


def test_a_failed_transcription_still_leaves_the_tracks_in_the_catalogue(
    database, whisper_is_down, tmp_path
):
    job = store.create_job(
        "job-1", JobType.TRANSCRIBE, {"tracks_output_dir": str(tmp_path)}
    )

    with pytest.raises(WhisperIsDown):
        runner._run_transcribe(job, job.params)

    recording = store.get_recording("session-1")
    assert recording is not None, "the meeting was never catalogued at all"
    assert recording["main_track_path"], "the whole-meeting track is missing"

    participants = recording["participants"]
    assert [p["participant_name"] for p in participants] == ["Ana"]
    assert participants[0]["track_path"], "the speaker's aligned track is missing"
    assert participants[0]["speech_track_path"], "the speaker's speech track is missing"
    assert participants[0]["segments"] == [{"start": 1.0, "end": 4.5}]
    # The half that genuinely did not happen stays empty rather than being faked.
    assert participants[0]["transcript_text"] is None
    assert recording["meeting_transcript_text"] is None


def test_the_recording_is_linked_to_the_job_that_was_transcribing_it(
    database, whisper_is_down, tmp_path
):
    """Without the link, the failure has no row to mark and the meeting is
    left claiming to be processing for good."""
    job = store.create_job(
        "job-2", JobType.TRANSCRIBE, {"tracks_output_dir": str(tmp_path)}
    )

    with pytest.raises(WhisperIsDown):
        runner._run_transcribe(job, job.params)

    assert store.get_job("job-2").recording_id == "session-1"


def test_a_recording_can_be_transcribed_again_from_its_own_directory(database, tmp_path):
    """Re-transcribing is a transcribe job over a directory that already has a
    recording in the catalogue, so it has to be accepted rather than refused as
    a duplicate."""
    (tmp_path / "track_mainAudio-1.wav").write_bytes(b"")
    store.upsert_recording(
        recording_id="session-1",
        session_dir=str(tmp_path),
        status="complete",
        meeting_url="https://meet.google.com/abc-defg-hij",
        provider="meet",
    )

    params = runner._validate(
        JobSubmission(type=JobType.TRANSCRIBE, tracks_output_dir=str(tmp_path))
    )

    assert params["tracks_output_dir"] == str(tmp_path)
