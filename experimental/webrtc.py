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

DEFAULT_SAMPLE_RATE = 48000
DEFAULT_CHANNELS = 1
MAX_OPUS_PAYLOAD_BYTES = 400
DEFAULT_WS_HOST = "127.0.0.1"
DEFAULT_WS_PORT = 8765

def _ensure_tshark_available() -> None:
    try:
        subprocess.run(["tshark", "-v"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except Exception as exc:
        raise RuntimeError("tshark is required for packet capture. Please install wireshark/tshark.") from exc

def start_packet_capture(pcap_path: str, interface: str = "any"):
    _ensure_tshark_available()
    print(f"Starting tshark capture to {pcap_path}")
    proc = subprocess.Popen([
        "tshark",
        "-i", interface,
        "-p",
        "-w", pcap_path,
        "-q",
        "-f", "udp",
    ])
    return proc

def _decode_opus_packet(decoder, payload: bytes) -> bytes | None:
    # Try common Opus frame sizes (in samples @ 48kHz)
    for frame_size in (960, 1920, 2880, 480, 240, 120):
        try:
            return decoder.decode(payload, frame_size, decode_fec=False)
        except Exception:
            continue
    return None

def _normalize_payload(p_type: str, payload: bytes) -> bytes | None:
    if p_type == "102":
        # RTX: first 2 bytes are original sequence number.
        return payload[2:] if len(payload) > 2 else None
    if p_type == "120":
        return _extract_red_primary(payload)
    return payload

def _extract_rtp_payload(udp_payload: bytes) -> bytes | None:
    # Minimal RTP header parsing to extract payload when tshark doesn't expose rtp.payload.
    if len(udp_payload) < 12:
        return None
    v_p_x_cc = udp_payload[0]
    version = v_p_x_cc >> 6
    if version != 2:
        return None
    padding = (v_p_x_cc >> 5) & 0x01
    extension = (v_p_x_cc >> 4) & 0x01
    csrc_count = v_p_x_cc & 0x0F
    header_len = 12 + (csrc_count * 4)
    if len(udp_payload) < header_len:
        return None
    if extension:
        if len(udp_payload) < header_len + 4:
            return None
        ext_len = int.from_bytes(udp_payload[header_len + 2:header_len + 4], "big") * 4
        header_len += 4 + ext_len
    if len(udp_payload) < header_len:
        return None
    payload = udp_payload[header_len:]
    if padding and payload:
        pad_len = payload[-1]
        if pad_len <= len(payload):
            payload = payload[:-pad_len]
    return payload if payload else None

def _extract_red_primary(payload: bytes) -> bytes | None:
    # RFC2198 RED: last block is primary. We return the primary block payload.
    if not payload:
        return None
    i = 0
    headers = []
    while True:
        if i >= len(payload):
            return None
        b = payload[i]
        f = (b & 0x80) != 0
        pt = b & 0x7F
        if f:
            if i + 4 > len(payload):
                return None
            block_len = ((payload[i + 2] & 0x03) << 8) | payload[i + 3]
            headers.append((pt, block_len))
            i += 4
        else:
            headers.append((pt, None))
            i += 1
            break
    data_start = i
    for _, blen in headers[:-1]:
        if blen is None:
            return None
        data_start += blen
    if data_start > len(payload):
        return None
    return payload[data_start:]

def decode_webrtc_audio_from_pcap(pcap_path: str, output_dir: str, keylog_path: str | None = None) -> List[str]:
    if not os.path.exists(pcap_path):
        raise RuntimeError(f"PCAP file not found: {pcap_path}")
    try:
        import opuslib  # type: ignore
    except Exception as exc:
        raise RuntimeError("opuslib is required to decode Opus RTP payloads.") from exc

    os.makedirs(output_dir, exist_ok=True)

    if keylog_path:
        if not os.path.exists(keylog_path):
            raise RuntimeError(f"SSL key log not found: {keylog_path}")
        if os.path.getsize(keylog_path) == 0:
            raise RuntimeError("SSL key log is empty; WebRTC SRTP cannot be decrypted.")

    payload_stats: Dict[str, Dict[str, int]] = {}

    def _tshark_count(filter_expr: str, use_keylog: bool) -> int:
        cmd = ["tshark", "-r", pcap_path, "-Y", filter_expr, "-T", "fields", "-e", "frame.number"]
        if use_keylog and keylog_path:
            cmd = ["tshark", "-o", f"tls.keylog_file:{keylog_path}"] + cmd[1:]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if result.returncode != 0:
            return 0
        return len([line for line in result.stdout.splitlines() if line.strip()])

    rtp_plain = _tshark_count("rtp", use_keylog=False)
    rtp_decrypted = _tshark_count("rtp", use_keylog=True)
    srtp_packets = _tshark_count("srtp", use_keylog=False)
    rtp_payloads = _tshark_count("rtp.payload", use_keylog=True)

    def _tshark_payload_types() -> str:
        cmd = ["tshark", "-r", pcap_path, "-Y", "rtp", "-T", "fields", "-e", "rtp.p_type"]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if result.returncode != 0:
            return "(unavailable)"
        types = {}
        for line in result.stdout.splitlines():
            pt = line.strip()
            if not pt:
                continue
            types[pt] = types.get(pt, 0) + 1
        if not types:
            return "(none)"
        return ", ".join([f"{k}:{v}" for k, v in sorted(types.items())])

    payload_types = _tshark_payload_types()

    def _probe_rtp_payloads() -> None:
        cmd = [
            "tshark",
            "-r", pcap_path,
            "-Y", "rtp",
            "-T", "fields",
            "-e", "rtp.ssrc",
            "-e", "rtp.p_type",
            "-e", "udp.length",
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if result.returncode != 0:
            print("Probe failed to read RTP payloads.")
            return
        stats: Dict[str, Dict[str, Dict[str, int]]] = {}
        for line in result.stdout.splitlines():
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) < 3:
                parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            ssrc, p_type, udp_len = parts[0], parts[1], parts[2]
            if not ssrc or not p_type or not udp_len:
                continue
            try:
                ulen = int(udp_len)
            except ValueError:
                continue
            pt_stats = stats.setdefault(ssrc, {}).setdefault(p_type, {"count": 0, "sum": 0, "min": 10**9, "max": 0})
            pt_stats["count"] += 1
            pt_stats["sum"] += ulen
            pt_stats["min"] = min(pt_stats["min"], ulen)
            pt_stats["max"] = max(pt_stats["max"], ulen)
        if not stats:
            print("Probe: No RTP packets found for payload analysis.")
            return
        print("RTP payload probe (udp.length) by SSRC/PT:")
        for ssrc, pts in stats.items():
            lines = []
            for pt, st in sorted(pts.items(), key=lambda x: x[0]):
                avg = st["sum"] // st["count"] if st["count"] else 0
                lines.append(f"pt {pt} count={st['count']} avg={avg} min={st['min']} max={st['max']}")
            print(f"  {ssrc}: " + "; ".join(lines))

    _probe_rtp_payloads()

    # Payload type 111 is commonly Opus in WebRTC. Teams often uses RED/RTX PTs too.
    tshark_cmd = [
        "tshark",
        "-r", pcap_path,
        "-Y", "rtp && (rtp.p_type == 102 || rtp.p_type == 111 || rtp.p_type == 120)",
        "-T", "fields",
        "-e", "rtp.ssrc",
        "-e", "rtp.seq",
        "-e", "rtp.timestamp",
        "-e", "rtp.p_type",
        "-e", "rtp.payload",
        "-e", "udp.payload",
        "-E", "separator=,",
        "-E", "occurrence=f",
    ]
    if keylog_path:
        tshark_cmd = ["tshark", "-o", f"tls.keylog_file:{keylog_path}"] + tshark_cmd[1:]

    result = subprocess.run(tshark_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"tshark failed to parse pcap: {result.stderr.strip()}")

    decoders: Dict[str, opuslib.Decoder] = {}
    wav_files: Dict[str, wave.Wave_write] = {}
    output_paths: List[str] = []
    packets_by_ssrc: Dict[str, List[Tuple[int, int, str, bytes]]] = {}
    decoded_frames: Dict[str, int] = {}

    def _get_writer(ssrc: str, channels: int) -> Tuple[opuslib.Decoder, wave.Wave_write, str]:
        if ssrc not in decoders:
            decoders[ssrc] = opuslib.Decoder(DEFAULT_SAMPLE_RATE, channels)
            out_path = os.path.join(output_dir, f"stream_{ssrc}.wav")
            wf = wave.open(out_path, "wb")
            wf.setnchannels(channels)
            wf.setsampwidth(2)
            wf.setframerate(DEFAULT_SAMPLE_RATE)
            wav_files[ssrc] = wf
            output_paths.append(out_path)
        return decoders[ssrc], wav_files[ssrc], output_paths[-1]

    for line in result.stdout.splitlines():
        if "," not in line:
            continue
        parts = line.split(",", 5)
        while len(parts) < 6:
            parts.append("")
        ssrc, seq, ts, p_type, rtp_payload_hex, udp_payload_hex = (p.strip() for p in parts)
        rtp_payload_hex = rtp_payload_hex.replace(":", "")
        udp_payload_hex = udp_payload_hex.replace(":", "")
        payload_hex = rtp_payload_hex
        if not payload_hex and udp_payload_hex:
            try:
                udp_payload = bytes.fromhex(udp_payload_hex)
            except ValueError:
                udp_payload = b""
            payload = _extract_rtp_payload(udp_payload) if udp_payload else None
            if payload:
                payload_hex = payload.hex()
        if not ssrc or not payload_hex or not seq:
            continue
        try:
            payload = bytes.fromhex(payload_hex)
        except ValueError:
            continue
        stats = payload_stats.setdefault(p_type or "(none)", {"count": 0, "sum": 0, "min": 10**9, "max": 0, "small": 0})
        plen = len(payload)
        stats["count"] += 1
        stats["sum"] += plen
        stats["min"] = min(stats["min"], plen)
        stats["max"] = max(stats["max"], plen)
        if plen <= MAX_OPUS_PAYLOAD_BYTES:
            stats["small"] += 1
        try:
            seq_num = int(seq)
            ts_num = int(ts)
        except ValueError:
            continue
        packets_by_ssrc.setdefault(ssrc, []).append((seq_num, ts_num, p_type, payload))

    for ssrc, packets in packets_by_ssrc.items():
        packets.sort(key=lambda p: p[0])
        # Choose mono vs stereo based on a small sample of packets.
        sample = packets[:20]
        mono_decoder = opuslib.Decoder(DEFAULT_SAMPLE_RATE, 1)
        stereo_decoder = opuslib.Decoder(DEFAULT_SAMPLE_RATE, 2)
        mono_ok = 0
        stereo_ok = 0
        for _, _, p_type, payload in sample:
            if len(payload) > MAX_OPUS_PAYLOAD_BYTES:
                continue
            payload = _normalize_payload(p_type, payload)
            if not payload:
                continue
            if _decode_opus_packet(mono_decoder, payload):
                mono_ok += 1
            if _decode_opus_packet(stereo_decoder, payload):
                stereo_ok += 1
        channels = 2 if stereo_ok > mono_ok else 1
        decoder, wf, _ = _get_writer(ssrc, channels)
        last_ts = None
        for _, ts_num, p_type, payload in packets:
            if len(payload) > MAX_OPUS_PAYLOAD_BYTES:
                continue
            payload = _normalize_payload(p_type, payload)
            if not payload:
                continue
            pcm = _decode_opus_packet(decoder, payload)
            if pcm:
                if last_ts is not None:
                    delta = ts_num - last_ts
                    if delta > 0:
                        # Insert silence for missing time to preserve duration.
                        silence_samples = min(delta, DEFAULT_SAMPLE_RATE * 10)
                        wf.writeframes(b"\x00\x00" * silence_samples)
                last_ts = ts_num + (len(pcm) // 2)
                wf.writeframes(pcm)
                decoded_frames[ssrc] = decoded_frames.get(ssrc, 0) + 1

    for wf in wav_files.values():
        wf.close()

    if not output_paths:
        if payload_stats:
            details = []
            for pt, st in sorted(payload_stats.items(), key=lambda x: x[0]):
                avg = st["sum"] // st["count"] if st["count"] else 0
                details.append(f"pt {pt} count={st['count']} avg={avg} min={st['min']} max={st['max']} small<=400={st['small']}")
            details_str = "; ".join(details)
        else:
            details_str = "(no payload stats)"
        raise RuntimeError(
            "No RTP audio payloads were decoded. "
            f"tshark counts - rtp(no keylog): {rtp_plain}, rtp(with keylog): {rtp_decrypted}, "
            f"rtp.payload: {rtp_payloads}, srtp: {srtp_packets}, payload types: {payload_types}. "
            f"payload stats: {details_str}. "
            "Check that SSLKEYLOGFILE is populated and that SRTP is decryptable."
        )

    if payload_stats and "111" not in payload_stats:
        print("Warning: No RTP payload type 111 (Opus) observed. Audio may be encoded in RED/RTX or non-audio RTP.")
    if decoded_frames:
        summary = ", ".join([f"{ssrc}:{count}" for ssrc, count in decoded_frames.items()])
        print(f"Decoded frame counts by SSRC: {summary}")

    return output_paths

def _meeting_origin(meeting_url: str) -> str:
    parsed = urlparse(meeting_url)
    return f"{parsed.scheme}://{parsed.netloc}"

def record_meeting(
    meeting_url: str,
    q: Queue,
    audio_output: str,
    debug: bool,
    pcap_output: str,
    keylog_path: str,
    interface: str,
):
    print("Hello from record-meeting!")

    # Start packet capture for WebRTC RTP traffic
    capture_proc = start_packet_capture(pcap_output, interface=interface)

    with sync_playwright() as p:
        os.environ["SSLKEYLOGFILE"] = keylog_path
        browser = p.chromium.launch(
            headless=True,
            ignore_default_args=["--mute-audio"],
            env={
                **os.environ,
                "SSLKEYLOGFILE": keylog_path,
            },
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
        
        print("Listening and capturing WebRTC packets...")
        q.put("start")
        # Wait and close browser

        try:
            span_locator = page.locator("span:has-text('Did you leave by mistake?')")
            span_locator.wait_for(state="visible", timeout=7_200_000) # Wait up to 2 hours
        except Exception:
            print("Meeting end span not detected, closing after timeout.")

        # Stop packet capture
        capture_proc.terminate()
        try:
            capture_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            capture_proc.kill()
            
        context.close()
        browser.close()

        q.put("stop")


def _load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_vad_timeline(path: str, meta: dict, events: list[dict]) -> None:
    payload = {
        "meta": meta,
        "events": events,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


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
    ws_url = f"ws://{ws_host}:{ws_port}"
    intercept_js = intercept_js.replace("{{WS_URL}}", ws_url).replace("{{WORKLET_CODE}}", json.dumps(worklet_js))
    vad_events: list[dict] = []
    vad_meta = {
        "session_id": session_id,
        "meeting_url": meeting_url,
        "page_start_ms": None,
        "page_end_ms": None,
    }

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
            context.add_init_script(intercept_js)
            context.add_init_script(vad_observer_js)
            context.grant_permissions([], origin=_meeting_origin(meeting_url))

            page = context.new_page()
            page.on("console", lambda msg: print(f"[page:{msg.type}] {msg.text}"))
            page.on("pageerror", lambda err: print(f"[page:error] {err}"))

            page.goto(meeting_url, wait_until="domcontentloaded")

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

            try:
                vad_meta["page_start_ms"] = page.evaluate("() => Date.now()")
                page.evaluate("() => { if (window.__vadSnapshotRoster) { window.__vadSnapshotRoster(); } }")
            except Exception as exc:
                print(f"[vad] failed to initialize timeline: {exc}")

            print("Listening and capturing per-track audio...")
            q.put("start")

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

            try:
                span_locator = page.locator("span:has-text('Did you leave by mistake?')")
                deadline = time.time() + 7_200
                while True:
                    _drain_vad_events()
                    try:
                        span_locator.wait_for(state="visible", timeout=1000)
                        break
                    except Exception:
                        if time.time() >= deadline:
                            print("Meeting end span not detected, closing after timeout.")
                            break
            except Exception:
                print("Meeting end span not detected, closing after timeout.")

            _drain_vad_events()
            try:
                vad_meta["page_end_ms"] = page.evaluate("() => Date.now()")
            except Exception as exc:
                print(f"[vad] failed to capture end time: {exc}")
            try:
                vad_path = os.path.join(output_dir, "vad_timeline.json")
                _write_vad_timeline(vad_path, vad_meta, vad_events)
                print(f"[vad] timeline written to {vad_path}")
            except Exception as exc:
                print(f"[vad] failed to write timeline: {exc}")

            context.close()
            browser.close()
    finally:
        ws_proc.terminate()
        try:
            ws_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ws_proc.kill()

    q.put("stop")


def main(
    meeting_url: str,
    output: str = "/tmp",
    debug: bool = False,
    interface: str = "any",
    pcap_input: str | None = None,
    mode: str = "intercept",
    ws_host: str = DEFAULT_WS_HOST,
    ws_port: int = DEFAULT_WS_PORT,
) -> str:
    try:
        mp.set_start_method('spawn', force=True)
    except RuntimeError:
        pass
    
    q = mp.Queue()

    if not output:
        output = "/tmp"
    os.makedirs(output, exist_ok=True)

    session_id = str(uuid4())
    output_file_path = f"{output}/{session_id}.wav"
    pcap_file_path = f"{output}/{session_id}.pcap"
    keylog_path = f"{output}/{session_id}.keylog"
    streams_output_dir = f"{output}/{session_id}_streams"
    tracks_output_dir = f"{output}/{session_id}_tracks"

    if pcap_input:
        print("Decoding host-captured RTP streams...")
        decode_webrtc_audio_from_pcap(pcap_input, streams_output_dir, keylog_path=None)
        return streams_output_dir

    if mode == "intercept":
        print(f"Starting intercept mode session {session_id} -> {tracks_output_dir}")
        p_join_browser = mp.Process(
            target=record_meeting_intercept,
            args=(meeting_url, q, tracks_output_dir, debug, session_id, ws_host, ws_port),
        )
        p_join_browser.start()
        p_join_browser.join()
        print("Finished")
        if q.get_nowait() == "start" and q.get_nowait() == "stop":
            return tracks_output_dir
        raise RuntimeError("Did not receive expected start/stop signals from recording process.")

    p_join_browser = mp.Process(
        target=record_meeting,
        args=(meeting_url, q, output_file_path, debug, pcap_file_path, keylog_path, interface),
    )
    # p_process_packages = mp.Process(target=retrieve_packages, args=('eth0', q,))

    p_join_browser.start()
    # p_process_packages.start()

    p_join_browser.join()
    print("Finished")
    if q.get_nowait() == "start" and q.get_nowait() == "stop":
        print("Decoding captured RTP streams...")
        decode_webrtc_audio_from_pcap(pcap_file_path, streams_output_dir, keylog_path=keylog_path)
        return streams_output_dir
    else:
        raise RuntimeError("Did not receive expected start/stop signals from recording process.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Record Meeting Application")
    parser.add_argument('--meeting_url', type=str, help='URL of the meeting to record', default="https://teams.live.com/meet/9389656018910?p=Ka7VjEoBlf2s9aXGXU")
    parser.add_argument('--output', type=str, help='Output file path')
    parser.add_argument('--interface', type=str, default="any", help='Network interface to capture packets')
    parser.add_argument('--pcap_input', type=str, help='Path to a host-captured pcap file to decode')
    parser.add_argument('--mode', type=str, default="intercept", choices=["intercept", "packet"], help='Capture mode')
    parser.add_argument('--ws_host', type=str, default=DEFAULT_WS_HOST, help='WS server host')
    parser.add_argument('--ws_port', type=int, default=DEFAULT_WS_PORT, help='WS server port')

    args = parser.parse_args()
    main(
        meeting_url=args.meeting_url,
        output=args.output,
        interface=args.interface,
        pcap_input=args.pcap_input,
        mode=args.mode,
        ws_host=args.ws_host,
        ws_port=args.ws_port,
    )
