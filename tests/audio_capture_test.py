"""The audio of a recording, taken from its own PulseAudio sink.

This replaces the audio server tests. What is under test is the same contract
they guarded - one main track, a sidecar slicing can read, nothing shared
between two recordings running at once - now that the audio comes from the
operating system instead of from a tap inside the page.

A browser stands in as any process bound by PULSE_SINK, because that is all the
browser is here: `ffmpeg` playing a tone with the environment variable set is
routed by exactly the mechanism Chrome is routed by, and it is the routing that
these tests are about.

Needs a PulseAudio daemon, which both Dockerfiles install; skipped without one.
"""

import json
import math
import os
import shutil
import subprocess
import wave
from array import array
from datetime import datetime
from glob import glob
from pathlib import Path

import pytest

from gravai.recording.session_audio_capture import (
    MAIN_TRACK_ID,
    AudioCaptureError,
    SessionAudioCapture,
    ensure_pulse_running,
)

pytestmark = pytest.mark.skipif(
    shutil.which("pulseaudio") is None or shutil.which("ffmpeg") is None,
    reason="needs pulseaudio and ffmpeg, which both Dockerfiles install",
)

# Long enough for ffmpeg to have written something, short enough to keep the
# suite quick: nothing here is measuring a meeting, only a capture.
_TONE_SECONDS = 3
_AUDIBLE_DBFS = -40.0


@pytest.fixture(autouse=True)
def pulse():
    ensure_pulse_running()


def _sinks() -> list[str]:
    listed = subprocess.run(
        ["pactl", "list", "short", "sinks"], capture_output=True, text=True
    )
    return [line.split("\t")[1] for line in listed.stdout.splitlines() if "\t" in line]


def _play_tone_into(sink_name: str) -> None:
    """Plays a tone as a process bound to `sink_name` by PULSE_SINK alone.

    Which is how the browser reaches its own recording's sink, and the only
    thing keeping two recordings from ending up in each other's file.
    """
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-re", "-f", "lavfi", "-i", f"sine=frequency=440:duration={_TONE_SECONDS}",
            "-f", "pulse", "default",
        ],
        env=dict(os.environ, PULSE_SINK=sink_name),
        capture_output=True,
        check=True,
    )


def _peak_dbfs(wav_path: str) -> float:
    with wave.open(wav_path, "rb") as wf:
        samples = array("h")
        samples.frombytes(wf.readframes(wf.getnframes()))
    peak = max((abs(sample) for sample in samples), default=0)
    return -120.0 if peak == 0 else 20.0 * math.log10(peak / 32768.0)


def _wav_seconds(wav_path: str) -> float:
    with wave.open(wav_path, "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def test_the_capture_leaves_what_slicing_goes_looking_for(tmp_path):
    """Slicing reads a directory, not an object, so the files are the contract."""
    with SessionAudioCapture("session-contract", str(tmp_path)) as capture:
        _play_tone_into(capture.sink_name)

    tracks = glob(str(tmp_path / "track_mainAudio*.wav"))
    assert len(tracks) == 1, "slicing rejects a directory with any other number"
    assert Path(tracks[0]).name == "track_mainAudio.wav"

    sidecar = json.loads((tmp_path / "session_sidecar.json").read_text())
    # `track_mainAudio.wav` -> `mainAudio`, the key slicing derives and looks up.
    key = Path(tracks[0]).stem.removeprefix("track_")
    assert key == MAIN_TRACK_ID
    track = sidecar["tracks"][key]
    assert track["wav_file"] == "track_mainAudio.wav"
    assert track["sample_rate"] == 48000 and track["channels"] == 1

    started = datetime.fromisoformat(track["started_at"])
    ended = datetime.fromisoformat(track["ended_at"])
    # The sidecar and the file have to agree about how long the recording was:
    # slicing turns event timestamps into offsets into this wav, so a sidecar
    # claiming more time than the file holds sends every cut past the end of it.
    claimed = (ended - started).total_seconds()
    assert claimed == pytest.approx(_wav_seconds(tracks[0]), abs=0.05)


def test_slicing_reads_back_what_the_capture_wrote(tmp_path):
    """The same check as above, made by the code that actually has to do it.

    Asserting the field names by hand only proves the test and the capture agree.
    This runs the slicer's own reader over the directory, which is what fails a
    real recording when the handover is wrong.
    """
    from gravai.slicing.slice import _extract_session_dto

    with SessionAudioCapture("session-handover", str(tmp_path)) as capture:
        _play_tone_into(capture.sink_name)

    started = datetime.fromisoformat(
        json.loads((tmp_path / "session_sidecar.json").read_text())
        ["tracks"][MAIN_TRACK_ID]["started_at"]
    )
    spoke_at = int(started.timestamp() * 1000) + 500
    (tmp_path / "vad_timeline.json").write_text(json.dumps({
        "meta": {"session_id": "session-handover", "meeting_url": "https://x.invalid",
                 "page_start_ms": spoke_at, "page_end_ms": spoke_at + 2000},
        "events": [
            {"type": "voice-level", "timestamp": spoke_at,
             "data": {"id": "p1", "participantName": "pedrohos",
                      "classCount": 1, "timestamp": spoke_at}},
            {"type": "voice-level", "timestamp": spoke_at + 2000,
             "data": {"id": "p1", "participantName": "pedrohos",
                      "classCount": 0, "timestamp": spoke_at + 2000}},
        ],
    }))

    session = _extract_session_dto(str(tmp_path))

    assert session.main_track_name == "track_mainAudio.wav"
    assert session.session_id == "session-handover"
    assert session.start_time == started
    # The speaking event has to land inside the audio, or every cut made from it
    # runs off the end of the file.
    assert session.start_time <= datetime.fromtimestamp(
        spoke_at / 1000, tz=session.start_time.tzinfo
    ) <= session.end_time


def test_a_recording_captures_what_its_own_browser_played(tmp_path):
    with SessionAudioCapture("session-audible", str(tmp_path)) as capture:
        _play_tone_into(capture.sink_name)

    assert _peak_dbfs(str(tmp_path / "track_mainAudio.wav")) > _AUDIBLE_DBFS


def test_two_recordings_at_once_do_not_hear_each_other(tmp_path):
    """The reason the sink is per recording and not per service."""
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    with SessionAudioCapture("session-first", str(first_dir)) as first, \
         SessionAudioCapture("session-second", str(second_dir)) as second:
        assert first.sink_name != second.sink_name
        _play_tone_into(first.sink_name)

    assert _peak_dbfs(str(first_dir / "track_mainAudio.wav")) > _AUDIBLE_DBFS
    assert _peak_dbfs(str(second_dir / "track_mainAudio.wav")) < -80.0


def test_the_sink_goes_away_with_the_recording(tmp_path):
    """A module left loaded holds a sink for as long as the daemon lives."""
    capture = SessionAudioCapture("session-tidy", str(tmp_path)).start()
    assert capture.sink_name in _sinks()

    capture.stop()

    assert capture.sink_name not in _sinks()


def test_a_capture_that_never_starts_leaves_no_sink_behind(tmp_path, monkeypatch):
    """Failing to record is not a reason to leak the sink it would have used."""
    monkeypatch.setattr(
        SessionAudioCapture,
        "_start_ffmpeg",
        lambda self: subprocess.Popen(["false"], stdin=subprocess.PIPE),
    )
    capture = SessionAudioCapture("session-doomed", str(tmp_path))

    with pytest.raises(AudioCaptureError, match="without recording anything"):
        capture.start()

    assert capture.sink_name not in _sinks()
