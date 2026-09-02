import os
import json
from datetime import UTC, datetime

STOP_REQUEST_FILENAME = "stop.request"


def stop_request_path(session_dir: str) -> str:
    return os.path.join(session_dir, STOP_REQUEST_FILENAME)


def request_stop(session_dir: str, reason: str = "") -> str:
    """Asks the recording writing into session_dir to leave the meeting.

    Writing it for a session that has already finished is harmless and is not
    treated as an error - a stop that arrives a second too late is a race the
    caller cannot win, and the recording it was aimed at is already complete.
    """
    os.makedirs(session_dir, exist_ok=True)
    path = stop_request_path(session_dir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"requested_at": datetime.now(UTC).isoformat(), "reason": reason},
            f,
        )
    return path


def stop_requested(session_dir: str) -> bool:
    """Whether somebody has asked this recording to wind up."""
    return os.path.exists(stop_request_path(session_dir))


def stop_request_reason(session_dir: str) -> str | None:
    """The reason recorded with the request, when there is one to read."""
    try:
        with open(stop_request_path(session_dir), "r", encoding="utf-8") as f:
            return json.load(f).get("reason") or None
    except (OSError, json.JSONDecodeError):
        return None


def clear_stop_request(session_dir: str) -> None:
    """Removes the request, so a directory reused for a resume does not inherit it."""
    try:
        os.remove(stop_request_path(session_dir))
    except OSError:
        pass