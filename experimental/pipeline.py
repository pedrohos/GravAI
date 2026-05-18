import argparse
import os
import subprocess
from typing import Optional

import general
import experimental.slice as slice_audio
import experimental.webrtc as webrtc


def _stop_process(proc: Optional[subprocess.Popen]) -> None:
    if not proc:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="Record meeting, capture VAD, and slice audio")
    parser.add_argument("--meeting_url", required=True, help="Teams meeting URL")
    parser.add_argument("--output_dir", default="/tmp", help="Output directory")
    parser.add_argument("--debug", action="store_true", help="Enable Playwright debug screenshots")
    parser.add_argument("--ws_host", default=webrtc.DEFAULT_WS_HOST)
    parser.add_argument("--ws_port", type=int, default=webrtc.DEFAULT_WS_PORT)
    parser.add_argument("--record_wav", action="store_true", help="Record mixed audio from system output")
    parser.add_argument("--audio_out", help="Path to mixed WAV output (when --record_wav)")
    parser.add_argument("--slice", action="store_true", help="Slice mixed audio using VAD")
    parser.add_argument("--min_duration", type=float, default=0.2, help="Minimum segment duration in seconds")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    audio_path = None
    ffmpeg_proc = None
    if args.record_wav:
        audio_path = args.audio_out or os.path.join(args.output_dir, "mixed_audio.wav")
        ffmpeg_proc = general.start_pulseaudio_and_ffmpeg(audio_path)

    try:
        tracks_dir = webrtc.main(
            meeting_url=args.meeting_url,
            output=args.output_dir,
            debug=args.debug,
            mode="intercept",
            ws_host=args.ws_host,
            ws_port=args.ws_port,
        )
    finally:
        _stop_process(ffmpeg_proc)

    vad_path = os.path.join(tracks_dir, "vad_timeline.json")
    if args.slice:
        if not audio_path:
            raise RuntimeError("--slice requires --record_wav or --audio_out")
        if not os.path.exists(vad_path):
            raise RuntimeError(f"VAD timeline not found: {vad_path}")
        speakers_dir = os.path.join(tracks_dir, "speakers")
        outputs = slice_audio.slice_audio_by_vad(
            audio_path,
            vad_path,
            speakers_dir,
            min_duration_s=args.min_duration,
        )
        print(f"Wrote {len(outputs)} speaker files to {speakers_dir}")

    print(f"Tracks and VAD written to {tracks_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
