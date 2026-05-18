from fastapi import FastAPI
import json
from models.models import Session
from recording.teams import TeamsMeetingRecorder, stop_ws_audio_server
from slicing.slice import Slicer
from transcribe.base import transcribe_meeting_tracks

app = FastAPI(
    description="record_bot",
    # lifespan=lifespan
)


@app.on_event("shutdown")
def _shutdown_ws_server() -> None:
    stop_ws_audio_server()

@app.post("/record_meeting_and_transcribe")
async def record_meeting_and_transcribe(meeting_url: str, slice_tracks: bool, save_slices_metadata: bool = True):
    if "teams.live.com" not in meeting_url:
        return json.dumps({"error": "Only Teams meetings are supported at the moment. Make sure the url contains 'teams.live.com'."})

    try:
        tracks_output_dir, metadata_output_path, session = _record_meeting(meeting_url, slice_tracks, save_slices_metadata)
        transc_session = transcribe_meeting_tracks(session)
    except Exception as e:
        return json.dumps({"error": str(e)})

    return {"recording_path": tracks_output_dir, "session_metadata_path": metadata_output_path, "transcribed_slices_session": transc_session}

@app.post("/record_meeting")
async def record_meeting(meeting_url: str, slice_tracks: bool = True, save_slices_metadata: bool = True):
    if "teams.live.com" not in meeting_url:
        return json.dumps({"error": "Only Teams meetings are supported at the moment. Make sure the url contains 'teams.live.com'."})

    try:
        tracks_output_dir, metadata_output_path, session = _record_meeting(meeting_url, slice_tracks, save_slices_metadata)
    except Exception as e:
        return json.dumps({"error": str(e)})

    return {"recording_path": tracks_output_dir, "session_metadata_path": metadata_output_path, "slices_session": session}

def _record_meeting(meeting_url: str, slice_tracks: bool, save_slices_metadata: bool) -> tuple[str, str | None, Session | None]:
    teams = TeamsMeetingRecorder()
    tracks_output_dir = teams.record_meeting_with_ws_audio_server(meeting_url)
    
    metadata_output_path = None
    session = None
    if slice_tracks:
        session = Slicer.slice_teams_audio_track(tracks_output_dir)
        if save_slices_metadata:
            metadata_output_path = f"{tracks_output_dir}/session_metadata.json"
            Slicer.save_session_data(session, metadata_output_path)

    return tracks_output_dir, metadata_output_path, session