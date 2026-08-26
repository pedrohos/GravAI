import queue
import time
from pathlib import Path
from playwright.sync_api import sync_playwright
import multiprocessing as mp
from multiprocessing import Queue
import os
import json
from uuid import uuid4
from pydantic import BaseModel, model_validator

from gravai.config.settings import get_settings
from gravai.config.logging_config import get_logger, release_session_logs
from abc import ABC, abstractmethod
from gravai.recording.session_audio_server import EPHEMERAL_PORT, SessionAudioServer
from gravai.recording.utils import _meeting_origin, _load_text, _write_vad_timeline
from datetime import datetime

# Only reached when the recording process died without reporting a result, so
# this is a short backstop before giving up - not a wait on the recording itself.
_RESULT_QUEUE_TIMEOUT_S = 30.0

class MeetingRecorder(BaseModel, ABC):
    rtc_intercept_js_path: Path | None
    vad_observer_js_path: Path | None
    audio_worklet_js_path: Path | None

    # Is run on a separate process
    @abstractmethod
    def record_meeting(self, meeting_url: str, q: Queue, output_dir: str, debug: bool, intercept_js, vad_observer_js, vad_events: list[dict], vad_meta: dict):
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def prepare_injection(self, meeting_url: str, ws_url: str, session_id: str) -> tuple[str, str, list[dict], dict]:
        raise NotImplementedError("Subclasses must implement this method")

    def record_meeting_with_audio_server(self, meeting_url: str, output_dir: str | None = None, ws_host: str | None = None, ws_port: int | None = None, debug: bool = False) -> str:
        """Records one meeting, backed by an audio server process of its own.

        The audio server is started before the browser and stopped after it, so
        the port injected into the page belongs to this session alone and no
        other recording is affected when this one starts or ends.
        """
        settings = get_settings()

        session_id, tracks_output_dir, ctx, q = self.setup(output_dir or settings.SAVE_DIR)
        logger = get_logger("recording.teams", tracks_output_dir)
        logger.info(f"Setup complete for session {session_id} (meeting_url={meeting_url}, output_dir={tracks_output_dir})")

        try:
            with SessionAudioServer(
                session_id=session_id,
                output_dir=tracks_output_dir,
                host=ws_host or settings.WS_HOST,
                port=ws_port if ws_port is not None else EPHEMERAL_PORT,
            ) as audio_server:
                # The intercept_js and vad_observer_js are modified with this session's
                # ws_url and injected in the browser; they are responsible for recording
                # and sending the audio packages to its audio server
                intercept_js, vad_observer_js, vad_events, vad_meta = self.prepare_injection(
                    meeting_url,
                    audio_server.ws_url,
                    session_id,
                )

                logger.info(f"Starting intercept mode session {session_id} -> {tracks_output_dir}")
                p_join_browser = ctx.Process(
                    target=self.record_meeting,
                    args=(meeting_url, q, tracks_output_dir, debug, intercept_js, vad_observer_js, vad_events, vad_meta),
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

            # Leaving the block stopped the audio server, which only exits once
            # every track it was writing is closed and re-encoded - so the
            # directory returned here is complete and ready to slice.
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
        session_id = f"{session_prefix}_{str(uuid4())}"

        tracks_output_dir = f"{output_dir}/{session_id}_tracks"

        get_logger("recording.base", tracks_output_dir).info(
            f"Setup started at {datetime.now().isoformat()} for session {session_id}"
        )

        return session_id, tracks_output_dir, ctx, q