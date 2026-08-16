"""One audio server process per recording.

The previous design ran a single audio server for the whole application: one
fixed port, one event loop, one process lifetime shared by every recording and
multiplexed by session id. Two meetings recorded at once therefore shared a
socket and an interpreter, a stop meant for one recording stopped the others,
and the port could only ever be claimed once.

This starts one server per recording instead - its own process, its own
OS-assigned port, its own session directory - and stops it when that recording
ends. Concurrent recordings then have nothing to collide over.

    with SessionAudioServer(session_id, tracks_dir) as audio_server:
        ...  # inject audio_server.ws_url into the page and run the meeting
    # leaving the block drains and stops the server: every track is on disk
"""

import json
import os
import signal
import subprocess
import sys
import time
from urllib.parse import quote

from gravai.config.logging_config import get_logger
from gravai.recording.ws_audio_server import DEFAULT_DRAIN_TIMEOUT_S

# Written by the server once it is listening, read by us to learn its port.
READY_FILENAME = ".audio_server.json"

_STARTUP_TIMEOUT_S = 30.0
_STARTUP_POLL_INTERVAL_S = 0.05

# Past the server's own drain deadline, so a server that is finishing a track
# gets to finish it and only a stuck one is killed.
_KILL_GRACE_S = 15.0

#: Let the OS assign the port, which is what allows concurrent recordings.
EPHEMERAL_PORT = 0


class AudioServerError(RuntimeError):
    """Raised when a recording's audio server cannot be started."""


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


class SessionAudioServer:
    """Supervises the audio server process of a single recording session."""

    def __init__(
        self,
        session_id: str,
        output_dir: str,
        host: str = "127.0.0.1",
        port: int = EPHEMERAL_PORT,
        startup_timeout: float = _STARTUP_TIMEOUT_S,
        drain_timeout: float = DEFAULT_DRAIN_TIMEOUT_S,
    ):
        self.session_id = session_id
        self.output_dir = output_dir
        self.requested_host = host
        # A fixed port is only for debugging a single recording: the second
        # concurrent recording asking for the same one fails to bind.
        self.requested_port = port
        self.startup_timeout = startup_timeout
        self.drain_timeout = drain_timeout

        self._proc: subprocess.Popen | None = None
        self._host: str | None = None
        self._port: int | None = None
        self._ready_file = os.path.join(output_dir, READY_FILENAME)
        self._logger = get_logger("recording.audio_server", output_dir)

    @property
    def host(self) -> str:
        if self._host is None:
            raise AudioServerError("Audio server has not been started")
        return self._host

    @property
    def port(self) -> int:
        if self._port is None:
            raise AudioServerError("Audio server has not been started")
        return self._port

    @property
    def ws_url(self) -> str:
        """The url the page injects its audio into, for this session only."""
        return f"ws://{self.host}:{self.port}?session_id={quote(self.session_id, safe='')}"

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> "SessionAudioServer":
        if self._proc is not None:
            raise AudioServerError(f"Audio server for session {self.session_id} already started")

        os.makedirs(self.output_dir, exist_ok=True)
        # A file left behind by an earlier run in this directory would be read as
        # this run's port, pointing the browser at a socket nobody listens on.
        _remove_quietly(self._ready_file)

        self._proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "gravai.recording.ws_audio_server",
                "--host", self.requested_host,
                "--port", str(self.requested_port),
                "--output_dir", self.output_dir,
                "--session_id", self.session_id,
                "--ready_file", self._ready_file,
                "--drain_timeout", str(self.drain_timeout),
            ]
        )
        self._host, self._port = self._await_ready()
        self._logger.info(
            f"Audio server for session {self.session_id} ready on {self.ws_url} "
            f"(pid={self._proc.pid})"
        )
        return self

    def stop(self) -> int | None:
        """Stops the server, waiting for it to finish writing its tracks.

        Returns its exit code, or None when it was never started.
        """
        proc = self._proc
        if proc is None:
            return None

        if proc.poll() is None:
            # SIGTERM is the server's drain signal: it stops accepting and
            # finishes the tracks it is still writing before exiting.
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=self.drain_timeout + _KILL_GRACE_S)
            except subprocess.TimeoutExpired:
                self._logger.error(
                    f"Audio server for session {self.session_id} did not stop within "
                    f"{self.drain_timeout + _KILL_GRACE_S:.0f}s; killing it. Tracks it "
                    f"had not finished writing may be incomplete."
                )
                proc.kill()
                proc.wait()

        exit_code = proc.returncode
        if exit_code:
            self._logger.warning(
                f"Audio server for session {self.session_id} exited with code {exit_code}"
            )
        else:
            self._logger.info(f"Audio server for session {self.session_id} stopped")

        _remove_quietly(self._ready_file)
        self._proc = None
        return exit_code

    def _await_ready(self) -> tuple[str, int]:
        deadline = time.time() + self.startup_timeout
        while True:
            endpoint = self._read_ready_file()
            if endpoint is not None:
                return endpoint

            exit_code = self._proc.poll()  # type: ignore[union-attr]
            if exit_code is not None:
                self._proc = None
                raise AudioServerError(
                    f"Audio server for session {self.session_id} exited with code "
                    f"{exit_code} before it started listening. Check "
                    f"{os.path.join(self.output_dir, 'session.log')} and its stderr."
                )

            if time.time() >= deadline:
                # It never listened, so it has no tracks to finish: kill it
                # outright rather than granting it the drain window.
                proc, self._proc = self._proc, None
                proc.kill()  # type: ignore[union-attr]
                proc.wait()  # type: ignore[union-attr]
                _remove_quietly(self._ready_file)
                raise AudioServerError(
                    f"Audio server for session {self.session_id} did not report a port "
                    f"within {self.startup_timeout:.0f}s"
                )

            time.sleep(_STARTUP_POLL_INTERVAL_S)

    def _read_ready_file(self) -> tuple[str, int] | None:
        try:
            with open(self._ready_file, "r", encoding="utf-8") as f:
                ready = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        port = ready.get("port")
        if not port:
            return None
        return ready.get("host") or self.requested_host, int(port)

    def __enter__(self) -> "SessionAudioServer":
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stop()
