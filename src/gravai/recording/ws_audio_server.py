"""The audio server behind a single recording.

One of these runs per recording, spawned by
`gravai.recording.session_audio_server.SessionAudioServer`. Everything it owns -
the listening socket, the session directory, the sidecar and the track writers -
belongs to exactly one session, so two recordings running at the same time share
nothing but the machine.

It runs as its own process rather than as a thread of the API for two reasons:
the audio path is CPU bound (float32 to PCM16 conversion for every frame of
every track), so a shared interpreter would make concurrent recordings fight
over one GIL; and a recording whose audio server hangs or dies takes down only
itself.

The port is chosen by the OS (`--port 0`) and reported back to the parent
through `--ready_file`, which is what lets any number of recordings listen at
the same time.
"""

import argparse
import asyncio
import json
import os
import signal
import tempfile
import threading
import wave
from array import array
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from websockets.asyncio.server import ServerConnection, serve

from gravai.config.logging_config import get_logger

SIDECAR_FILENAME = "session_sidecar.json"

# How long a track that is still streaming (or still re-encoding) may hold up
# shutdown after the parent asks the server to stop. The parent allows for this
# plus a margin before it resorts to killing the process.
DEFAULT_DRAIN_TIMEOUT_S = 180.0

# Second, much shorter wait: the tracks were told to close, and all that is left
# is their handlers returning.
_FORCED_CLOSE_TIMEOUT_S = 30.0

# How often to check that the recording that owns this server is still there.
_ORPHAN_CHECK_INTERVAL_S = 5.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)


def _atomic_write_json(path: str, payload: dict) -> None:
    # The sidecar is rewritten as tracks start and finish, while the slicer may
    # be reading it. Write to a temp file and rename so a reader never sees a
    # truncated one.
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(path) or ".", prefix=".tmp_", suffix=".json"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _float32_to_pcm16(payload: bytes) -> bytes:
    floats = array("f")
    floats.frombytes(payload)
    ints = array("h")
    for f in floats:
        if f > 1.0:
            f = 1.0
        elif f < -1.0:
            f = -1.0
        ints.append(int(f * 32767.0))
    return ints.tobytes()


class TrackWriter:
    def __init__(self, output_dir: str, track_id: str, sample_rate: int, channels: int):
        self.track_id = track_id
        self.sample_rate = 48000
        self.channels = channels
        safe_id = _sanitize_name(track_id)
        self.path = os.path.join(output_dir, f"track_{safe_id}.wav")
        self._wrote_any = False
        self._wf = wave.open(self.path, "wb")
        self._wf.setnchannels(channels)
        self._wf.setsampwidth(2)
        self._wf.setframerate(self.sample_rate)

    def write_float32(self, payload: bytes) -> bool:
        """Writes one chunk, and says whether it was the first one.

        The caller uses that to timestamp the track by its first audio rather
        than by its socket: the page opens the socket, then builds an
        AudioContext, loads the worklet module and resumes it, and only then does
        a frame arrive. Those seconds are missing from the file while the sidecar
        claims the recording started earlier, which shifts every offset slicing
        computes and cuts the beginning off each participant's first sentence.
        """
        first = not self._wrote_any
        self._wrote_any = True
        pcm16 = _float32_to_pcm16(payload)
        self._wf.writeframes(pcm16)
        return first

    def close(self) -> None:
        """Closes the file, and nothing else.

        This used to re-encode every track with `asetrate=96000,aresample=48000`
        to "fix slow-motion playback". The slow motion was self-inflicted: the
        page's worklet posted a remote track's two channels interleaved while the
        header declared mono, so every file came out twice as long, and doubling
        the rate on the way out hid it. The intercept now hands over one channel
        (see `monoCollector` in common/rtc_intercept.js) and the samples written
        here are the samples that were recorded.

        Keeping the correction after that fix would halve every track instead.
        """
        self._wf.close()


