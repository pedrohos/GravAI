    
from models.models import Session
from slicing.slice import Slicer


def slice_track(tracks_output_dir: str) -> tuple[str, Session]:
    session = Slicer.slice_teams_audio_track(tracks_output_dir)
    metadata_output_path = f"{tracks_output_dir}/session_metadata.json"
    Slicer.save_session_data(session, metadata_output_path)

    return metadata_output_path, session