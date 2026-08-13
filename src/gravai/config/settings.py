from importlib import resources
from pathlib import Path

from pydantic import Field, FilePath
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
        description="Required WebSocket host",
        alias="WS_HOST",
    )

    WS_PORT: int = Field(
        8765,
        description="Required WebSocket port",
        alias="WS_PORT",
    )

    SAVE_DIR: str = Field(
        "/tmp",
        min_length=1,
        description="Directory to save recordings and metadata",
        alias="SAVE_DIR",
    )

    RTC_INTERCEPT_JS_PATH: FilePath = str(resources.files("gravai.recording") / "common" / "rtc_intercept.js")
    VAD_OBSERVER_TEAMS_JS_PATH: FilePath = str(resources.files("gravai.recording") / "providers" / "teams" / "vad_observer.js")
    AUDIO_WORKLET_JS_PATH: FilePath = str(resources.files("gravai.recording") / "common" / "audio_worklet_processor.js")

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