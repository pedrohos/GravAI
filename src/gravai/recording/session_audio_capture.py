"""The audio of one recording, taken from the operating system instead of the page.

Every audio defect this project has fought came from tapping the audio inside the
page: a receiver whose `enabled` Meet clears while it is not routing it, a remote
track that only feeds WebAudio while an element consumes it, a worklet blob URL
registered inside an opaque-origin frame that killed the renderer, a stereo track
written under a mono header, and a track timestamped by its socket rather than by
its first frame. All of them wrote a full-length wav of zeros, which reaches
transcription as a quiet meeting rather than as an error - see
`docs/audio-capture-options.md`.

So the audio is no longer taken from inside the browser. Each recording gets a
PulseAudio null sink of its own, the browser is bound to it through `PULSE_SINK`,
and ffmpeg records that sink's monitor for as long as the meeting lasts:

    with SessionAudioCapture(session_id, tracks_dir) as capture:
        ...  # launch the browser with capture.browser_env() and run the meeting
    # leaving the block stops ffmpeg and finalizes the wav: the track is on disk

`PULSE_SINK` is read by the PulseAudio client library when a process connects, so
it binds that browser and everything it spawns to that sink and nothing else
lands there. That is what keeps two recordings running at once from recording
each other, and it is the property the audio server had to be rewritten
per-session to get.

The handover to slicing is unchanged: one `track_mainAudio*.wav` and a
`session_sidecar.json` describing it, whose `started_at` shares a wall clock with
the timestamps in `vad_timeline.json`.
"""

import json
import math
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import wave
from array import array
from datetime import UTC, datetime, timedelta

from gravai.config.logging_config import get_logger

SIDECAR_FILENAME = "session_sidecar.json"

#: Slicing requires exactly one `track_mainAudio*.wav` in a session directory and
#: derives its sidecar key from the filename, so these two are the contract and
#: not a naming choice. See `_extract_session_dto` in slicing/slice.py.
MAIN_TRACK_ID = "mainAudio"
MAIN_TRACK_FILENAME = f"track_{MAIN_TRACK_ID}.wav"

#: What ffmpeg is told to write, and therefore what the sidecar announces.
SAMPLE_RATE = 48000
CHANNELS = 1
_SAMPLE_WIDTH_BYTES = 2
_WAV_HEADER_BYTES = 44

#: ffmpeg's own log, kept beside the audio: when a capture comes back empty this
#: is the only account of what Pulse told it.
CAPTURE_LOG_FILENAME = "audio_capture.log"

# A monitor source produces silence when nothing is playing, so samples appear
# as soon as ffmpeg is attached - this is a wait on a process starting, not on
# anybody speaking.
_FIRST_SAMPLES_TIMEOUT_S = 20.0
_PULSE_READY_TIMEOUT_S = 15.0
_POLL_INTERVAL_S = 0.05

_FFMPEG_QUIT_TIMEOUT_S = 20.0
_FFMPEG_INTERRUPT_TIMEOUT_S = 5.0

#: How much of the tail of the capture `capture_peak_dbfs` measures. Long enough
#: to cover a pause between sentences, short enough to say something about now.
_PEAK_WINDOW_S = 10.0

#: Peak under which the capture is taken to be carrying no speech, matching
#: `_SILENCE_PEAK_DBFS` in transcribe/base.py: a track that measures below this
#: is one whisper would be handed nothing but silence.
_AUDIBLE_PEAK_DBFS = -45.0

#: What a capture of pure digital silence reads as, rather than -inf.
_SILENCE_FLOOR_DBFS = -120.0


