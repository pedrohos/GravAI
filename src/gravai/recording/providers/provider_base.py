import multiprocessing as mp
import os
import queue
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from multiprocessing import Queue
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from gravai.config.logging_config import get_logger, release_session_logs
from gravai.config.settings import get_settings
from gravai.recording.session_audio_capture import SessionAudioCapture
from gravai.recording.utils import _load_text

# Only reached when the recording process died without reporting a result, so
# this is a short backstop before giving up - not a wait on the recording itself.
_RESULT_QUEUE_TIMEOUT_S = 30.0

class MeetingRecorder(BaseModel, ABC):
    vad_observer_js_path: Path | None

    # Is run on a separate process
    @abstractmethod
    def record_meeting(self, meeting_url: str, q: Queue, output_dir: str, debug: bool, vad_observer_js, vad_events: list[dict], vad_meta: dict, audio_sink: str | None):
        raise NotImplementedError("Subclasses must implement this method")

    def prepare_vad_observer(self, meeting_url: str, session_id: str) -> tuple[str, list[dict], dict]:
        """Script injected into the page to detect speaker activity and the timeline it fills.

        Meet and Teams only show who is speaking through an animated level indicator,
        which is readable at any useful resolution only from inside the page. See
        `docs/speaker-attribution-options.md`.
        """
        assert self.vad_observer_js_path, "Expected a vad observer path to be set"

        vad_events: list[dict] = []
        vad_meta = {
            "session_id": session_id,
            "meeting_url": meeting_url,
            "page_start_ms": None,
            "page_end_ms": None,
        }
        return _load_text(self.vad_observer_js_path), vad_events, vad_meta

    def record_meeting_with_audio_capture(
        self,
        meeting_url: str,
        output_dir: str | None = None,
        debug: bool = False,
        on_session_start: Callable[[str, str], None] | None = None,
    ) -> str:
        """Records one meeting, its audio taken from a sink of its own.

        The sink and the ffmpeg recording it are started before the browser and
        stopped after it, which is the order this design depends on: a browser
        that launches before there is a sink to bind to plays into a dummy
        device for the rest of its life and the recording is silence.

        on_session_start is handed the session id and its directory as soon as
        both exist, which is long before this returns - a meeting runs for as
        long as a meeting runs. That is the first moment anything outside can
        learn where this recording is writing, and so the only way to reach it
        while it is still going: to follow its log, to answer a CAPTCHA it is
        waiting on, or to ask it to leave the meeting. A callback that raises is
        not allowed to take the recording down with it.
        """
        settings = get_settings()

        session_id, tracks_output_dir, ctx, q = self.setup(output_dir or settings.SAVE_DIR)
        logger = get_logger("recording.teams", tracks_output_dir)
        logger.info(f"Setup complete for session {session_id} (meeting_url={meeting_url}, output_dir={tracks_output_dir})")

        if on_session_start is not None:
            try:
                on_session_start(session_id, tracks_output_dir)
            except Exception:
                logger.exception(
                    f"on_session_start failed for session {session_id}; the recording continues"
                )

        try:
            with SessionAudioCapture(
                session_id=session_id,
                output_dir=tracks_output_dir,
            ) as audio_capture:
                vad_observer_js, vad_events, vad_meta = self.prepare_vad_observer(
                    meeting_url, session_id
                )

                logger.info(f"Starting session {session_id} -> {tracks_output_dir}")
                p_join_browser = ctx.Process(
                    target=self.record_meeting,
                    args=(meeting_url, q, tracks_output_dir, debug, vad_observer_js, vad_events, vad_meta, audio_capture.sink_name),
                )

                p_join_browser.start()

                interrupted = False
                try:
                    p_join_browser.join()
                except KeyboardInterrupt:
                    logger.warning("Keyboard interrupt received, waiting for recording process to stop...")
                    interrupted = True
                    p_join_browser.join()

                try:
                    # A child that exits normally flushes its queue before terminating,
                    # so after join() the result is already there. The timeout only
                    # matters when the child was killed and will never report at all -
                    # blocking forever there would hang the request.
                    res_type, message = q.get(timeout=_RESULT_QUEUE_TIMEOUT_S)
                except queue.Empty:
                    exitcode = p_join_browser.exitcode
                    if interrupted:
                        raise RuntimeError(
                            f"Recording for session {session_id} was interrupted before it reported a result."
                        )
                    logger.error(
                        f"Recording process for session {session_id} exited with code {exitcode} "
                        f"without reporting a result"
                    )
                    raise RuntimeError(
                        f"Recording process died before reporting a result (exit code {exitcode}). "
                        f"Check {tracks_output_dir}/session.log and the process' stderr."
                    ) from None

                if res_type == "exception":
                    logger.error(f"Recording process raised an exception for session {session_id}: {message}")
                    raise RuntimeError(f"Recording process raised an exception: {message}")
                if res_type != "stop":
                    logger.error(
                        f"Recording process for session {session_id} reported an unexpected result: {res_type!r}"
                    )
                    raise RuntimeError(f"Unexpected result from recording process: {res_type!r}")

            # Leaving the block stopped ffmpeg, finalized the wav header and
            # stamped the sidecar's ended_at - so the directory returned here is
            # complete and ready to slice.
            logger.info(f"Recording finished successfully for session {session_id}")
            return tracks_output_dir
        except BaseException:
            # On success the pipeline releases this session's log file once slicing
            # and transcription are done. When recording fails the caller never
            # learns the directory, so it has to be released here.
            release_session_logs(tracks_output_dir)
            raise


    def setup(self, output_dir: str):
        # A context instead of set_start_method(force=True): the global start
        # method is process-wide, so setting it per recording would race with any
        # other recording starting at the same time.
        ctx = mp.get_context("spawn")
        q = ctx.Queue()

        if not output_dir:
            output_dir = "/tmp"
        os.makedirs(output_dir, exist_ok=True)

        session_prefix = datetime.today().strftime("%Y_%m_%d")
        session_id = f"{session_prefix}_{uuid4()!s}"

        tracks_output_dir = f"{output_dir}/{session_id}_tracks"

        get_logger("recording.base", tracks_output_dir).info(
            f"Setup started at {datetime.now().isoformat()} for session {session_id}"
        )

        return session_id, tracks_output_dir, ctx, q
