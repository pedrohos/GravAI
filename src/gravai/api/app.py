from fastapi import FastAPI, HTTPException
import json
from contextlib import asynccontextmanager

from gravai.config.logging_config import get_logger
from gravai.recording.service import record_meeting as service_record_meeting
from gravai.recording.service import start_ws_server as service_record_start_ws_server
from gravai.recording.service import stop_ws_server as service_record_stop_ws_server

from gravai.slicing.service import slice_track as service_slice_track

from gravai.transcribe.service import transcribe_meeting_tracks as service_transcribe_meeting_tracks
from gravai.models.models import RecordingType

logger = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    service_record_start_ws_server()
    yield
    service_record_stop_ws_server()

app = FastAPI(
    description="record_bot",
    lifespan=lifespan
)

@app.post("/record_meeting")
async def record_meeting(meeting_url: str, slice_tracks: bool = True):
    if "teams.live.com" not in meeting_url and "teams.microsoft.com" not in meeting_url:
        return json.dumps({"error": "Only Teams meetings are supported at the moment. Make sure the url contains 'teams.live.com'."})

    logger.info(f"Received /record_meeting request for {meeting_url} (slice_tracks={slice_tracks})")
    try:
        tracks_output_dir = service_record_meeting(RecordingType.TEAMS, meeting_url)
        metadata_output_path, session = None, None
        if slice_tracks:
            metadata_output_path, session = service_slice_track(RecordingType.TEAMS, tracks_output_dir)
    except Exception as e:
        logger.exception(f"/record_meeting failed for {meeting_url}")
        raise HTTPException(status_code=500, detail=f"{str(e)}")

    logger.info(f"/record_meeting completed for {meeting_url} -> {tracks_output_dir}")
    return {"recording_path": tracks_output_dir, "session_metadata_path": metadata_output_path, "session": session}


@app.post("/record_meeting_and_transcribe")
async def record_meeting_and_transcribe(meeting_url: str, group_slices_by_name: bool = True):
    if "teams.live.com" not in meeting_url and "teams.microsoft.com" not in meeting_url:
        return json.dumps({"error": "Only Teams meetings are supported at the moment. Make sure the url contains 'teams.live.com'."})

    logger.info(f"Received /record_meeting_and_transcribe request for {meeting_url}")
    try:
        tracks_output_dir = service_record_meeting(RecordingType.TEAMS, meeting_url)
        metadata_output_path, session = service_slice_track(RecordingType.TEAMS, tracks_output_dir, group_slices_by_name)
        transc_session = service_transcribe_meeting_tracks(session)
    except Exception as e:
        logger.exception(f"/record_meeting_and_transcribe failed for {meeting_url}")
        raise HTTPException(status_code=500, detail=f"{str(e)}")

    logger.info(f"/record_meeting_and_transcribe completed for {meeting_url} -> {tracks_output_dir}")
    return {"recording_path": tracks_output_dir, "session_metadata_path": metadata_output_path, "transcribed_slices_session": transc_session}

@app.post("/transcribe")
async def transcribe(tracks_output_dir: str, group_slices_by_name: bool = True):
    logger.info(f"Received /transcribe request for {tracks_output_dir}")
    try:
        metadata_output_path, session = service_slice_track(RecordingType.TEAMS, tracks_output_dir, group_slices_by_name)
        transc_session = service_transcribe_meeting_tracks(session)
    except Exception as e:
        logger.exception(f"/transcribe failed for {tracks_output_dir}")
        raise HTTPException(status_code=500, detail=f"{str(e)}")

    logger.info(f"/transcribe completed for {tracks_output_dir}")
    return {"recording_path": tracks_output_dir, "session_metadata_path": metadata_output_path, "transcribed_slices_session": transc_session}
