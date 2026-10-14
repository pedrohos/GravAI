from pydantic import BaseModel, Field

from gravai.models.database import RecordingDTO


class CaptchaChallenge(BaseModel):
    """A CAPTCHA on a Google sign-in that is waiting for somebody to type it.

    The recording that is waiting is a job running in a process of its own, and
    nothing it learns mid-join reaches the caller that submitted it. This is the
    other channel: connect a VNC client to vnc_url, answer the challenge, and the
    recorder carries on by itself.
    """

    state: str
    session_dir: str
    meeting_url: str | None = None
    vnc_url: str
    vnc_host: str
    vnc_port: int
    # Only ever the throwaway password generated for this one challenge. A
    # VNC_PASSWORD configured by the operator is theirs and is not repeated here.
    vnc_password: str | None = None
    screenshot_path: str | None = None
    expires_at: str
    updated_at: str


class Recording(RecordingDTO):
    """A persisted meeting with its duration calculated for the HTTP response."""

    duration_seconds: float | None = None


class JobLog(BaseModel):
    """The tail of a job's session log."""

    job_id: str
    session_dir: str | None = None
    log_path: str | None = None
    lines: list[str] = Field(default_factory=list)


class ConfigField(BaseModel):
    """One setting, as the configuration page needs to render it."""

    name: str
    value: str
    # A secret's value never leaves the service. It comes back as a placeholder
    # when one is set and empty when it is not, and writing the placeholder back
    # is what leaves it alone.
    secret: bool = False
    is_set: bool = True
    kind: str = "string"  # string | int | float | bool
    required: bool = False
    description: str = ""


class ConfigResponse(BaseModel):
    env_file: str
    fields: list[ConfigField]


class ConfigUpdate(BaseModel):
    """The settings to change, by name. Anything not named is left as it is."""

    values: dict[str, str]