class AudioCaptureError(RuntimeError):
    """Raised when a recording's audio cannot be captured."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def _atomic_write_json(path: str, payload: dict) -> None:
    # The sidecar is rewritten when the track starts and again when it ends,
    # while the slicer may already be reading it. Write and rename, so a reader
    # never sees a truncated one.
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".tmp_", suffix=".json")
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


def _require_binaries() -> None:
    missing = [name for name in ("pulseaudio", "pactl", "ffmpeg") if shutil.which(name) is None]
    if missing:
        raise AudioCaptureError(
            f"Cannot record audio: {', '.join(missing)} is not installed. The meeting "
            f"audio is captured from a PulseAudio null sink with ffmpeg; both Dockerfiles "
            f"install them."
        )


def ensure_pulse_running(logger=None) -> None:
    """Brings the PulseAudio daemon up if it is not already.

    Ordering is the one hazard this design has: a browser that launches while
    there is no daemon binds a dummy device for its whole lifetime and plays
    into nothing, which is the same full-length file of silence the intercept
    used to produce. So this runs before the sink is created, and raises rather
    than letting a recording start against a daemon that is not there.

    `--exit-idle-time=-1` because nothing holds a connection to the daemon
    between recordings, and a daemon that has timed itself out in the gap is
    exactly the case above.
    """
    _require_binaries()
    if _run(["pulseaudio", "--check"]).returncode == 0:
        return

    # Running as root draws a warning on stderr and starts anyway, which is what
    # the container does; a failure to start shows up in --check below.
    started = _run(["pulseaudio", "--start", "--exit-idle-time=-1"])
    deadline = time.monotonic() + _PULSE_READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if _run(["pulseaudio", "--check"]).returncode == 0:
            if logger:
                logger.info("Started the PulseAudio daemon for this recording's audio")
            return
        time.sleep(_POLL_INTERVAL_S)

    raise AudioCaptureError(
        f"PulseAudio did not come up within {_PULSE_READY_TIMEOUT_S:.0f}s, so the "
        f"browser would play into a dummy device and the recording would be silence. "
        f"pulseaudio --start said: {(started.stderr or started.stdout).strip()!r}"
    )


def sink_name_for(session_id: str) -> str:
    """This session's sink, named so that it is obvious whose it is in `pactl`."""
    return "gravai_" + re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)


def _sink_index(sink_name: str) -> str | None:
    listed = _run(["pactl", "list", "short", "sinks"])
    for line in listed.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) >= 2 and fields[1] == sink_name:
            return fields[0]
    return None


def sink_applications(sink_name: str) -> list[str]:
    """Which processes are currently playing into this recording's sink.

    Empty while the browser is starting, and empty afterwards means the browser
    is not bound to this sink at all - it went to a dummy device, or Pulse came
    up after it did. That is the one way this design records silence, and it is
    observable from here, which the page-side tap never was.
    """
    index = _sink_index(sink_name)
    if index is None:
        return []
    listed = _run(["pactl", "list", "sink-inputs"])
    applications: list[str] = []
    on_this_sink = False
    name: str | None = None
    for line in listed.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Sink Input #"):
            on_this_sink = False
            name = None
        elif stripped.startswith("Sink:"):
            on_this_sink = stripped.split(":", 1)[1].strip() == index
        elif stripped.startswith("application.name = ") and on_this_sink and name is None:
            name = stripped.split("=", 1)[1].strip().strip('"')
            applications.append(name)
    return applications


def capture_peak_dbfs(wav_path: str, window_s: float = _PEAK_WINDOW_S) -> float | None:
    """Loudest sample in the last `window_s` of a capture, in dBFS.

    Reads the tail of the file rather than measuring the whole of it, so this can
    be asked repeatedly during a meeting that is hours long. None means there is
    nothing recorded yet.
    """
    frame_bytes = _SAMPLE_WIDTH_BYTES * CHANNELS
    try:
        size = os.path.getsize(wav_path)
    except OSError:
        return None
    available = size - _WAV_HEADER_BYTES
    if available < frame_bytes:
        return None

    wanted = int(window_s * SAMPLE_RATE) * frame_bytes
    read_bytes = min(available, wanted)
    # Whole frames only, or the samples come back interleaved by half a frame.
    read_bytes -= read_bytes % frame_bytes
    try:
        with open(wav_path, "rb") as f:
            f.seek(size - read_bytes)
            tail = f.read(read_bytes)
    except OSError:
        return None
    if not tail:
        return None

    samples = array("h")
    samples.frombytes(tail[: len(tail) - len(tail) % frame_bytes])
    if not samples:
        return None
    peak = max(abs(sample) for sample in samples)
    if peak == 0:
        return _SILENCE_FLOOR_DBFS
    return round(20.0 * math.log10(peak / 32768.0), 1)


def browser_env(sink_name: str | None, base=None) -> dict[str, str]:
    """`base` with the browser pointed at one recording's sink.

    Chrome is told where to play through the environment and not a flag, so this
    is what a launch has to be handed - and it has to be handed the whole
    environment, since Playwright replaces it rather than adding to it. A None
    sink means no capture was set up, which is the shape a test takes; the
    browser then plays wherever it would have.
    """
    environment = dict(base if base is not None else os.environ)
    if sink_name:
        environment["PULSE_SINK"] = sink_name
    return environment


