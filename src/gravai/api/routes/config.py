"""Reading and changing the settings a person is expected to change.

Which Google account the recorder signs in as, and where whisper is, decide
whether a meeting can be recorded at all - and they live in a .env file on a
volume, which is the right place for them and an awkward one to reach from a
browser. These two routes are what the configuration page is built on.

Only the settings in the allowlist are visible or writable, secrets come back as
a placeholder, and a change is validated before it is written: see
config/env_file.py for why each of those is the way it is.
"""

from fastapi import APIRouter

from gravai.api.routes.errors import handled
from gravai.api.schemas import ConfigResponse, ConfigUpdate
from gravai.config import env_file
from gravai.config.logging_config import get_logger

logger = get_logger("api.config")

router = APIRouter(prefix="/config", tags=["config"])


@router.get("", response_model=ConfigResponse)
def read_config() -> ConfigResponse:
    """The editable settings, their current values and what each one is for."""
    with handled("GET /config", env_file.env_file_path()):
        return ConfigResponse.model_validate(env_file.read_config())


@router.put("", response_model=ConfigResponse)
def update_config(update: ConfigUpdate) -> ConfigResponse:
    """Changes settings, in this process and in the .env file.

    A setting takes effect immediately for everything started after the call -
    every job runs in a process of its own and reads the configuration when it
    starts - and survives a restart because the file is written too. Recordings
    already in flight keep the configuration they began with.

    Sending a secret back unchanged, as the placeholder it was read as, leaves it
    as it was; sending an empty string clears it.
    """
    logger.info(f"Received configuration update for: {', '.join(sorted(update.values))}")
    with handled("PUT /config", env_file.env_file_path()):
        return ConfigResponse.model_validate(env_file.write_config(update.values))
