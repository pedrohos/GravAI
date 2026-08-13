from gravai.models.models import RecordingType
from gravai.config.settings import get_settings
from gravai.config.logging_config import get_logger
from gravai.recording.providers.teams.provider import MeetingRecorder
from gravai.recording.providers.teams.provider import TeamsMeetingRecorder, stop_ws_audio_server
from importlib import resources
logger = get_logger("recording.service")


def start_ws_server(
    output_dir: str | None = None,
    ws_host: str | None = None,
    ws_port: int | None = None,
) -> None:
    settings = get_settings()
    logger.info("Starting WS audio server")
    TeamsMeetingRecorder.launch_ws_server(
        output_dir or settings.SAVE_DIR,
        ws_host or settings.WS_HOST,
        ws_port or settings.WS_PORT,
    )


def stop_ws_server() -> None:
    logger.info("Stopping WS audio server")
    stop_ws_audio_server()


def record_meeting(
    recorder_type: RecordingType,
    meeting_url: str,
    output_dir: str | None = None,
    ws_host: str | None = None,
    ws_port: int | None = None,
    debug: bool = False,
) -> str:
    settings = get_settings()
    debug = debug or settings.DEBUG_GRAVAI
    logger.info(f"Recording meeting from {meeting_url} with recorder type {recorder_type}")
    logger.info(f"Debug mode is {'enabled' if debug else 'disabled'}")
    
    match recorder_type:
        case RecordingType.TEAMS:
            recorder = TeamsMeetingRecorder()
        case RecordingType.MEET:
            raise NotImplementedError("Google Meet recording is not implemented yet.")
        case _:
            raise ValueError(f"Unsupported recorder type: {recorder_type}")
        
    return recorder.record_meeting_with_ws_audio_server(
        meeting_url,
        output_dir=output_dir,
        ws_host=ws_host,
        ws_port=ws_port,
        debug=debug,
    )
