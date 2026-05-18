"""
Teams Per-User Audio Recorder
==============================
Joins a Teams meeting via the web browser (teams.microsoft.com),
hooks WebRTC at the browser level to intercept each participant's
individual MediaStream track, and saves a separate audio file per user.

No Microsoft proprietary API. No diarization. No screen capture mixing.

Requirements:
    pip install playwright websockets
    playwright install chromium

Usage:
    python recorder.py --url "https://teams.microsoft.com/l/meetup-join/..." \\
                       --name "Recorder Bot" \\
                       --out ./recordings
"""

import asyncio
import json
import os
import sys
import argparse
import signal
import logging
from pathlib import Path
from datetime import datetime

import websockets
from playwright.async_api import async_playwright
from playwright.sync_api import sync_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("teams_recorder")

# ── WebSocket server that receives audio chunks from the browser ──────────────

class AudioSession:
    """Holds an open file handle for one participant's audio stream."""
    def __init__(self, user_id: str, display_name: str, out_dir: Path):
        self.user_id = user_id
        self.display_name = display_name
        safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in display_name)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = out_dir / f"{safe}__{ts}.webm"
        self.fh = open(self.path, "wb")
        self.bytes_written = 0
        log.info(f"  + Started recording: {display_name!r}  →  {self.path.name}")

    def write(self, data: bytes):
        self.fh.write(data)
        self.bytes_written += len(data)

    def close(self):
        self.fh.close()
        log.info(
            f"  ✓ Closed: {self.display_name!r} "
            f"({self.bytes_written / 1024:.1f} KB)  →  {self.path}"
        )


