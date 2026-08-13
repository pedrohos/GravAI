from gravai.config.logging_config import get_logger
from gravai.models.models import (
    ParticipantData,
    RecordingType,
    Session,
    TranscriptedSession,
)
from gravai.recording.service import record_meeting as service_record_meeting
from gravai.slicing.service import slice_track as service_slice_track
from gravai.transcribe.service import (
    transcribe_meeting_tracks as service_transcribe_meeting_tracks,
)

logger = get_logger("api.pipeline")

# Substrings that identify which provider a meeting URL belongs to. Adding a
# provider means adding an entry here plus its branch in the services.
_URL_MARKERS: dict[RecordingType, tuple[str, ...]] = {
    RecordingType.TEAMS: ("teams.live.com", "teams.microsoft.com"),
    RecordingType.MEET: ("meet.google.com",),
}


class UnsupportedMeetingURL(ValueError):
    """Raised when no known provider matches the given meeting URL."""


def detect_recording_type(meeting_url: str) -> RecordingType:
    for recording_type, markers in _URL_MARKERS.items():
        if any(marker in meeting_url for marker in markers):
            return recording_type

    supported = ", ".join(
        marker for markers in _URL_MARKERS.values() for marker in markers
    )
    raise UnsupportedMeetingURL(
        f"No provider matches {meeting_url!r}. Supported meeting URLs contain: {supported}."
    )


def record(
    meeting_url: str,
    slice_tracks: bool = True,
) -> tuple[str, str | None, Session[ParticipantData] | None]:
    recording_type = detect_recording_type(meeting_url)

    tracks_output_dir = service_record_meeting(recording_type, meeting_url)
    if not slice_tracks:
        return tracks_output_dir, None, None

    metadata_output_path, session = service_slice_track(recording_type, tracks_output_dir)
    return tracks_output_dir, metadata_output_path, session


def record_and_transcribe(
    meeting_url: str,
    group_slices_by_name: bool = True,
) -> tuple[str, str, TranscriptedSession]:
    recording_type = detect_recording_type(meeting_url)

    tracks_output_dir = service_record_meeting(recording_type, meeting_url)
    metadata_output_path, transc_session = transcribe_tracks(
        tracks_output_dir,
        recording_type=recording_type,
        group_slices_by_name=group_slices_by_name,
    )
    return tracks_output_dir, metadata_output_path, transc_session


def transcribe_tracks(
    tracks_output_dir: str,
    recording_type: RecordingType = RecordingType.TEAMS,
    group_slices_by_name: bool = True,
) -> tuple[str, TranscriptedSession]:
    metadata_output_path, session = service_slice_track(
        recording_type, tracks_output_dir, group_slices_by_name
    )
    transc_session = service_transcribe_meeting_tracks(session)
    return metadata_output_path, transc_session
