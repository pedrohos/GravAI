import argparse
import json
import os
import subprocess
from collections import defaultdict


def _sanitize_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)


def _load_vad(path: str) -> tuple[dict, list[dict]]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, dict) and "events" in payload:
        return payload.get("meta", {}) or {}, payload.get("events", []) or []
    if isinstance(payload, list):
        return {}, payload
    raise ValueError("VAD timeline must be a list or a dict with 'events'.")


def _extract_segments(
    events: list[dict],
    base_ms: int,
    end_ms: int | None,
    min_duration_s: float,
) -> dict[str, list[tuple[float, float]]]:
    segments: dict[str, list[tuple[float, float]]] = defaultdict(list)
    speaking_state: dict[str, float | None] = {}

    def _to_seconds(ts_ms: int) -> float:
        return max(0.0, (ts_ms - base_ms) / 1000.0)

    ordered = [e for e in events if isinstance(e, dict)]
    ordered.sort(key=lambda e: e.get("ts", 0))

    for event in ordered:
        name = event.get("name")
        speaking = event.get("speaking")
        ts = event.get("ts")
        if not name or speaking is None or ts is None:
            continue
        if not isinstance(ts, (int, float)):
            continue
        t_s = _to_seconds(int(ts))
        if speaking:
            if speaking_state.get(name) is None:
                speaking_state[name] = t_s
        else:
            start = speaking_state.get(name)
            if start is not None:
                if t_s - start >= min_duration_s:
                    segments[name].append((start, t_s))
                speaking_state[name] = None

    if end_ms is not None:
        end_s = _to_seconds(end_ms)
    else:
        last_ts = max((e.get("ts", 0) for e in ordered if isinstance(e.get("ts", None), (int, float))), default=0)
        end_s = _to_seconds(int(last_ts))

    for name, start in speaking_state.items():
        if start is None:
            continue
        if end_s - start >= min_duration_s:
            segments[name].append((start, end_s))

    return segments


def _build_filter(segments: list[tuple[float, float]]) -> str:
    parts = []
    for idx, (start, end) in enumerate(segments):
        parts.append(
            f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[s{idx}]"
        )
    concat_inputs = "".join(f"[s{idx}]" for idx in range(len(segments)))
    parts.append(f"{concat_inputs}concat=n={len(segments)}:v=0:a=1[outa]")
    return ";".join(parts)


def slice_audio_by_vad(
    audio_path: str,
    vad_path: str,
    output_dir: str,
    min_duration_s: float = 0.2,
) -> list[str]:
    meta, events = _load_vad(vad_path)
    base_ms = meta.get("page_start_ms") or meta.get("recording_start_ms")
    if base_ms is None:
        base_ms = min((e.get("ts", 0) for e in events if isinstance(e, dict)), default=0)
    base_ms = int(base_ms)
    end_ms = meta.get("page_end_ms")
    if end_ms is not None:
        end_ms = int(end_ms)

    segments_by_name = _extract_segments(events, base_ms, end_ms, min_duration_s)
    if not segments_by_name:
        return []

    os.makedirs(output_dir, exist_ok=True)
    outputs = []

    for name, segments in segments_by_name.items():
        if not segments:
            continue
        safe_name = _sanitize_name(name) or "unknown"
        output_path = os.path.join(output_dir, f"{safe_name}.wav")
        filter_complex = _build_filter(segments)
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            audio_path,
            "-filter_complex",
            filter_complex,
            "-map",
            "[outa]",
            output_path,
        ]
        subprocess.run(cmd, check=True)
        outputs.append(output_path)

    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Slice mixed audio by VAD timeline")
    parser.add_argument("--audio", required=True, help="Path to mixed audio WAV")
    parser.add_argument("--vad", required=True, help="Path to vad_timeline.json")
    parser.add_argument("--output_dir", required=True, help="Directory for per-speaker WAVs")
    parser.add_argument("--min_duration", type=float, default=0.2, help="Minimum segment duration in seconds")
    args = parser.parse_args()

    outputs = slice_audio_by_vad(args.audio, args.vad, args.output_dir, args.min_duration)
    print(f"Wrote {len(outputs)} speaker files to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
