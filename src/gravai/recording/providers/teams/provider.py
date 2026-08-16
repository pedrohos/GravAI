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

        get_logger("recording.teams", tracks_output_dir).info(
            f"Setup started at {datetime.now().isoformat()} for session {session_id}"
        )

        return session_id, tracks_output_dir, ctx, q

class TeamsMeetingRecorder(MeetingRecorder):
    rtc_intercept_js_path: Path | None = None
    vad_observer_js_path: Path | None = None
    audio_worklet_js_path: Path | None = None

    @model_validator(mode="after")
    def assemble_es_hosts(self) -> "TeamsMeetingRecorder":
        """Constructs the ES_HOSTS URL after model validation."""
        settings = get_settings()
        self.audio_worklet_js_path = self.audio_worklet_js_path or settings.AUDIO_WORKLET_JS_PATH
        self.rtc_intercept_js_path = self.rtc_intercept_js_path or settings.RTC_INTERCEPT_JS_PATH
        self.vad_observer_js_path = self.vad_observer_js_path or settings.VAD_OBSERVER_TEAMS_JS_PATH
        return self

    def prepare_injection(self, meeting_url: str, ws_url: str, session_id: str) -> tuple[str, str, list[dict], dict]:
        """Builds the scripts injected into the page, pointed at this session's
        audio server, which the caller has already started at ws_url."""
        worklet_path = self.audio_worklet_js_path
        intercept_path = self.rtc_intercept_js_path
        vad_observer_path = self.vad_observer_js_path

        assert worklet_path and intercept_path and vad_observer_path, "Expected worklet, intercept and vad observer paths to be set"

        worklet_js = _load_text(worklet_path)
        intercept_js = _load_text(intercept_path)
        vad_observer_js = _load_text(vad_observer_path)
        intercept_js = intercept_js.replace("{{WS_URL}}", ws_url).replace("{{WORKLET_CODE}}", json.dumps(worklet_js))
        vad_events: list[dict] = []
        vad_meta = {
            "session_id": session_id,
            "meeting_url": meeting_url,
            "page_start_ms": None,
            "page_end_ms": None,
        }

        return intercept_js, vad_observer_js, vad_events, vad_meta

    # Is run on a separate process
    def record_meeting(self, meeting_url: str, q: Queue, output_dir: str, debug: bool, intercept_js, vad_observer_js, vad_events: list[dict], vad_meta: dict):
        logger = get_logger("recording.teams", output_dir)
        try:
            with sync_playwright() as p:
                logger.info("Launching headless Chromium browser")
                browser = p.chromium.launch(
                    headless=True,
                    ignore_default_args=["--mute-audio"],
                    args=[
                        "--use-fake-ui-for-media-stream",
                        "--autoplay-policy=no-user-gesture-required",
                        "--enable-logging",
                        "--v=1",
                        "--vmodule=*webrtc*=3,*libjingle*=3",
                    ],
                )
                context = browser.new_context(bypass_csp=True, ignore_https_errors=True)
                context.add_init_script(intercept_js)
                context.add_init_script(vad_observer_js)
                context.grant_permissions([], origin=_meeting_origin(meeting_url))

                page = context.new_page()
                page.set_default_timeout(20_000)

                logger.info(f"Navigating to meeting URL: {meeting_url}")
                page.goto(meeting_url, wait_until="domcontentloaded")
                logger.info("Prejoin page DOM content loaded")

                try:
                    page.locator("prejoin-join-button").first.click(timeout=9000)
                    logger.info("Prejoin page stage: clicked prejoin-join-button")
                except Exception:
                    logger.info("Prejoin page stage: prejoin-join-button not present, skipping")
                if debug:
                    page.screenshot(path="prejoin.png")
                try:
                    page.locator(".btn.primary").first.click(timeout=3000)
                    logger.info("Prejoin page stage: clicked primary button")
                except Exception:
                    logger.info("Prejoin page stage: primary button not present, skipping")
                try:
                    if debug:
                        page.screenshot(path="prejoin2.png")
                    page.get_by_role("button", name="Continue without audio or video").first.click(timeout=8000)
                    logger.info("Prejoin page stage: clicked 'Continue without audio or video'")
                except Exception:
                    logger.info("Prejoin page stage: 'Continue without audio or video' not present, skipping")

                if debug:
                    page.screenshot(path="prejoin3.png")

                try:
                    input_box = page.locator('input[data-tid="prejoin-display-name-input"]')
                    input_box.wait_for(timeout=30000)
                except Exception:
                    input_box = page.locator("input").first

                input_box.click(timeout=10_000)
                input_box.fill("Bot de Gravação de Pedro Silva")
                logger.info("Prejoin page stage: display name filled in")

                try:
                    page.get_by_role("button", name="Join now").first.click(timeout=5000)
                    logger.info("Clicked 'Join now' button, waiting to enter the meeting room")
                except Exception:
                    logger.warning("Failed to click 'Join now' button, attempting keyboard fallback to submit join")
                    for _ in range(4):
                        page.keyboard.press("Tab")
                    page.keyboard.press("Enter")

                if debug:
                    page.screenshot(path="last_wait.png")

                try:
                    vad_meta["page_start_ms"] = page.evaluate("() => Date.now()")
                    page.evaluate("() => { if (window.__vadSnapshotRoster) { window.__vadSnapshotRoster(); } }")
                except Exception as exc:
                    logger.warning(f"[vad] failed to initialize timeline: {exc}")

                logger.info("Waiting to be admitted into the meeting room / listening for audio activity...")

                def _drain_vad_events() -> None:
                    try:
                        new_events = page.evaluate(
                            """
                            () => {
                                const events = Array.isArray(window.__vadEvents) ? window.__vadEvents : [];
                                window.__vadEvents = [];
                                return events;
                            }
                            """
                        )
                    except Exception:
                        return
                    if new_events:
                        vad_events.extend(new_events)

                # call_timer_locator = page.locator("span[data-tid='call-duration']")
                ended_span_locator = page.locator("span:has-text('Did you leave by mistake?')")
                removed_h1_locator = page.locator("h1:has-text(\"You've been removed from this meeting\")")
                retry_h1_locator = page.locator("h1[id='calling-retry-screen-title']")

                deadline = time.time() + 7_200
                activity_timeout = 600  # 10 minutes

                last_vad_event_time_update = time.time()
                last_vad_event_count = 0
                joined_meeting = False
                while True:
                    _drain_vad_events()
                    try:
                        # Updates last 'activity detected' timestamp. New events implies the meeting is still ongoing
                        # and the bot was not left by himself on the meeting
                        if len(vad_events) != last_vad_event_count:
                            if not joined_meeting:
                                logger.info("Audio activity detected: successfully joined the meeting and started recording")
                                joined_meeting = True
                            last_vad_event_time_update = time.time()
                            last_vad_event_count = len(vad_events)
                        # Checks for inactivity in VAD events to determine if the meeting has likely ended,
                        # as a fallback in case the end-of-meeting indicators are not detected
                        elif time.time() - last_vad_event_time_update > activity_timeout:
                            logger.info("No audio activity detected for a prolonged period, assuming meeting ended")
                            break
                        else:
                            # Check if the meeting was rejected by the host, which can happen if the bot is not allowed to join
                            try:
                                retry_h1_locator.wait_for(state="visible", timeout=200)
                                if not joined_meeting:
                                    logger.error("Entry to the meeting was denied by the host")
                                    raise Exception("Rejected from joining the meeting")
                            except Exception:
                                pass

                        # Check for meeting end indicators (the meeting ended or it was removed)
                        ended_span_locator.or_(removed_h1_locator).wait_for(state="visible", timeout=1000)
                        logger.info("Meeting end detected (left the meeting or was removed)")
                        break
                    except Exception as e:
                        if time.time() >= deadline:
                            logger.warning("Meeting end span not detected, closing after timeout.")
                            break

                _drain_vad_events()
                try:
                    vad_meta["page_end_ms"] = page.evaluate("() => Date.now()")
                except Exception as exc:
                    logger.warning(f"[vad] failed to capture end time: {exc}")
                try:
                    vad_path = os.path.join(output_dir, "vad_timeline.json")
                    _write_vad_timeline(vad_path, vad_meta, vad_events)
                    logger.info(f"[vad] timeline written to {vad_path}")
                except Exception as exc:
                    logger.warning(f"[vad] failed to write timeline: {exc}")

                context.close()
                browser.close()
                logger.info("Browser closed, recording session finished")

                q.put(("stop", None))
        except Exception as e:
            logger.exception(f"Error in recording process: {e}")
            q.put(("exception", str(e)))


# if __name__ == "__main__":
#     parser = argparse.ArgumentParser(description="Record Meeting Application")
#     parser.add_argument('--meeting_url', type=str, help='URL of the meeting to record')
#     parser.add_argument('--output', type=str, help='Output file path', default="res")
#     parser.add_argument('--ws_host', type=str, help='WS server host')
#     parser.add_argument('--ws_port', type=int, help='WS server port')

#     args = parser.parse_args()
