from gravai.models.models import RecordingType
from gravai.config.settings import get_settings
from gravai.config.logging_config import get_logger
from gravai.registry import get_provider

logger = get_logger("recording.service")


def record_meeting(
    recorder_type: RecordingType,
    meeting_url: str,
    output_dir: str | None = None,
    ws_host: str | None = None,
    ws_port: int | None = None,
    debug: bool = False,
) -> str:
    """Records one meeting end to end.

    Each call runs its own audio server process, so recordings started while
    another is in progress are independent of it. ws_port pins that server to a
    fixed port, which is for debugging a single recording - leave it unset and
    the OS assigns one, which is what makes concurrent recordings possible.
    """
    settings = get_settings()
    debug = debug or settings.DEBUG_GRAVAI
    logger.info(f"Recording meeting from {meeting_url} with recorder type {recorder_type}")
    logger.info(f"Debug mode is {'enabled' if debug else 'disabled'}")

    recorder = get_provider(recorder_type).recorder()  # type: ignore[misc]
    return recorder.record_meeting_with_audio_server(
        meeting_url,
        output_dir=output_dir,
        ws_host=ws_host,
        ws_port=ws_port,
        debug=debug,
    )
