"""Covers the per-recording audio server without needing a meeting.

Fake browser clients stand in for the injected page script: they speak the same
protocol (a `start` message, float32 frames, a `stop`), which is enough to prove
that two recordings running at once stay independent.
"""

import contextlib
import json
import math
import os
import subprocess
import sys
import time
import wave
from array import array

import pytest
from websockets.sync.client import connect

from gravai.recording.session_audio_server import AudioServerError, SessionAudioServer

_SAMPLE_RATE = 48000
_FRAME_SAMPLES = 4800  # 100ms per frame
_FRAMES_PER_TRACK = 5


def _tone(frequency: float, offset: int) -> bytes:
    samples = array(
        "f",
        (
            0.5 * math.sin(2 * math.pi * frequency * (offset + i) / _SAMPLE_RATE)
            for i in range(_FRAME_SAMPLES)
        ),
    )
    return samples.tobytes()


def _stream_track(ws_url: str, track_id: str, frequency: float = 440.0) -> None:
    """Sends one track the way the page's audio worklet does."""
    with connect(f"{ws_url}&track={track_id}&sr={_SAMPLE_RATE}&ch=1") as ws:
        ws.send(json.dumps({"type": "start", "trackId": track_id, "sampleRate": _SAMPLE_RATE, "channels": 1}))
        for frame in range(_FRAMES_PER_TRACK):
            ws.send(_tone(frequency, frame * _FRAME_SAMPLES))
        ws.send(json.dumps({"type": "stop", "trackId": track_id}))


def _read_sidecar(output_dir: str) -> dict:
    with open(os.path.join(output_dir, "session_sidecar.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def _wav_frames(path: str) -> int:
    with wave.open(path, "rb") as wf:
        return wf.getnframes()


@pytest.fixture
def session_dir(tmp_path):
    def _make(name: str) -> str:
        path = tmp_path / f"{name}_tracks"
        path.mkdir()
        return str(path)

    return _make


def test_records_tracks_and_finalizes_sidecar(session_dir):
    output_dir = session_dir("single")

    with SessionAudioServer("session-single", output_dir) as server:
        assert server.port > 0
        _stream_track(server.ws_url, "mainAudio-1")
        # Exit code 0 means it drained and shut itself down, rather than being
        # killed once the stop deadline ran out.
        assert server.stop() == 0

    sidecar = _read_sidecar(output_dir)
    assert sidecar["session_id"] == "session-single"
    track = sidecar["tracks"]["mainAudio-1"]
    # ended_at is what tells the slicer the audio is complete, and the server
    # only exits once it has written it.
    assert "started_at" in track and "ended_at" in track

    wav_path = os.path.join(output_dir, track["wav_file"])
    assert _wav_frames(wav_path) > 0


def test_simultaneous_recordings_stay_independent(session_dir):
    first_dir = session_dir("first")
    second_dir = session_dir("second")

    with SessionAudioServer("session-first", first_dir) as first, \
         SessionAudioServer("session-second", second_dir) as second:
        # The whole point of a server per recording: no shared port.
        assert first.port != second.port

        _stream_track(first.ws_url, "mainAudio-1", frequency=440.0)
        _stream_track(second.ws_url, "mainAudio-2", frequency=880.0)
        _stream_track(second.ws_url, "participant-a", frequency=220.0)

        # Stopping one recording must not disturb the other, so the second keeps
        # recording after the first is gone.
        first.stop()
        _stream_track(second.ws_url, "participant-b", frequency=330.0)

    assert set(_read_sidecar(first_dir)["tracks"]) == {"mainAudio-1"}
    assert set(_read_sidecar(second_dir)["tracks"]) == {
        "mainAudio-2",
        "participant-a",
        "participant-b",
    }

    first_tracks = {name for name in os.listdir(first_dir) if name.endswith(".wav")}
    assert first_tracks == {"track_mainAudio-1.wav"}
    for track in _read_sidecar(second_dir)["tracks"].values():
        assert "ended_at" in track
        assert _wav_frames(os.path.join(second_dir, track["wav_file"])) > 0


def test_rejects_a_client_of_another_session(session_dir):
    output_dir = session_dir("mismatch")

    with SessionAudioServer("session-owner", output_dir) as server:
        stranger_url = f"ws://{server.host}:{server.port}?session_id=someone-else"
        # The connection is closed on arrival, so whether the client notices
        # while sending is a race; what matters is that nothing was recorded.
        with contextlib.suppress(Exception):
            _stream_track(stranger_url, "stranger-track")

        _stream_track(server.ws_url, "mainAudio-1")

    assert set(_read_sidecar(output_dir)["tracks"]) == {"mainAudio-1"}
    assert {name for name in os.listdir(output_dir) if name.endswith(".wav")} == {
        "track_mainAudio-1.wav"
    }


def test_closes_a_track_that_never_disconnects(session_dir):
    """A browser that dies with its sockets open must not hold shutdown forever."""
    output_dir = session_dir("stuck")
    server = SessionAudioServer("session-stuck", output_dir, drain_timeout=2.0).start()

    ws = connect(f"{server.ws_url}&track=mainAudio-1&sr={_SAMPLE_RATE}&ch=1")
    try:
        ws.send(json.dumps({"type": "start", "trackId": "mainAudio-1", "sampleRate": _SAMPLE_RATE, "channels": 1}))
        ws.send(_tone(440.0, 0))
        # Never sends stop and never closes: the server has to give up waiting.
        assert server.stop() == 0
    finally:
        with contextlib.suppress(Exception):
            ws.close()

    track = _read_sidecar(output_dir)["tracks"]["mainAudio-1"]
    assert "ended_at" in track
    assert _wav_frames(os.path.join(output_dir, track["wav_file"])) > 0


def test_does_not_outlive_the_recording_that_started_it(session_dir):
    """A recording killed outright must not leave its audio server behind."""
    output_dir = session_dir("orphan")
    script = (
        "import os, sys\n"
        "from gravai.recording.session_audio_server import SessionAudioServer\n"
        "server = SessionAudioServer(sys.argv[1], sys.argv[2]).start()\n"
        "print(f'PID={server.pid}', flush=True)\n"
        "os._exit(0)\n"  # dies without stopping the server, like a SIGKILL would
    )
    started = subprocess.run(
        [sys.executable, "-c", script, "session-orphan", output_dir],
        capture_output=True,
        text=True,
        check=True,
    )
    # The server logs to the inherited stdout too, so pick out the marker line.
    server_pid = int(
        next(line for line in started.stdout.splitlines() if line.startswith("PID="))[4:]
    )

    deadline = time.time() + 30
    while _process_exists(server_pid):
        assert time.time() < deadline, f"Audio server {server_pid} outlived its recording"
        time.sleep(0.5)

    assert _read_sidecar(output_dir)["session_id"] == "session-orphan"


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_reports_a_port_it_cannot_bind(session_dir):
    output_dir = session_dir("taken")
    other_dir = session_dir("holder")

    with SessionAudioServer("session-holder", other_dir) as holder:
        taken_port = holder.port
        with pytest.raises(AudioServerError):
            SessionAudioServer("session-taken", output_dir, port=taken_port).start()
