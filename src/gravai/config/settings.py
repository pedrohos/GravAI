from importlib import resources
from pathlib import Path

from pydantic import Field, FilePath, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv
from functools import lru_cache

load_dotenv()

class Settings(BaseSettings):
    """Application configuration from environment variables"""
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    DEBUG_GRAVAI: bool = Field(
        False,
        description="Enable debug mode",
        alias="DEBUG_GRAVAI",
    )

    WS_HOST: str = Field(
        "127.0.0.1",
        min_length=1,
        description=(
            "Host the per-recording audio WebSocket server binds to. Its port is "
            "assigned by the OS, one per recording, so simultaneous recordings "
            "never contend for it."
        ),
        alias="WS_HOST",
    )

    SAVE_DIR: str = Field(
        "/tmp",
        min_length=1,
        description="Directory to save recordings and metadata",
        alias="SAVE_DIR",
    )

    RTC_INTERCEPT_JS_PATH: FilePath = str(resources.files("gravai.recording") / "common" / "rtc_intercept.js")
    VAD_OBSERVER_TEAMS_JS_PATH: FilePath = str(resources.files("gravai.recording") / "providers" / "teams" / "vad_observer.js")
    VAD_OBSERVER_MEET_JS_PATH: FilePath = str(resources.files("gravai.recording") / "providers" / "meet" / "vad_observer.js")
    AUDIO_WORKLET_JS_PATH: FilePath = str(resources.files("gravai.recording") / "common" / "audio_worklet_processor.js")

    GOOGLE_ACCOUNT_EMAIL: str = Field(
        "",
        description=(
            "Google account the recorder signs in as, and only when a meeting turns "
            "an anonymous guest away. Left empty the recorder always joins as a "
            "guest, which is what most meetings allow. The account has to sign in "
            "with a password alone - a 2-Step Verification prompt cannot be answered "
            "from this container."
        ),
        alias="GOOGLE_ACCOUNT_EMAIL",
    )

    GOOGLE_ACCOUNT_PASSWORD: SecretStr = Field(
        SecretStr(""),
        description=(
            "Password for GOOGLE_ACCOUNT_EMAIL. SecretStr so that logging the "
            "settings, or anything holding them, prints a placeholder instead."
        ),
        alias="GOOGLE_ACCOUNT_PASSWORD",
    )

    VNC_ENABLED: bool = Field(
        True,
        description=(
            "Run the meeting browser on a virtual X display, so that a CAPTCHA on the "
            "Google sign-in can be handed to a person over VNC instead of ending the "
            "join. The display costs a process per recording and is never served to "
            "anyone until a challenge actually appears. Off, the browser runs headless "
            "and a CAPTCHA ends the attempt, which is what it did before this existed."
        ),
        alias="VNC_ENABLED",
    )

    VNC_HOST: str = Field(
        "0.0.0.0",
        min_length=1,
        description=(
            "Interface the CAPTCHA's VNC server binds to. The default is every one of "
            "them, which is what makes it reachable from outside the container - "
            "publish the port and keep VNC_PASSWORD set, or narrow this to 127.0.0.1 "
            "and reach it through an SSH tunnel."
        ),
        alias="VNC_HOST",
    )

    VNC_PORT: int = Field(
        5900,
        ge=1,
        le=65535,
        description=(
            "First port tried for the CAPTCHA's VNC server. It walks upwards from here "
            "when the port is taken, which is what lets two recordings be waiting on a "
            "person at once; the port it settled on is in the log and in the session's "
            "captcha_challenge.json."
        ),
        alias="VNC_PORT",
    )

    VNC_PASSWORD: SecretStr = Field(
        SecretStr(""),
        description=(
            "Password for the CAPTCHA's VNC server. Left empty a throwaway one is "
            "generated per challenge and written to the log and the session's "
            "captcha_challenge.json, which is the only place it exists. Note that VNC "
            "truncates a password to 8 characters."
        ),
        alias="VNC_PASSWORD",
    )

    VNC_CAPTCHA_TIMEOUT_S: float = Field(
        600.0,
        gt=0,
        description=(
            "How long a CAPTCHA waits for somebody to type it before the join is given "
            "up. This is a wait on a person noticing and connecting, not on a page."
        ),
        alias="VNC_CAPTCHA_TIMEOUT_S",
    )

    WHISPER_HOST: str = Field(
        ...,
        description="Host URL for the Whisper transcription service",
        alias="WHISPER_HOST",
    )

    WHISPER_PORT: str = Field(
        ...,
        description="Port for the Whisper transcription service",
        alias="WHISPER_PORT",
    )

    WHISPER_LANGUAGE: str = Field(
        "auto",
        description=(
            "Language code sent to Whisper, e.g. 'pt'. 'auto' leaves the language "
            "to Whisper's own detection, which reads a noisy or short clip as the "
            "wrong language and translates the transcript into it."
        ),
        alias="WHISPER_LANGUAGE",
    )

    LOG_LEVEL: str = Field(
        "INFO",
        description="Logging verbosity (DEBUG, INFO, WARNING, ERROR)",
        alias="LOG_LEVEL",
    )

    # DF_PATH: str = "df.pkl"


    # @model_validator(mode="after")
    # def assemble_es_hosts(self) -> "Settings":
    #     """Constructs the ES_HOSTS URL after model validation."""
    #     if self.ES_HOST and self.ES_PORT:
    #         self.ES_HOSTS = [f"http://{self.ES_HOST}:{self.ES_PORT}"]
    #     return self

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings() # type: ignore