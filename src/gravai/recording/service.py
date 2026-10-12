from collections.abc import Callable

from gravai.config.logging_config import get_logger
from gravai.config.registry import get_provider
from gravai.config.settings import get_settings
from gravai.models.common import RecordingType

logger = get_logger("recording.service")


def record_meeting(
    recorder_type: RecordingType,
    meeting_url: str,
    output_dir: str | None = None,
    debug: bool = False,
    on_session_start: Callable[[str, str], None] | None = None,
) -> str:
    """Records one meeting end to end.

    The audio is captured from a PulseAudio sink of this recording's own, bound
    to its browser through PULSE_SINK, so recordings started while another is in
    progress neither hear each other nor share a process lifetime.

    on_session_start is called with the session id and its directory as soon as
    the recorder has both, which is the only way a caller learns where a
    recording is writing before it ends.
    """
    settings = get_settings()
    debug = debug or settings.DEBUG_GRAVAI
    logger.info(f"Recording meeting from {meeting_url} with recorder type {recorder_type}")
    logger.info(f"Debug mode is {'enabled' if debug else 'disabled'}")

    recorder = get_provider(recorder_type).recorder()  # type: ignore[misc]
    return recorder.record_meeting_with_audio_capture(
        meeting_url,
        output_dir=output_dir,
        debug=debug,
        on_session_start=on_session_start,
    )
