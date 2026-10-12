from collections.abc import Callable

from gravai.config.logging_config import get_logger, release_session_logs
from gravai.config.registry import UnsupportedMeetingURL, detect_recording_type
from gravai.models.common import (
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

# Re-exported so callers keep importing URL detection and its error from the
# pipeline; the registry owns both.
__all__ = ["UnsupportedMeetingURL", "detect_recording_type", "record", "transcribe_tracks"]

logger = get_logger("api.pipeline")


def record(
    meeting_url: str,
    slice_tracks: bool = True,
    on_session_start: Callable[[str, str], None] | None = None,
) -> tuple[str, str | None, Session[ParticipantData] | None]:
    recording_type = detect_recording_type(meeting_url)

    tracks_output_dir = service_record_meeting(
        recording_type, meeting_url, on_session_start=on_session_start
    )
    try:
        if not slice_tracks:
            return tracks_output_dir, None, None

        metadata_output_path, session = service_slice_track(recording_type, tracks_output_dir)
        return tracks_output_dir, metadata_output_path, session
    finally:
        release_session_logs(tracks_output_dir)


def transcribe_tracks(
    tracks_output_dir: str,
    recording_type: RecordingType = RecordingType.TEAMS,
    group_slices_by_name: bool = True,
    on_sliced: Callable[[str, Session[ParticipantData]], None] | None = None,
) -> tuple[str, TranscriptedSession]:
    """Slices a recorded directory, then transcribes what came out of it.

    `on_sliced` runs in between, with everything slicing produced: the mixed
    track, one track per speaker and the turns behind them. That is the whole
    point of the hook - none of it depends on whisper, and whisper is the half
    of this that fails for reasons outside the recording, so a caller that
    catalogues at this point keeps the audio it already has when transcription
    goes on to fail. Called before transcription rather than after, because
    afterwards is exactly the moment that never arrives.
    """
    try:
        metadata_output_path, session = service_slice_track(
            recording_type, tracks_output_dir, group_slices_by_name
        )
        if on_sliced is not None:
            on_sliced(metadata_output_path, session)
        transc_session = service_transcribe_meeting_tracks(session)
        return metadata_output_path, transc_session
    finally:
        # Closes this session's log file. Safe when a job runs this straight
        # after record(): nothing logs against the session after this.
        release_session_logs(tracks_output_dir)
