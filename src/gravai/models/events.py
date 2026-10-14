"""Typed messages crossing browser, recorder, and transcription boundaries."""

from typing import Any, Literal, TypedDict


class VadMetadata(TypedDict):
    session_id: str
    meeting_url: str
    page_start_ms: int | None
    page_end_ms: int | None


class VadEvent(TypedDict):
    type: str
    timestamp: int
    data: dict[str, Any]


class ParticipantEvent(TypedDict):
    class_count: int
    timestamp: int
    participant_name: str


class WhisperResponse(TypedDict, total=False):
    text: str
    segments: list[dict[str, Any]]


type RecorderResult = tuple[Literal["stop", "exception"], str | None]
