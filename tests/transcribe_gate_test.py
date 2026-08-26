"""Guards the two things that keep invented text out of a transcript.

Whisper answers silence with sentences nobody said - a 220-second participant
track holding 4 seconds of talking came back with "E aí" eight times, one per
30-second window - so transcription reads a speech-only track and skips whatever
is certainly silent. Both halves fail quietly when they break: the first by
sending silence to be hallucinated over, the second by dropping a participant's
words, which is why the threshold is checked against measured levels rather than
against itself.
"""

import subprocess

import pytest

from gravai.models.models import SpeechSegment
from gravai.transcribe.base import _SILENCE_PEAK_DBFS, is_silent, peak_dbfs, to_meeting_time


def _wav(path, filter_spec: str, seconds: float = 3.0) -> str:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", filter_spec, "-t", str(seconds), str(path)],
        check=True,
    )
    return str(path)


def test_digital_silence_is_recognised(tmp_path):
    silent = _wav(tmp_path / "silent.wav", "anullsrc=r=48000:cl=mono")
    assert is_silent(silent)


def test_speech_level_audio_is_not_recognised_as_silent(tmp_path):
    # -14 dBFS is about where speech peaks in these recordings.
    tone = _wav(tmp_path / "tone.wav", "sine=frequency=440:sample_rate=48000:beep_factor=0")
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", tone,
         "-af", "volume=-14dB", str(tmp_path / "quiet.wav")],
        check=True,
    )
    assert not is_silent(str(tmp_path / "quiet.wav"))


def test_the_threshold_sits_between_the_two(tmp_path):
    """The gate is only safe while both cases stay well clear of it."""
    silent = peak_dbfs(_wav(tmp_path / "silent.wav", "anullsrc=r=48000:cl=mono"))
    speech = peak_dbfs(_wav(tmp_path / "tone.wav", "sine=frequency=440:sample_rate=48000"))
    assert silent < _SILENCE_PEAK_DBFS - 20, f"silence measured {silent}, too close to the gate"
    assert speech > _SILENCE_PEAK_DBFS + 20, f"a tone measured {speech}, too close to the gate"


def test_an_unmeasurable_file_is_transcribed_anyway(tmp_path):
    """A broken probe has to send audio to whisper, never drop it."""
    missing = tmp_path / "not-a-wav.wav"
    missing.write_text("this is not audio")
    assert not is_silent(str(missing))


SEGMENTS = [SpeechSegment(start=10.0, end=15.0), SpeechSegment(start=30.0, end=33.0)]


@pytest.mark.parametrize(
    "offset,expected",
    [
        (0.0, 10.0),      # start of the speech track is the start of segment one
        (2.0, 12.0),      # inside segment one
        (7.5, 32.5),      # inside segment two
        (99.0, 33.0),     # past the end lands on the end of the last segment
    ],
)
def test_offsets_map_back_onto_the_meeting(offset, expected):
    assert to_meeting_time(offset, SEGMENTS) == pytest.approx(expected)


def test_a_seam_maps_to_the_side_being_asked_about():
    """Segment one ends 5s in and segment two begins there; they are 15s apart in
    the meeting, so reading both as the same instant would stretch a phrase over
    the gap."""
    assert to_meeting_time(5.0, SEGMENTS, at_segment_start=True) == pytest.approx(30.0)
    assert to_meeting_time(5.0, SEGMENTS, at_segment_start=False) == pytest.approx(15.0)


def test_offsets_pass_through_without_segments():
    assert to_meeting_time(4.0, []) == 4.0


def test_the_language_reaches_whisper(monkeypatch, tmp_path):
    """WHISPER_LANGUAGE existed in .env for a long while without ever being sent.

    Whisper detects the language itself when the field is absent, and on a short
    or noisy clip it picks the wrong one and translates the transcript into it.
    """
    import gravai.transcribe.base as base

    audio = tmp_path / "clip.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:sample_rate=48000", "-t", "1", str(audio)],
        check=True,
    )

    sent = {}

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {"text": "", "segments": []}

    def _fake_post(url, files, data, timeout):
        sent.update(data)
        return _Response()

    monkeypatch.setattr(base.requests, "post", _fake_post)

    base.transcribe(str(audio), "whisper.invalid", 8080, language="pt")
    assert sent["language"] == "pt"

    sent.clear()
    base.transcribe(str(audio), "whisper.invalid", 8080, language="auto")
    assert "language" not in sent, "'auto' means letting whisper decide, so nothing is sent"

    sent.clear()
    base.transcribe(str(audio), "whisper.invalid", 8080)
    assert "language" not in sent