class SessionRecorder:
    """The one session this process records, and the sidecar describing it.

    The sidecar is the handover to slicing: it is rewritten whenever a track
    starts or finishes, and a track's `ended_at` is what tells the slicer that
    track's audio is complete.
    """

    def __init__(self, session_id: str, output_dir: str, logger):
        self.session_id = session_id
        self.output_dir = output_dir
        self.sidecar_path = os.path.join(output_dir, SIDECAR_FILENAME)
        self._logger = logger
        self._info: dict = {
            "session_id": session_id,
            "session_start": _utc_now(),
            "tracks": {},
        }
        # Sidecar writes happen on the event loop thread, so they are already
        # serialized; the lock keeps that true if a write ever moves off it.
        self._sidecar_lock = threading.Lock()
        self._write_sidecar()

    def open_track(self, track_id: str, sample_rate: int, channels: int) -> TrackWriter:
        writer = TrackWriter(self.output_dir, track_id, sample_rate, channels)
        self._info["tracks"][track_id] = {
            "started_at": _utc_now(),
            "sample_rate": sample_rate,
            "channels": channels,
            "wav_file": os.path.basename(writer.path),
        }
        self._write_sidecar()
        self._logger.info(
            f"Track {track_id} started (session={self.session_id} sr={sample_rate} "
            f"ch={channels} file={os.path.basename(writer.path)})"
        )
        return writer

    def mark_first_audio(self, track_id: str) -> None:
        """Moves a track's start to when its audio actually began.

        Between the socket opening and the first frame the page is still bringing
        up its audio graph, and that gap is silence the file never contains -
        leaving it in `started_at` makes slicing look for speech later in the
        file than where it is.
        """
        track = self._info["tracks"].get(track_id)
        if track is None:
            return
        opened_at = track["started_at"]
        track["started_at"] = _utc_now()
        self._write_sidecar()
        if opened_at != track["started_at"]:
            self._logger.info(
                f"Track {track_id} first audio at {track['started_at']} "
                f"(socket opened at {opened_at})"
            )

    async def close_track(self, track_id: str, writer: TrackWriter) -> None:
        # Off the event loop: closing only finalizes a wav header now, but it is
        # still file I/O on a loop shared with every other track of this session.
        await asyncio.to_thread(writer.close)
        track = self._info["tracks"].setdefault(track_id, {})
        track["ended_at"] = _utc_now()
        self._write_sidecar()
        self._logger.info(f"Track {track_id} finished (session={self.session_id})")

    def _write_sidecar(self) -> None:
        with self._sidecar_lock:
            _atomic_write_json(self.sidecar_path, self._info)


def _connection_params(ws: ServerConnection) -> tuple[str | None, str | None, int, int]:
    path = getattr(ws, "path", None)
    if path is None and hasattr(ws, "request"):
        path = ws.request.path
    params = parse_qs(urlparse(path or "").query)
    return (
        params.get("session_id", [None])[0],
        params.get("track", [None])[0],
        int(params.get("sr", ["48000"])[0]),
        int(params.get("ch", ["1"])[0]),
    )


async def _handle_connection(ws: ServerConnection, session: SessionRecorder, logger) -> None:
    """Writes one track for the lifetime of one browser-side websocket.

    The track can be declared either in the URL or in a `start` message, since
    the page opens the socket before the worklet reports the audio format.
    """
    client_session_id, track_id, sample_rate, channels = _connection_params(ws)
    if client_session_id and client_session_id != session.session_id:
        # Only this recording's browser knows the port, so this means the wrong
        # url was injected. Recording it here would file another session's audio
        # under this one.
        logger.error(
            f"Rejecting connection for session {client_session_id!r}: this server "
            f"records {session.session_id!r}"
        )
        await ws.close(code=1008, reason="session mismatch")
        return

    writer = None
    if track_id:
        writer = session.open_track(track_id, sample_rate, channels)

    try:
        async for message in ws:
            if isinstance(message, str):
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if payload.get("type") == "start" and writer is None:
                    track_id = payload.get("trackId") or payload.get("track_id")
                    if not track_id:
                        continue
                    sample_rate = int(payload.get("sampleRate", sample_rate))
                    channels = int(payload.get("channels", channels))
                    writer = session.open_track(track_id, sample_rate, channels)
                elif payload.get("type") == "stop":
                    break
                continue

            if writer is not None:
                if writer.write_float32(message) and track_id is not None:
                    session.mark_first_audio(track_id)
    finally:
        if writer is not None and track_id is not None:
            await session.close_track(track_id, writer)


