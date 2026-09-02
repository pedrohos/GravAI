"""Reading and writing the .env file behind Settings.

The settings a person actually has to change - which Google account the recorder
signs in as, where whisper is - are the ones that decide whether a meeting can be
recorded at all, and getting them wrong is how a first run fails. They live in a
file on a volume, which is the right place for them and an awkward one to reach
from a browser, so this exposes exactly the ones that are meant to be edited and
leaves the rest alone.

Three things are deliberate here:

- **The allowlist.** Only what is in EDITABLE settles what a caller can write, so
  a request naming VAD_OBSERVER_MEET_JS_PATH cannot repoint the injected
  JavaScript at a file of its choosing.
- **Secrets do not come back.** A password reads as a placeholder, and writing
  the placeholder back is what leaves it as it was - so a page that renders the
  form and posts it whole does not need to have been given the password to avoid
  destroying it.
- **The file keeps its shape.** Existing lines are rewritten in place and
  everything else - comments, blank lines, ordering, keys nothing here knows
  about - is left exactly as it was found.
"""

import os
from typing import Any

from pydantic import SecretStr

from gravai.config.logging_config import get_logger
from gravai.config.settings import ENV_FILE, Settings, get_settings

logger = get_logger("config.env_file")

#: What a secret reads as, and what writing it back means: leave it alone.
SECRET_PLACEHOLDER = "********"

#: The settings the configuration page may show and change. Everything else in
#: Settings is either derived from the package layout or not a knob.
EDITABLE: tuple[str, ...] = (
    "WHISPER_HOST",
    "WHISPER_PORT",
    "WHISPER_LANGUAGE",
    "GOOGLE_ACCOUNT_EMAIL",
    "GOOGLE_ACCOUNT_PASSWORD",
    "SAVE_DIR",
    "DATABASE_PATH",
    "VNC_ENABLED",
    "VNC_HOST",
    "VNC_PORT",
    "VNC_PASSWORD",
    "VNC_CAPTCHA_TIMEOUT_S",
    "LOG_LEVEL",
    "DEBUG_GRAVAI",
    "JOB_LOG_TAIL_LINES",
)

_SECRET_FIELDS = frozenset({"GOOGLE_ACCOUNT_PASSWORD", "VNC_PASSWORD"})


class ConfigError(ValueError):
    """A configuration change that cannot be applied."""


def env_file_path() -> str:
    """The .env this service reads, which is the one this module writes.

    GRAVAI_ENV_FILE points both at the same file. Settings reads it once, when it
    is imported, because that is when the class is built; this reads it on every
    call, so that a test which redirects it after import still writes where it
    then reads. The environment is consulted before the file either way, so the
    two never disagree about a value.
    """
    return os.path.abspath(os.environ.get("GRAVAI_ENV_FILE", ENV_FILE))


def _kind_of(name: str) -> str:
    annotation = Settings.model_fields[name].annotation
    if annotation is bool:
        return "bool"
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    return "string"


def _render(value: Any) -> str:
    if isinstance(value, SecretStr):
        return SECRET_PLACEHOLDER if value.get_secret_value() else ""
    if isinstance(value, bool):
        return "True" if value else "False"
    return "" if value is None else str(value)


def read_config() -> dict:
    """The editable settings as plain data.

    Plain dicts rather than the API's response models, so that the configuration
    layer does not import the HTTP layer to describe itself; the route validates
    this into its own schema.
    """
    settings = get_settings()
    fields = []
    for name in EDITABLE:
        model_field = Settings.model_fields[name]
        raw = getattr(settings, name)
        fields.append(
            {
                "name": name,
                "value": _render(raw),
                "secret": name in _SECRET_FIELDS,
                "is_set": bool(raw.get_secret_value())
                if isinstance(raw, SecretStr)
                else raw != "",
                "kind": _kind_of(name),
                "required": model_field.is_required(),
                "description": model_field.description or "",
            }
        )
    return {"env_file": env_file_path(), "fields": fields}


def write_config(values: dict[str, str]) -> dict:
    """Applies changed settings to the process and to the .env file.

    Both, and in that order: the environment is what the running service and
    every job process it spawns from here on will read, and the file is what
    survives a restart. The new values are validated by building a Settings out
    of them before anything is written, so a port that is not a number is refused
    with the old configuration still in place rather than accepted into a file
    that stops the service from starting next time.
    """
    unknown = sorted(set(values) - set(EDITABLE))
    if unknown:
        raise ConfigError(
            f"Not settings this service will change: {', '.join(unknown)}. "
            f"Editable settings are: {', '.join(EDITABLE)}."
        )

    changes = {
        name: value
        for name, value in values.items()
        # A secret that came back unchanged from a form is the placeholder, which
        # means "as it was" and not "set the password to eight asterisks".
        if not (name in _SECRET_FIELDS and value == SECRET_PLACEHOLDER)
    }
    if not changes:
        return read_config()

    previous = {name: os.environ.get(name) for name in changes}
    for name, value in changes.items():
        os.environ[name] = value

    get_settings.cache_clear()
    try:
        get_settings()
    except Exception as exc:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        get_settings.cache_clear()
        raise ConfigError(f"Rejected: {exc}") from exc

    _write_env_file(env_file_path(), changes)
    logger.info(f"Configuration updated: {', '.join(sorted(changes))}")
    return read_config()


def _write_env_file(path: str, changes: dict[str, str]) -> None:
    """Rewrites the named keys in place, appending the ones that are not there.

    Everything the file already contains that is not one of those keys survives
    untouched - comments included, since the comments in this project's .env are
    where the reasoning about each setting lives.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []

    remaining = dict(changes)
    output = []
    for line in lines:
        stripped = line.lstrip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped else None
        if key in remaining and not stripped.startswith("#"):
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)

    if remaining:
        if output and output[-1].strip():
            output.append("")
        output.extend(f"{key}={value}" for key, value in remaining.items())

    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    content = "\n".join(output).rstrip("\n") + "\n"

    # Preferred: write beside the file and rename over it, so anything reading
    # .env sees the old file or the new one and never a half-written one.
    temporary = f"{path}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(temporary, path)
        return
    except OSError as exc:
        # docker-compose mounts .env into the container as a single-file bind
        # mount, and renaming over one of those is refused by the kernel - the
        # mount is attached to the inode, not to the name. There the only way to
        # change the file is to write into it, so that is what happens, and the
        # brief window where it is truncated is the cost of the deployment this
        # project actually ships.
        logger.info(f"Could not replace {path} atomically ({exc}); writing it in place")
        try:
            os.remove(temporary)
        except OSError:
            pass

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
