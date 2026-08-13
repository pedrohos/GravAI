from contextlib import contextmanager

from fastapi import APIRouter, HTTPException

from gravai.api import pipeline
from gravai.api.schemas import RecordMeetingResponse, TranscribeResponse
from gravai.config.logging_config import get_logger

logger = get_logger("api.meetings")

router = APIRouter(tags=["meetings"])


@contextmanager
def _handled(operation: str, target: str):
    """Maps pipeline failures onto status codes, so every route reports them alike."""
    try:
        yield
    except HTTPException:
        raise
    except pipeline.UnsupportedMeetingURL as exc:
        logger.warning(f"{operation} rejected {target}: {exc}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotImplementedError as exc:
        logger.warning(f"{operation} unsupported for {target}: {exc}")
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(f"{operation} failed for {target}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/record_meeting", response_model=RecordMeetingResponse)
def record_meeting(meeting_url: str, slice_tracks: bool = True) -> RecordMeetingResponse:
    logger.info(f"Received /record_meeting request for {meeting_url} (slice_tracks={slice_tracks})")

    with _handled("/record_meeting", meeting_url):
        tracks_output_dir, metadata_output_path, session = pipeline.record(
            meeting_url, slice_tracks
        )

    logger.info(f"/record_meeting completed for {meeting_url} -> {tracks_output_dir}")
    return RecordMeetingResponse(
        recording_path=tracks_output_dir,
        session_metadata_path=metadata_output_path,
        session=session,
    )


@router.post("/record_meeting_and_transcribe", response_model=TranscribeResponse)
def record_meeting_and_transcribe(
    meeting_url: str, group_slices_by_name: bool = True
) -> TranscribeResponse:
    logger.info(f"Received /record_meeting_and_transcribe request for {meeting_url}")

    with _handled("/record_meeting_and_transcribe", meeting_url):
        tracks_output_dir, metadata_output_path, transc_session = pipeline.record_and_transcribe(
            meeting_url, group_slices_by_name
        )

    logger.info(f"/record_meeting_and_transcribe completed for {meeting_url} -> {tracks_output_dir}")
    return TranscribeResponse(
        recording_path=tracks_output_dir,
        session_metadata_path=metadata_output_path,
        transcribed_slices_session=transc_session,
    )


@router.post("/transcribe", response_model=TranscribeResponse)
def transcribe(tracks_output_dir: str, group_slices_by_name: bool = True) -> TranscribeResponse:
    logger.info(f"Received /transcribe request for {tracks_output_dir}")

    with _handled("/transcribe", tracks_output_dir):
        metadata_output_path, transc_session = pipeline.transcribe_tracks(
            tracks_output_dir, group_slices_by_name=group_slices_by_name
        )

    logger.info(f"/transcribe completed for {tracks_output_dir}")
    return TranscribeResponse(
        recording_path=tracks_output_dir,
        session_metadata_path=metadata_output_path,
        transcribed_slices_session=transc_session,
    )
