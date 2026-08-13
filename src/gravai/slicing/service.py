
from gravai.config.logging_config import get_logger
from gravai.models.models import RecordingType, Session
from gravai.slicing.slice import Slicer


def slice_track(recorder_type: RecordingType, tracks_output_dir: str, group_slices_by_name: bool = True) -> tuple[str, Session]:
    logger = get_logger("slicing", tracks_output_dir)
    logger.info(f"Slicing started for tracks in {tracks_output_dir}")


    match recorder_type:
        case RecordingType.TEAMS:
            session = Slicer.slice_and_create_session_teams_audio_track(tracks_output_dir, group_slices_by_name)
        case RecordingType.MEET:
            raise NotImplementedError("Google Meet slicing is not implemented yet.")
        case _:
            raise ValueError(f"Unsupported recorder type: {recorder_type}")
        
    metadata_output_path = f"{tracks_output_dir}/session_metadata.json"
    Slicer.save_session_data(session, metadata_output_path)
    logger.info(f"Slicing completed for session {session.session_id}, metadata written to {metadata_output_path}")

    return metadata_output_path, session