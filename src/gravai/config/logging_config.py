import logging
import os
import sys

from gravai.config.settings import get_settings

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

_ROOT_NAME = "gravai"
# Separates the module name from the session scope in a logger's name.
_SCOPE_SEP = "#"

_root_configured = False
_session_handlers: dict[str, logging.FileHandler] = {}


class _TrimScope(logging.Filter):
    """Session loggers are named `gravai.slicing#/tmp/...` so their file handler
    stays bound to a single session. Strip that suffix on the way out so lines
    still read `gravai.slicing`.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if _SCOPE_SEP in record.name:
            record.name = record.name.split(_SCOPE_SEP, 1)[0]
        return True


def _build_handler(handler: logging.Handler) -> logging.Handler:
    handler.setFormatter(logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT))
    handler.addFilter(_TrimScope())
    return handler


def _configure_root(filename: str, global_log: bool, global_log_dir: str) -> logging.Logger:
    """Attaches stdout and the shared log file once, to the root logger.

    Everything else propagates into them, so a line is written exactly once no
    matter how many modules or sessions have asked for a logger.
    """
    global _root_configured
    root = logging.getLogger(_ROOT_NAME)
    if _root_configured:
        return root

    root.setLevel(getattr(logging, get_settings().LOG_LEVEL.upper(), logging.INFO))
    root.propagate = False
    root.addHandler(_build_handler(logging.StreamHandler(sys.stdout)))

    if global_log:
        os.makedirs(global_log_dir, exist_ok=True)
        root.addHandler(_build_handler(logging.FileHandler(os.path.join(global_log_dir, filename))))

    _root_configured = True
    return root


def get_logger(
    name: str,
    log_dir: str | None = None,
    filename: str = "session.log",
    global_log: bool = True,
    global_log_dir: str = "./logs",
) -> logging.Logger:
    """Logger that always writes to stdout and, when log_dir is given, also
    appends to a per-session log file inside that directory (e.g. the
    recording's tracks output dir), so each session's timeline can be
    inspected on its own.
    """
    _configure_root(filename, global_log, global_log_dir)

    if not log_dir:
        return logging.getLogger(f"{_ROOT_NAME}.{name}")

    # Keyed by directory rather than by module, so every module logging against
    # the same session shares one file handler and a later session never
    # inherits an earlier session's.
    session_key = os.path.abspath(log_dir)
    logger = logging.getLogger(f"{_ROOT_NAME}.{name}{_SCOPE_SEP}{session_key}")

    handler = _session_handlers.get(session_key)
    if handler is None:
        os.makedirs(log_dir, exist_ok=True)
        handler = _build_handler(logging.FileHandler(os.path.join(log_dir, filename)))
        _session_handlers[session_key] = handler  # type: ignore[assignment]
    if handler not in logger.handlers:
        logger.addHandler(handler)

    return logger


def release_session_logs(log_dir: str) -> None:
    """Closes the file handler for a finished session, so its descriptor is not
    held for the lifetime of the process."""
    session_key = os.path.abspath(log_dir)
    handler = _session_handlers.pop(session_key, None)
    if handler is None:
        return
    for logger_name in list(logging.root.manager.loggerDict):
        if logger_name.endswith(f"{_SCOPE_SEP}{session_key}"):
            logging.getLogger(logger_name).removeHandler(handler)
    handler.close()