class RecorderServer:
    """
    WebSocket server.  The injected browser script connects here and sends
    JSON control messages + binary audio blobs.

    Protocol (browser → server):
        Text frame  : {"type": "join",  "userId": "...", "name": "Alice"}
        Text frame  : {"type": "leave", "userId": "..."}
        Text frame  : {"type": "participants", "list": [{"userId":"...","name":"..."},...]}
        Text frame  : {"type": "stop"}
        Text frame  : {"type": "log",  "msg": "..."}
        Binary frame: first USER_ID_HEADER_LEN bytes = userId (UTF-8, null-padded),
                      remaining bytes = audio chunk (WebM/Opus)
    """
    USER_ID_HEADER_LEN = 64   # bytes reserved for userId prefix in binary frames

    def __init__(self, out_dir: Path, host: str = "127.0.0.1", port: int = 8765):
        self.out_dir = out_dir
        self.host = host
        self.port = port
        self.sessions: dict[str, AudioSession] = {}
        self._stop = asyncio.Event()

    # ── WebSocket handler ─────────────────────────────────────────────────────

    async def handler(self, ws):
        log.info("Browser connected to recorder WebSocket")
        try:
            async for message in ws:
                if isinstance(message, str):
                    await self._handle_text(message)
                elif isinstance(message, bytes):
                    self._handle_binary(message)
        except websockets.exceptions.ConnectionClosedOK:
            pass
        except Exception as exc:
            log.error(f"WebSocket error: {exc}")
        finally:
            log.info("Browser disconnected from WebSocket")

    async def _handle_text(self, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        t = msg.get("type")

        if t == "join":
            uid, name = msg["userId"], msg.get("name", "Unknown")
            if uid not in self.sessions:
                self.sessions[uid] = AudioSession(uid, name, self.out_dir)

        elif t == "leave":
            uid = msg["userId"]
            if uid in self.sessions:
                self.sessions[uid].close()
                del self.sessions[uid]

        elif t == "participants":
            for p in msg.get("list", []):
                uid, name = p["userId"], p.get("name", "Unknown")
                if uid not in self.sessions:
                    self.sessions[uid] = AudioSession(uid, name, self.out_dir)

        elif t == "stop":
            log.info("Received stop signal from browser")
            self._stop.set()

        elif t == "log":
            log.info(f"[browser] {msg.get('msg')}")

    def _handle_binary(self, data: bytes):
        header = data[: self.USER_ID_HEADER_LEN]
        audio  = data[self.USER_ID_HEADER_LEN :]
        uid = header.rstrip(b"\x00").decode("utf-8", errors="replace")
        if uid not in self.sessions:
            # Fallback: user track arrived before join message
            self.sessions[uid] = AudioSession(uid, uid, self.out_dir)
        self.sessions[uid].write(audio)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def run(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        async with websockets.serve(self.handler, self.host, self.port):
            log.info(f"Recorder WebSocket listening on ws://{self.host}:{self.port}")
            await self._stop.wait()

    def close_all(self):
        for s in list(self.sessions.values()):
            s.close()
        self.sessions.clear()


# ── JavaScript injected into Teams web ───────────────────────────────────────

def build_injected_js(ws_url: str) -> str:
    """
    JS injected via Playwright add_init_script (runs before any page JS).

    What it does:
      1. Opens a WebSocket back to Python.
      2. Patches RTCPeerConnection so every new peer connection is hooked.
      3. On each 'track' event, if audio, starts a MediaRecorder for that
         individual MediaStream and streams 500 ms chunks to Python.
      4. Polls the Teams DOM every 3 s to map stream/track IDs to names.
    """
    return f"""
(function() {{
  const WS_URL = "{ws_url}";
  const HEADER_LEN = 64;
  const MIME = "audio/webm;codecs=opus";

  let ws = null;
  const recorders = {{}};      // trackId -> MediaRecorder
  const trackNames = {{}};     // trackId -> display name

  // ── Utility ────────────────────────────────────────────────────────────────

  function sendJSON(obj) {{
    if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
  }}

  function log(msg) {{
    sendJSON({{ type: "log", msg }});
    console.log("[recorder]", msg);
  }}

  function makeHeader(userId) {{
    const buf = new Uint8Array(HEADER_LEN);
    const enc = new TextEncoder().encode(String(userId).slice(0, HEADER_LEN));
    buf.set(enc);
    return buf;
  }}

  function sendAudio(userId, blob) {{
    if (!ws || ws.readyState !== 1 || blob.size === 0) return;
    blob.arrayBuffer().then(ab => {{
      const header = makeHeader(userId);
      const audio  = new Uint8Array(ab);
      const frame  = new Uint8Array(HEADER_LEN + audio.byteLength);
      frame.set(header, 0);
      frame.set(audio,  HEADER_LEN);
      ws.send(frame.buffer);
    }});
  }}

  // ── DOM scraper: map stream IDs to participant names ───────────────────────
  // Teams web renders a roster/participant list in the sidebar.
  // Multiple selector strategies are tried for resilience across versions.

  const streamIdToName = {{}};

  function scrapeParticipants() {{
    const candidates = [];
    const selectors = [
      "[data-tid='roster-participant']",
      "[class*='participantItem']",
      "[class*='participant-item']",
      "[class*='calling-participant-item']",
      "[class*='remotevideocell']",
      "li[class*='participant']",
    ];
    const seen = new Set();
    for (const sel of selectors) {{
      try {{
        document.querySelectorAll(sel).forEach(el => {{
          // Extract the best name candidate from child text nodes
          const spans = el.querySelectorAll("span, div, p");
          let name = "";
          for (const sp of spans) {{
            const t = sp.textContent.trim();
            if (t && t.length > 1 && t.length < 60) {{ name = t; break; }}
          }}
          if (!name) name = el.textContent.trim().split("\\n")[0].trim();
          // Stable ID: prefer data attributes
          const uid = el.dataset?.userId
            || el.dataset?.participantId
            || el.dataset?.tid
            || el.id
            || name;
          if (name && uid && !seen.has(uid)) {{
            seen.add(uid);
            candidates.push({{ userId: uid, name }});
          }}
        }});
      }} catch(e) {{}}
    }}
    if (candidates.length) {{
      sendJSON({{ type: "participants", list: candidates }});
      candidates.forEach(c => {{ streamIdToName[c.userId] = c.name; }});
    }}
  }}

  setInterval(scrapeParticipants, 3000);

  // ── WebRTC hook ────────────────────────────────────────────────────────────

  function startTrackRecorder(track, streamId) {{
    if (recorders[streamId]) return;

    const stream = new MediaStream([track]);
    const mime   = MediaRecorder.isTypeSupported(MIME) ? MIME : "";
    const mr     = new MediaRecorder(stream, mime ? {{ mimeType: mime }} : {{}});

    recorders[streamId] = mr;

    mr.ondataavailable = e => sendAudio(streamId, e.data);
    mr.onerror         = e => log("MediaRecorder error: " + e.message);
    mr.start(500);

    const name = streamIdToName[streamId] || streamId;
    sendJSON({{ type: "join", userId: streamId, name }});
    log("Recording audio track for: " + name + " (id=" + streamId + ")");

    track.onended = () => {{
      try {{ mr.stop(); }} catch(e) {{}}
      delete recorders[streamId];
      sendJSON({{ type: "leave", userId: streamId }});
      log("Track ended for: " + (streamIdToName[streamId] || streamId));
    }};
  }}

  function hookPC(pc) {{
    pc.addEventListener("track", e => {{
      if (e.track.kind !== "audio") return;

      // Use the first stream's id as the stable key, fall back to track.id
      const sid = e.streams && e.streams.length > 0 ? e.streams[0].id : e.track.id;

      // Defer slightly so DOM scraper has a chance to populate names
      setTimeout(() => startTrackRecorder(e.track, sid), 800);
    }});
  }}

  // Monkey-patch RTCPeerConnection constructor
  const NativeRTC = window.RTCPeerConnection;
  window.RTCPeerConnection = function(...args) {{
    const pc = new NativeRTC(...args);
    hookPC(pc);
    return pc;
  }};
  // Preserve static methods & prototype chain
  Object.setPrototypeOf(window.RTCPeerConnection, NativeRTC);
  window.RTCPeerConnection.prototype = NativeRTC.prototype;
  window.RTCPeerConnection.generateCertificate = NativeRTC.generateCertificate.bind(NativeRTC);

  // ── WebSocket connection ───────────────────────────────────────────────────

  function connect() {{
    ws = new WebSocket(WS_URL);
    ws.binaryType = "arraybuffer";
    ws.onopen  = () => log("Connected to Python recorder (ws)");
    ws.onclose = () => {{ log("WS closed, reconnecting…"); setTimeout(connect, 2000); }};
    ws.onerror = e  => console.error("[recorder] WS error", e);
  }}

  connect();
  log("Teams per-user recorder hook installed");
}})();
"""


# ── Playwright: join the meeting ──────────────────────────────────────────────

async def join_meeting(meeting_url: str, display_name: str, ws_url: str, headless: bool, debug: bool = True):
    """Launch Chromium, grant mic access, navigate to Teams web, inject JS hook."""
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
        context.grant_permissions([], origin=meeting_url)

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
        # q.put("start")
        # Wait and close browser

        try:
            span_locator = page.locator("span:has-text('Did you leave by mistake?')")
            span_locator.wait_for(state="visible", timeout=7_200_000) # Wait up to 2 hours
        except Exception:
            print("Meeting end span not detected, closing after timeout.")

        # Stop ffmpeg
        # ffmpeg_proc.terminate()
        # try:
        #     ffmpeg_proc.wait(timeout=5)
        # except subprocess.TimeoutExpired:
        #     ffmpeg_proc.kill()
            
        context.close()
        browser.close()

        q.put("stop")


# ── Entry point ───────────────────────────────────────────────────────────────

async def main(args):
    out_dir = Path(args.out).resolve()
    ws_url  = f"ws://127.0.0.1:{args.port}"

    server = RecorderServer(out_dir=out_dir, port=args.port)

    server_task  = asyncio.create_task(server.run())
    browser_task = asyncio.create_task(
        join_meeting(args.url, args.name, ws_url, args.headless)
    )

    loop = asyncio.get_running_loop()

    def shutdown(*_):
        log.info("Shutdown signal received…")
        server.close_all()
        server_task.cancel()
        browser_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown)

    try:
        await asyncio.gather(server_task, browser_task, return_exceptions=True)
    finally:
        server.close_all()
        log.info(f"Done. Recordings saved to: {out_dir}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Teams per-user audio recorder (no MS API)")
    p.add_argument("--url",      required=False,        help="Teams meeting join URL", default="https://teams.live.com/meet/9389656018910?p=Ka7VjEoBlf2s9aXGXU")
    p.add_argument("--name",     default="Recorder",   help="Bot's display name in the meeting")
    p.add_argument("--out",      default="./recordings", help="Output directory for .webm files")
    p.add_argument("--port",     type=int, default=8765, help="Internal WebSocket port (default 8765)")
    p.add_argument("--headless", action="store_true",  help="Run Chromium headless (no window)")
    asyncio.run(main(p.parse_args()))