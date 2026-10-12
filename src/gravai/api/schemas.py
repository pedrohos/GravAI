from datetime import datetime

from pydantic import BaseModel, Field


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


class SpeechSegmentOut(BaseModel):
    """One stretch of the meeting this participant was speaking, in seconds from
    the start of the meeting."""

    start: float
    end: float


class ParticipantResult(BaseModel):
    """One speaker's share of a meeting: when they talked and what they said."""

    participant_id: str
    participant_name: str | None = None
    track_path: str | None = None
    speech_track_path: str | None = None
    segments: list[SpeechSegmentOut] = Field(default_factory=list)
    transcript_text: str | None = None
    # Whisper's own segments, timed against the meeting rather than against the
    # speech-only track they were produced from. Left as whisper shaped them
    # rather than narrowed to a model of our own, since callers that want the
    # confidences and the token data want all of it.
    transcript_segments: list[dict] | None = None


class Recording(BaseModel):
    """A meeting and everything known about it.

    The audio itself is not in here - the paths are, and there are routes that
    stream them - but the timings, the speaking segments and the transcripts are,
    because those are what a caller reads rather than downloads.
    """

    id: str
    meeting_url: str | None = None
    provider: str | None = None
    session_dir: str
    # recording | processing | complete | failed. A meeting in progress is in
    # here from the moment it has a directory, which is well before it has audio.
    status: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    main_track_path: str | None = None
    # The whole meeting read as one conversation, from the mixed track. Not the
    # participant transcripts concatenated - overlaps and interruptions are in
    # place here, and there is no speaker attribution.
    meeting_transcript_text: str | None = None
    meeting_transcript_segments: list[dict] | None = None
    participants: list[ParticipantResult] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


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
