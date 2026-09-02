import os
import time
from multiprocessing import Queue
from pathlib import Path

from playwright.sync_api import sync_playwright
from pydantic import model_validator

from gravai.config.logging_config import get_logger
from gravai.config.settings import get_settings
from gravai.recording.session_audio_capture import (
    MAIN_TRACK_FILENAME,
    browser_env,
    log_capture_status,
)
from gravai.jobs.stop_signal import stop_requested
from gravai.recording.utils import _meeting_origin, _write_vad_timeline

from ..provider_base import MeetingRecorder


class TeamsMeetingRecorder(MeetingRecorder):
    vad_observer_js_path: Path | None = None

    @model_validator(mode="after")
    def assemble_es_hosts(self) -> "TeamsMeetingRecorder":
        """Constructs the ES_HOSTS URL after model validation."""
        settings = get_settings()
        self.vad_observer_js_path = self.vad_observer_js_path or settings.VAD_OBSERVER_TEAMS_JS_PATH
        return self

    # Is run on a separate process
    def record_meeting(self, meeting_url: str, q: Queue, output_dir: str, debug: bool, vad_observer_js, vad_events: list[dict], vad_meta: dict, audio_sink: str | None):
        logger = get_logger("recording.teams", output_dir)
        main_track_path = os.path.join(output_dir, MAIN_TRACK_FILENAME)
        try:
            with sync_playwright() as p:
                logger.info("Launching headless Chromium browser")
                browser = p.chromium.launch(
                    headless=True,
                    # PULSE_SINK is what puts this browser's audio into this
                    # recording's sink and nowhere else. Headless Chrome plays
                    # into Pulse exactly as a windowed one does - measured at
                    # the same level - so this stays headless.
                    env=browser_env(audio_sink),
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
                if audio_sink:
                    log_capture_status(audio_sink, main_track_path, logger, "on joining the call")

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
                    if stop_requested(output_dir):
                        logger.info(
                            "Stop was requested for this recording; leaving the meeting and "
                            "finalizing the audio"
                        )
                        break
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
                    except Exception:
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