async def _serve(
    host: str,
    port: int,
    session: SessionRecorder,
    ready_file: str | None,
    drain_timeout: float,
    logger,
) -> None:
    stop_requested = asyncio.Event()
    _install_stop_handlers(stop_requested)
    orphan_watch = asyncio.create_task(_stop_when_orphaned(stop_requested, logger))

    async def handler(ws: ServerConnection) -> None:
        await _handle_connection(ws, session, logger)

    async with serve(handler, host, port, max_size=None) as server:
        bound_host, bound_port = server.sockets[0].getsockname()[:2]
        logger.info(
            f"Audio server for session {session.session_id} listening on "
            f"ws://{bound_host}:{bound_port} -> {session.output_dir}"
        )
        if ready_file:
            # The parent blocks until this lands: it is how it learns the port
            # the OS handed out, and that the socket is accepting connections.
            _atomic_write_json(
                ready_file,
                {
                    "session_id": session.session_id,
                    "host": bound_host,
                    "port": bound_port,
                    "pid": os.getpid(),
                },
            )

        await stop_requested.wait()
        orphan_watch.cancel()

        open_tracks = len(server.connections)
        logger.info(f"Stop requested with {open_tracks} track connection(s) open")
        # Stop accepting, but let tracks that are still streaming - or still
        # being re-encoded - finish, so no wav is truncated by shutdown.
        server.close(close_connections=False)
        try:
            await asyncio.wait_for(server.wait_closed(), drain_timeout)
        except TimeoutError:
            logger.error(
                f"Tracks still open {drain_timeout:.0f}s after stop was requested; "
                f"closing them now, their audio may be incomplete"
            )
            await _force_close(server, logger)

    logger.info(f"Audio server for session {session.session_id} stopped")


def _install_stop_handlers(stop_requested: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_requested.set)
        except NotImplementedError:
            # Not available off Unix; the handler then runs outside the loop.
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop_requested.set))


async def _stop_when_orphaned(stop_requested: asyncio.Event, logger) -> None:
    """Shuts down if the recording that started this server is gone.

    One server per recording means one orphan per recording that was killed
    outright (a SIGKILLed API, a crashed worker) - each holding a port and an
    open wav for as long as the machine is up. Getting reparented is the signal
    that nobody is coming back to stop this one.
    """
    parent_pid = os.getppid()
    while True:
        await asyncio.sleep(_ORPHAN_CHECK_INTERVAL_S)
        if os.getppid() != parent_pid:
            logger.warning(
                f"Recording process {parent_pid} is gone; stopping and writing out "
                f"whatever has been recorded"
            )
            stop_requested.set()
            return


async def _force_close(server, logger) -> None:
    await asyncio.gather(
        *(connection.close(code=1001) for connection in list(server.connections)),
        return_exceptions=True,
    )
    try:
        await asyncio.wait_for(server.wait_closed(), _FORCED_CLOSE_TIMEOUT_S)
    except TimeoutError:
        logger.error("Track handlers did not return after their connections were closed")


def run_server(
    host: str,
    port: int,
    output_dir: str,
    session_id: str,
    ready_file: str | None = None,
    drain_timeout: float = DEFAULT_DRAIN_TIMEOUT_S,
) -> None:
    os.makedirs(output_dir, exist_ok=True)
    logger = get_logger("recording.audio_server", output_dir)
    session = SessionRecorder(session_id, output_dir, logger)
    try:
        asyncio.run(_serve(host, port, session, ready_file, drain_timeout, logger))
    except Exception:
        logger.exception(f"Audio server for session {session_id} failed")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audio server for one recording session")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port", type=int, default=0, help="0 lets the OS pick a free port (default)"
    )
    parser.add_argument("--output_dir", required=True, help="This session's tracks directory")
    parser.add_argument("--session_id", required=True)
    parser.add_argument(
        "--ready_file", help="Where to report the bound host/port once listening"
    )
    parser.add_argument("--drain_timeout", type=float, default=DEFAULT_DRAIN_TIMEOUT_S)
    args = parser.parse_args()
    run_server(
        args.host,
        args.port,
        args.output_dir,
        args.session_id,
        args.ready_file,
        args.drain_timeout,
    )
