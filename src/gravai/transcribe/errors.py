import os

# Whisper failures often come back as an HTML error page or a long stack trace,
# and this text ends up in an HTTP response detail. Keep enough to identify the
# problem without pasting a whole page into the API output.
_MAX_BODY_CHARS = 500


class WhisperError(Exception):
    """Raised when the whisper server rejects or fails a transcription request.

    Carries the pieces needed to tell an upstream outage apart from a bad
    request, so callers can react to it rather than catching bare Exception.
    """

    def __init__(self, status_code: int, body: str, url: str, audio_file: str | None = None):
        self.status_code = status_code
        self.body = body
        self.url = url
        self.audio_file = audio_file
        super().__init__(self._describe())

    def _describe(self) -> str:
        message = f"Whisper server at {self.url} returned {self.status_code}"
        if self.audio_file:
            message += f" for {os.path.basename(self.audio_file)}"

        detail = (self.body or "").strip()
        if not detail:
            return message
        if len(detail) > _MAX_BODY_CHARS:
            detail = f"{detail[:_MAX_BODY_CHARS]}... ({len(self.body)} chars total)"
        return f"{message}: {detail}"
