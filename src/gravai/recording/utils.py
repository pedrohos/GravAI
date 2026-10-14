from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from gravai.models.events import VadEvent, VadMetadata


def _meeting_origin(meeting_url: str) -> str:
    parsed = urlparse(meeting_url)
    return f"{parsed.scheme}://{parsed.netloc}"

def _load_text(path: str | Path) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_vad_timeline(path: str, meta: VadMetadata, events: list[VadEvent]) -> None:
    payload = {
        "meta": meta,
        "events": events,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)