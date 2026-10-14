"""Participant storage stays compressed while Whisper receives compatible WAV."""

from pathlib import Path
import subprocess

import pytest

from gravai.models.common import ActionType
from gravai.slicing.slice import _ffmpeg_slice, _participant_track_path, _speech_track_path
from gravai.transcribe import base
from gravai.transcribe.errors import WhisperError
from types import SimpleNamespace


def _tone(path, seconds=1):
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:sample_rate=48000", "-t", str(seconds), str(path)],
        check=True,
    )


def test_slicing_writes_flac_with_aligned_and_speech_only_timelines(tmp_path):
    from datetime import timedelta

    session = SimpleNamespace(main_track_name="track_mainAudio.flac")
    main = tmp_path / session.main_track_name
    _tone(main, seconds=10)
    segments = _ffmpeg_slice(str(tmp_path), session, {
        "participant": [(ActionType.START, timedelta(seconds=5)),
                        (ActionType.END, timedelta(seconds=6))],
        "idle": [],
    })
    aligned = Path(_participant_track_path(str(tmp_path), "participant"))
    speech = Path(_speech_track_path(str(tmp_path), "participant"))
    assert aligned.suffix == speech.suffix == ".flac"
    for path in (aligned, speech):
        assert path.read_bytes().startswith(b"fLaC")
    assert aligned.stat().st_size < 10 * 48000 * 2 / 2
    assert base.is_silent(_participant_track_path(str(tmp_path), "idle"))
    assert base.is_silent(_speech_track_path(str(tmp_path), "idle"))
    assert not base.is_silent(str(speech))
    durations = []
    for path in (aligned, speech):
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            check=True, capture_output=True, text=True,
        )
        durations.append(float(result.stdout))
    assert durations[0] == pytest.approx(12.0, abs=0.01)
    start, end = segments["participant"][0]
    assert durations[1] == pytest.approx(end - start, abs=0.01)


@pytest.mark.parametrize("failure", [None, "http", "connection"])
@pytest.mark.parametrize("suffix,mime", [(".flac", "audio/flac"), (".wav", "audio/wav")])
def test_whisper_uploads_original_audio_without_conversion(tmp_path, monkeypatch, failure, suffix, mime):
    audio = tmp_path / f"participant_speech{suffix}"
    _tone(audio)
    original = audio.read_bytes()
    streams = []

    class Response:
        status_code = 500 if failure == "http" else 200
        text = "server error"

        def json(self):
            return {"text": "test", "segments": []}

    def post(url, files, data, timeout):
        filename, stream, content_type = files["file"]
        streams.append(stream)
        assert filename == audio.name
        assert content_type == mime
        assert Path(stream.name) == audio
        assert stream.read() == original
        if failure == "connection":
            raise base.requests.ConnectionError("offline")
        return Response()

    monkeypatch.setattr(base.requests, "post", post)
    monkeypatch.setattr(base.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not convert"))
    if failure:
        error = WhisperError if failure == "http" else base.requests.ConnectionError
        with pytest.raises(error):
            base.transcribe(str(audio), "whisper.invalid", 8080)
    else:
        assert base.transcribe(str(audio), "whisper.invalid", 8080)["text"] == "test"
    assert audio.read_bytes() == original
    assert len(streams) == 1 and streams[0].closed


def test_flac_capture_probe_measures_recent_audio_not_old_speech(tmp_path):
    from gravai.recording.session_audio_capture import capture_is_audible

    audio = tmp_path / "track_mainAudio.flac"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=3",
         "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1", str(audio)],
        check=True,
    )
    assert not capture_is_audible(str(audio), window_s=1)
    assert capture_is_audible(str(audio), window_s=5)