def log_capture_status(sink_name: str, wav_path: str, logger, when: str) -> bool:
    """Logs what is playing into this recording's sink and what has been heard.

    Answers whether audio is arriving, so a caller can stop asking once it is.
    A recording of silence leaves no trace of itself - the file comes out full
    length either way - so the only way to tell a browser that never attached
    from a quiet room afterwards is to have asked at the time.
    """
    applications = sink_applications(sink_name)
    peak = capture_peak_dbfs(wav_path)
    logger.info(
        f"[audio-capture] {when}: sink={sink_name} "
        f"playing={applications or 'nothing'} "
        f"peak(last {_PEAK_WINDOW_S:.0f}s)={'unrecorded' if peak is None else f'{peak} dBFS'}"
    )
    if not applications:
        logger.warning(
            f"[audio-capture] nothing is playing into {sink_name} {when}: the browser is "
            f"not bound to this recording's sink, so it is being recorded as silence"
        )
    return peak is not None and peak > _AUDIBLE_PEAK_DBFS


class SessionAudioCapture:
    """The null sink and the ffmpeg process behind a single recording."""

    def __init__(self, session_id: str, output_dir: str):
        self.session_id = session_id
        self.output_dir = output_dir
        self.sink_name = sink_name_for(session_id)
        self.wav_path = os.path.join(output_dir, MAIN_TRACK_FILENAME)
        self.sidecar_path = os.path.join(output_dir, SIDECAR_FILENAME)

        self._logger = get_logger("recording.audio_capture", output_dir)
        self._module_id: str | None = None
        self._ffmpeg: subprocess.Popen | None = None
        self._capture_log = None
        self._session_start = _utc_now()
        self._started_at: datetime | None = None
        self._ended_at: datetime | None = None

    @property
    def is_running(self) -> bool:
        return self._ffmpeg is not None and self._ffmpeg.poll() is None

    def browser_env(self, base=None) -> dict[str, str]:
        """`base` with the browser pointed at this recording's sink."""
        return browser_env(self.sink_name, base)

    def start(self) -> "SessionAudioCapture":
        if self._ffmpeg is not None:
            raise AudioCaptureError(f"Audio capture for session {self.session_id} already started")

        os.makedirs(self.output_dir, exist_ok=True)
        ensure_pulse_running(self._logger)
        self._module_id = self._load_sink()
        try:
            self._ffmpeg = self._start_ffmpeg()
            self._started_at = self._await_first_samples()
        except BaseException:
            self._stop_ffmpeg()
            self._unload_sink()
            raise

        self._write_sidecar()
        self._logger.info(
            f"Capturing session {self.session_id} from {self.sink_name}.monitor into "
            f"{MAIN_TRACK_FILENAME} (pid={self._ffmpeg.pid}, started_at={self._started_at.isoformat()})"
        )
        return self

    def stop(self) -> None:
        """Stops the capture and finalizes the track, sidecar included."""
        if self._ffmpeg is None:
            return

        self._stop_ffmpeg()
        self._unload_sink()

        frames = self._frames_recorded()
        if self._started_at is not None:
            # From the file rather than from the clock: a wav whose duration and
            # whose sidecar disagree makes slicing look for speech at offsets the
            # audio never reaches, and the file is the thing that gets cut.
            self._ended_at = self._started_at + timedelta(seconds=frames / SAMPLE_RATE)
        self._write_sidecar()
        self._logger.info(
            f"Capture for session {self.session_id} finished: {frames / SAMPLE_RATE:.1f}s "
            f"in {MAIN_TRACK_FILENAME}"
        )
        self._ffmpeg = None

    def _load_sink(self) -> str:
        loaded = _run([
            "pactl", "load-module", "module-null-sink",
            f"sink_name={self.sink_name}",
            f"sink_properties=device.description={self.sink_name}",
        ])
        module_id = loaded.stdout.strip()
        if loaded.returncode != 0 or not module_id.isdigit():
            raise AudioCaptureError(
                f"Could not create the null sink {self.sink_name} this recording plays "
                f"into: {(loaded.stderr or loaded.stdout).strip()!r}"
            )
        return module_id

    def _unload_sink(self) -> None:
        if self._module_id is None:
            return
        # Unloaded even when the recording failed: a module left behind holds a
        # sink for as long as the daemon lives, and every recording loads one.
        unloaded = _run(["pactl", "unload-module", self._module_id])
        if unloaded.returncode != 0:
            self._logger.warning(
                f"Could not unload the null sink {self.sink_name} (module "
                f"{self._module_id}): {(unloaded.stderr or unloaded.stdout).strip()!r}"
            )
        self._module_id = None

    def _start_ffmpeg(self) -> subprocess.Popen:
        self._capture_log = open(os.path.join(self.output_dir, CAPTURE_LOG_FILENAME), "wb")
        return subprocess.Popen(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
                "-f", "pulse", "-i", f"{self.sink_name}.monitor",
                "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le",
                # Without this the wav trails the audio by a 32KB buffer - a
                # third of a second at this rate - and the first samples this
                # waits for below would land that late, putting `started_at`
                # after the audio it is meant to stamp.
                "-flush_packets", "1",
                self.wav_path,
            ],
            stdin=subprocess.PIPE,
            stdout=self._capture_log,
            stderr=self._capture_log,
        )

    def _stop_ffmpeg(self) -> None:
        proc = self._ffmpeg
        if proc is not None and proc.poll() is None:
            # 'q' is ffmpeg's own stop: it writes the wav header's real length
            # before exiting. Killing it instead leaves a header claiming zero
            # samples over a file full of them.
            try:
                if proc.stdin is not None:
                    proc.stdin.write(b"q")
                    proc.stdin.flush()
                    proc.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                proc.wait(timeout=_FFMPEG_QUIT_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                self._logger.error(
                    f"ffmpeg did not stop within {_FFMPEG_QUIT_TIMEOUT_S:.0f}s; interrupting it"
                )
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=_FFMPEG_INTERRUPT_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    self._logger.error("ffmpeg ignored the interrupt; killing it. The wav "
                                       "header may understate how much audio is in the file.")
                    proc.kill()
                    proc.wait()
        if self._capture_log is not None:
            self._capture_log.close()
            self._capture_log = None

    def _await_first_samples(self) -> datetime:
        """When the capture began, measured back from the samples on disk.

        A null sink's monitor carries silence when nobody is playing, so samples
        appear as soon as ffmpeg has attached - what is unknown is when that was,
        and taking the clock before the launch would claim audio that predates
        the file. Counting the frames already written and subtracting their
        duration gives the moment the first of them was recorded, which is what
        `started_at` has to mean for slicing's offsets to land.
        """
        deadline = time.monotonic() + _FIRST_SAMPLES_TIMEOUT_S
        while True:
            frames = self._frames_on_disk()
            if frames:
                return _utc_now() - timedelta(seconds=frames / SAMPLE_RATE)

            assert self._ffmpeg is not None
            if self._ffmpeg.poll() is not None:
                raise AudioCaptureError(
                    f"ffmpeg exited with code {self._ffmpeg.returncode} without recording "
                    f"anything from {self.sink_name}.monitor. See "
                    f"{os.path.join(self.output_dir, CAPTURE_LOG_FILENAME)}: {self._capture_tail()}"
                )
            if time.monotonic() >= deadline:
                raise AudioCaptureError(
                    f"No audio was recorded from {self.sink_name}.monitor within "
                    f"{_FIRST_SAMPLES_TIMEOUT_S:.0f}s. See "
                    f"{os.path.join(self.output_dir, CAPTURE_LOG_FILENAME)}: {self._capture_tail()}"
                )
            time.sleep(_POLL_INTERVAL_S)

    def _frames_on_disk(self) -> int:
        """Frames in the file as it grows, counted from its size.

        The header ffmpeg wrote at the start claims no samples at all until it
        rewrites it on exit, so `wave` cannot answer this while recording.
        """
        try:
            size = os.path.getsize(self.wav_path)
        except OSError:
            return 0
        return max(0, size - _WAV_HEADER_BYTES) // (_SAMPLE_WIDTH_BYTES * CHANNELS)

    def _frames_recorded(self) -> int:
        try:
            with wave.open(self.wav_path, "rb") as wf:
                frames = wf.getnframes()
        except (OSError, wave.Error):
            return self._frames_on_disk()
        # A killed ffmpeg leaves the placeholder header behind; the file itself
        # is still the better answer.
        return frames or self._frames_on_disk()

    def _capture_tail(self, lines: int = 5) -> str:
        try:
            with open(os.path.join(self.output_dir, CAPTURE_LOG_FILENAME), "r", errors="replace") as f:
                return " | ".join(f.read().splitlines()[-lines:]) or "(nothing logged)"
        except OSError:
            return "(no capture log)"

    def _write_sidecar(self) -> None:
        track: dict = {}
        if self._started_at is not None:
            track = {
                MAIN_TRACK_ID: {
                    "started_at": self._started_at.isoformat(),
                    "sample_rate": SAMPLE_RATE,
                    "channels": CHANNELS,
                    "wav_file": MAIN_TRACK_FILENAME,
                }
            }
            if self._ended_at is not None:
                track[MAIN_TRACK_ID]["ended_at"] = self._ended_at.isoformat()
        _atomic_write_json(
            self.sidecar_path,
            {
                "session_id": self.session_id,
                "session_start": self._session_start.isoformat(),
                "tracks": track,
            },
        )

    def __enter__(self) -> "SessionAudioCapture":
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stop()
