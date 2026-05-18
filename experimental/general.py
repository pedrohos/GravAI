import argparse
import time
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
import multiprocessing as mp
from multiprocessing import Process, Queue
import queue
import os
import subprocess
from uuid import uuid4

# os.environ["SSLKEYLOGFILE"] = "/tmp/sslkeylogfile.log"

def start_pulseaudio_and_ffmpeg(output_file: str):
    # Stop any existing pulseaudio
    subprocess.run(["pkill", "-9", "pulseaudio"], stderr=subprocess.DEVNULL, check=False)
    
    # Start pulseaudio daemon if not running. We must use --system in root docker environments.
    subprocess.run(["pulseaudio", "--system", "-D"], check=False)
    time.sleep(2)
    
    # Create a virtual audio sink so Chromium has a valid "speaker" to play sound to
    subprocess.run(["pactl", "load-module", "module-null-sink", "sink_name=DummySink"], check=False)
    subprocess.run(["pactl", "set-default-sink", "DummySink"], check=False)
    subprocess.run(["pactl", "set-sink-volume", "DummySink", "100%"], check=False)
    
    # Start ffmpeg to record from the virtual sink monitor
    print(f"Starting ffmpeg to record system audio to {output_file}")
    proc = subprocess.Popen([
        "ffmpeg", "-y", 
        "-f", "pulse", 
        "-i", "DummySink.monitor", 
        "-c:a", "pcm_s16le",  # 16-bit PCM for WAV
        output_file
    ])
    return proc

def _meeting_origin(meeting_url: str) -> str:
    parsed = urlparse(meeting_url)
    return f"{parsed.scheme}://{parsed.netloc}"

def record_meeting(meeting_url: str, q: Queue, audio_output: str, debug: bool):
    print("Hello from record-meeting!")
    
    # Start FFmpeg recording system audio
    ffmpeg_proc = start_pulseaudio_and_ffmpeg(audio_output)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            ignore_default_args=["--mute-audio"],
            args=[
                "--use-fake-ui-for-media-stream",
                # Allow autoplay for media streams so headless doesn't reject playback
                "--autoplay-policy=no-user-gesture-required",
                "--enable-logging",
                "--v=1",
                "--vmodule=*webrtc*=3,*libjingle*=3",
            ]
        )
        context = browser.new_context()

        # Explicitly keep camera and mic blocked for this meeting origin.
        context.grant_permissions([], origin=_meeting_origin(meeting_url))

        page = context.new_page()
        
        page.goto(meeting_url, wait_until="domcontentloaded")


        # Try rejoin if it is the case.
        try:
            page.locator("prejoin-join-button").first.click(timeout=3000)
            # page.locator('css=button[data-focus-target="gum-continue"]').first.click(timeout=3000)
        except Exception:
            pass
        if debug:
            page.screenshot(path="prejoin.png")
        try:
            page.locator(".btn.primary").first.click(timeout=3000)
            # page.locator('css=button[data-focus-target="gum-continue"]').first.click(timeout=3000)
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
        
        print("Listening and recording system audio...")
        q.put("start")
        # Wait and close browser

        try:
            span_locator = page.locator("span:has-text('Did you leave by mistake?')")
            span_locator.wait_for(state="visible", timeout=7_200_000) # Wait up to 2 hours
        except Exception:
            print("Meeting end span not detected, closing after timeout.")

        # Stop ffmpeg
        ffmpeg_proc.terminate()
        try:
            ffmpeg_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ffmpeg_proc.kill()
            
        context.close()
        browser.close()

        q.put("stop")


def main(meeting_url: str, output: str = "/tmp", debug: bool = False) -> str:
    try:
        mp.set_start_method('spawn', force=True)
    except RuntimeError:
        pass
    
    q = mp.Queue()

    output_file_path = f"{output}/{str(uuid4())}.wav"

    p_join_browser = mp.Process(target=record_meeting, args=(meeting_url, q, output_file_path, debug))
    # p_process_packages = mp.Process(target=retrieve_packages, args=('eth0', q,))

    p_join_browser.start()
    # p_process_packages.start()

    p_join_browser.join()
    print("Finished")
    if q.get_nowait() == "start" and q.get_nowait() == "stop":
        return output_file_path
    else:
        raise RuntimeError("Did not receive expected start/stop signals from recording process.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Record Meeting Application")
    parser.add_argument('--meeting_url', type=str, help='URL of the meeting to record')
    parser.add_argument('--output', type=str, help='Output file path')

    args = parser.parse_args()
    main(meeting_url=args.meeting_url, output=args.output)
