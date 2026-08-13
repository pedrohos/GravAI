from gravai.config.logging_config import get_logger, release_session_logs
from gravai.models.models import (
    ParticipantData,
    RecordingType,
    Session,
    TranscriptedSession,
)
from gravai.registry import UnsupportedMeetingURL, detect_recording_type
from gravai.recording.service import record_meeting as service_record_meeting
from gravai.slicing.service import slice_track as service_slice_track
from gravai.transcribe.service import (
    transcribe_meeting_tracks as service_transcribe_meeting_tracks,
)

# Re-exported so callers keep importing URL detection and its error from the
# pipeline; the registry owns both.
__all__ = ["UnsupportedMeetingURL", "detect_recording_type", "record",
           "record_and_transcribe", "transcribe_tracks"]

logger = get_logger("api.pipeline")


def record(
    meeting_url: str,
    slice_tracks: bool = True,
) -> tuple[str, str | None, Session[ParticipantData] | None]:
    recording_type = detect_recording_type(meeting_url)

    tracks_output_dir = service_record_meeting(recording_type, meeting_url)
    try:
        if not slice_tracks:
            return tracks_output_dir, None, None

        metadata_output_path, session = service_slice_track(recording_type, tracks_output_dir)
        return tracks_output_dir, metadata_output_path, session
    finally:
        release_session_logs(tracks_output_dir)


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
    try:
        metadata_output_path, session = service_slice_track(
            recording_type, tracks_output_dir, group_slices_by_name
        )
        transc_session = service_transcribe_meeting_tracks(session)
        return metadata_output_path, transc_session
    finally:
        # Closes this session's log file. Safe when reached from
        # record_and_transcribe: nothing logs against the session after this.
        release_session_logs(tracks_output_dir)
