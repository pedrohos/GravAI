import argparse
import time
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
import multiprocessing as mp
from multiprocessing import Process, Queue
import queue
import os
import subprocess
import sys
import json
from uuid import uuid4
import wave
from typing import Dict, Tuple, List
import json
from datetime import datetime

# Add this function to your code
def _load_observer_js() -> str:
    """Load the data-is-speaking observer JavaScript"""
    base_dir = os.path.dirname(__file__)
    observer_path = os.path.join(base_dir, "data_is_speaking_observer.js")
    with open(observer_path, "r", encoding="utf-8") as f:
        return f.read()

def _load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def _meeting_origin(meeting_url: str) -> str:
    parsed = urlparse(meeting_url)
    return f"{parsed.scheme}://{parsed.netloc}"

# Update your record_meeting_intercept function
def record_meeting_intercept(
    meeting_url: str,
    q: Queue,
    output_dir: str,
    debug: bool,
    session_id: str,
    ws_host: str,
    ws_port: int,
):
    print("Hello from record-meeting!")

    base_dir = os.path.dirname(__file__)
    worklet_path = os.path.join(base_dir, "static", "audio_worklet_processor.js")
    intercept_path = os.path.join(base_dir, "rtc_intercept.js")
    vad_observer_path = os.path.join(base_dir, "vad_observer.js")
    ws_server_path = os.path.join(base_dir, "ws_audio_server.py")
    speaking_observer_path = os.path.join(base_dir, "data_is_speaking_observer.js")

    os.makedirs(output_dir, exist_ok=True)
    ws_proc = subprocess.Popen([
        sys.executable,
        ws_server_path,
        "--host", ws_host,
        "--port", str(ws_port),
        "--output_dir", output_dir,
        "--session_id", session_id,
    ])

    worklet_js = _load_text(worklet_path)
    intercept_js = _load_text(intercept_path)
    vad_observer_js = _load_text(vad_observer_path)
    speaking_observer_js = _load_text(speaking_observer_path)
    
    ws_url = f"ws://{ws_host}:{ws_port}"
    intercept_js = intercept_js.replace("{{WS_URL}}", ws_url).replace("{{WORKLET_CODE}}", json.dumps(worklet_js))
    
    # Store speaking events
    speaking_events: list[dict] = []
    
    # Function to collect speaking events from page
    def collect_speaking_events(page):
        try:
            events = page.evaluate("""
                () => {
                    if (!window.__speakingEvents) {
                        window.__speakingEvents = [];
                        window.DataIsSpeakingObserver.on((eventType, data) => {
                            window.__speakingEvents.push({
                                type: eventType,
                                data: data,
                                timestamp: Date.now()
                            });
                        });
                    }
                    const events = window.__speakingEvents;
                    window.__speakingEvents = [];
                    return events;
                }
            """)
            return events if events else []
        except Exception as e:
            print(f"[speaking] Failed to collect events: {e}")
            return []
    
    try:
        with sync_playwright() as p:
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
            
            # Add all initialization scripts
            # context.add_init_script(intercept_js)
            context.add_init_script(vad_observer_js)
            context.add_init_script(speaking_observer_js)
            
            # Start the observer automatically
            context.add_init_script("""
                window.DataIsSpeakingObserver.start({ scanExisting: true });
                console.log('[Playwright] DataIsSpeakingObserver started');
            """)
            
            context.grant_permissions([], origin=_meeting_origin(meeting_url))

            page = context.new_page()
            page.on("console", lambda msg: print(f"[page:{msg.type}] {msg.text}"))
            page.on("pageerror", lambda err: print(f"[page:error] {err}"))

            page.goto(meeting_url, wait_until="domcontentloaded")

            # ... (rest of your join logic remains the same)
            try:
                page.locator("prejoin-join-button").first.click(timeout=3000)
            except Exception:
                pass
            if debug:
                page.screenshot(path="prejoin.png")
            try:
                page.locator(".btn.primary").first.click(timeout=3000)
            except Exception:
                pass
            try:
                if debug:
                    page.screenshot(path="prejoin2.png")
                page.get_by_role("button", name="Continue without audio or video").click(timeout=3000)
            except Exception:
                pass

            if debug:
                page.screenshot(path="prejoin3.png")

            try:
                input_box = page.locator('input[data-tid="prejoin-display-name-input"]')
                input_box.wait_for(timeout=30000)
            except Exception:
                input_box = page.locator("input").first

            input_box.click(timeout=10_000)
            input_box.fill("Bot de Gravação de Pedro Silva")

            try:
                page.get_by_role("button", name="Join now").click(timeout=5000)
            except Exception:
                for _ in range(4):
                    page.keyboard.press("Tab")
                page.keyboard.press("Enter")

            if debug:
                page.screenshot(path="last_wait.png")

            print("Listening and capturing per-track audio and speaking events...")
            q.put("start")

            # Collect speaking events periodically
            def _drain_speaking_events():
                events = collect_speaking_events(page)
                if events:
                    speaking_events.extend(events)
                    # Log interesting events
                    for event in events:
                        if event['type'] in ['created', 'destroyed']:
                            print(f"[speaking] Element {event['type']}: {event['data'].get('value', 'N/A')}")

            # Monitor for meeting end
            try:
                ended_span_locator = page.locator("span:has-text('Did you leave by mistake?')")
                removed_h1_locator = page.locator("h1:has-text(\"You've been removed from this meeting\")")

                deadline = time.time() + 7_200
                while True:
                    _drain_speaking_events()
                    try:
                        ended_span_locator.or_(removed_h1_locator).wait_for(state="visible", timeout=1000)
                        break
                    except Exception:
                        if time.time() >= deadline:
                            print("Meeting end span not detected, closing after timeout.")
                            break
            except Exception:
                print("Meeting end span not detected, closing after timeout.")

            # Final collection of events
            _drain_speaking_events()
            
            # Get final snapshot
            try:
                snapshot = page.evaluate("window.DataIsSpeakingObserver.getSnapshot()")
                speaking_meta = {
                    "session_id": session_id,
                    "meeting_url": meeting_url,
                    "total_elements_detected": len(speaking_events),
                    "current_elements": snapshot,
                    "timestamp": datetime.now().isoformat()
                }
                
                # Write speaking events to file
                speaking_path = os.path.join(output_dir, "speaking_events.json")
                with open(speaking_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "meta": speaking_meta,
                        "events": speaking_events
                    }, f, indent=2, default=str)
                print(f"[speaking] Events written to {speaking_path}")
                
                # Print summary
                created_count = len([e for e in speaking_events if e['type'] == 'created'])
                destroyed_count = len([e for e in speaking_events if e['type'] == 'destroyed'])
                changed_count = len([e for e in speaking_events if e['type'] == 'changed'])
                print(f"[speaking] Summary: {created_count} created, {destroyed_count} destroyed, {changed_count} changed")
                
            except Exception as exc:
                print(f"[speaking] Failed to get snapshot: {exc}")

            context.close()
            browser.close()
    finally:
        ws_proc.terminate()
        try:
            ws_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ws_proc.kill()

    q.put("stop")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Record meeting, capture VAD, and slice audio")
    parser.add_argument("--meeting_url", required=False, help="Teams meeting URL")
    parser.add_argument("--output_dir", default="/tmp", help="Output directory")
    parser.add_argument("--debug", action="store_true", help="Enable Playwright debug screenshots")
    parser.add_argument("--ws_host", default="localhost")
    parser.add_argument("--ws_port", type=int, default=2121)
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    session_id = str(uuid4())
    record_meeting_intercept(
        meeting_url=args.meeting_url or "https://teams.live.com/meet/9323077651245?p=I5z58SjgsoKbDZ6gIz",
        q=mp.Queue(),
        output_dir=args.output_dir or "/tmp",
        debug=args.debug,
        session_id=session_id,
        ws_host=args.ws_host,
        ws_port=args.ws_port,
    )