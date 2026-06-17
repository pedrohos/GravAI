from fastapi import FastAPI, HTTPException
import json
from contextlib import asynccontextmanager

from recording.service import record_meeting as service_record_meeting
from recording.service import start_ws_server as service_record_start_ws_server
from recording.service import stop_ws_server as service_record_stop_ws_server

from slicing.service import slice_track as service_slice_track

from transcribe.service import transcribe_meeting_tracks as service_transcribe_meeting_tracks


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

    try:
        tracks_output_dir = service_record_meeting(meeting_url)
        metadata_output_path, session = None, None
        if slice_tracks:
            metadata_output_path, session = service_slice_track(tracks_output_dir)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=f"{str(e)}")

    return {"recording_path": tracks_output_dir, "session_metadata_path": metadata_output_path, "session": session}


@app.post("/record_meeting_and_transcribe")
async def record_meeting_and_transcribe(meeting_url: str):
    if "teams.live.com" not in meeting_url and "teams.microsoft.com" not in meeting_url:
        return json.dumps({"error": "Only Teams meetings are supported at the moment. Make sure the url contains 'teams.live.com'."})

    try:
        tracks_output_dir = service_record_meeting(meeting_url)
        metadata_output_path, session = service_slice_track(tracks_output_dir)
        transc_session = service_transcribe_meeting_tracks(session)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=f"{str(e)}")

    return {"recording_path": tracks_output_dir, "session_metadata_path": metadata_output_path, "transcribed_slices_session": transc_session}

@app.post("/transcribe")
async def transcribe(tracks_output_dir: str):
    try:
        metadata_output_path, session = service_slice_track(tracks_output_dir)
        transc_session = service_transcribe_meeting_tracks(session)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=f"{str(e)}")

    return {"recording_path": tracks_output_dir, "session_metadata_path": metadata_output_path, "transcribed_slices_session": transc_session}
