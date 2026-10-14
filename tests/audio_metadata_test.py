"""Saved audio metadata remains readable across the field-name migration."""

import json

import pytest
from pydantic import ValidationError

from gravai.models.audio import SessionAudioMetadata
from gravai.models.common import Session
from gravai.slicing.slice import _extract_session_dto


START = "2026-01-01T00:00:00+00:00"
END = "2026-01-01T00:00:10+00:00"


def test_legacy_session_paths_load_and_are_written_with_audio_names():
    session = Session.model_validate_json(json.dumps({
        "session_id": "legacy", "session_start": START, "session_end": END,
        "tracks": {"participant": {
            "participant_id": "participant", "participant_name": "Participant",
            "track": {
                "wav_file_path": "/recordings/participant.wav",
                "speech_wav_file_path": "/recordings/participant_speech.wav",
            },
        }},
    }))
    track = session.tracks["participant"].track
    assert track.audio_file_path == "/recordings/participant.wav"
    assert track.speech_audio_file_path == "/recordings/participant_speech.wav"
    saved = session.model_dump(mode="json")["tracks"]["participant"]["track"]
    assert "audio_file_path" in saved and "speech_audio_file_path" in saved
    assert "wav_file_path" not in saved and "speech_wav_file_path" not in saved
    assert Session.model_validate_json(session.model_dump_json()) == session


@pytest.mark.parametrize("suffix,file_key", [("wav", "wav_file"), ("flac", "audio_file")])
def test_slicing_reads_legacy_and_current_sidecars(tmp_path, suffix, file_key):
    filename = f"track_mainAudio.{suffix}"
    (tmp_path / filename).write_bytes(b"audio")
    payload = {
        "session_id": "metadata", "session_start": START,
        "tracks": {"mainAudio": {
            "started_at": START, "ended_at": END,
            "sample_rate": 48000, "channels": 1, file_key: filename,
        }},
    }
    (tmp_path / "session_sidecar.json").write_text(json.dumps(payload))
    (tmp_path / "vad_timeline.json").write_text(json.dumps({"events": []}))
    session = _extract_session_dto(str(tmp_path))
    assert session.main_track_name == filename
    assert (session.end_time - session.start_time).total_seconds() == 10
    metadata = SessionAudioMetadata.model_validate(payload)
    saved = metadata.model_dump(mode="json")["tracks"]["mainAudio"]
    assert saved["audio_file"] == filename and "wav_file" not in saved


def test_invalid_capture_format_is_rejected():
    with pytest.raises(ValidationError):
        SessionAudioMetadata.model_validate({
            "session_id": "invalid",
            "tracks": {"mainAudio": {"started_at": START, "sample_rate": 0}},
        })
