"""Role-aware logging configuration for the superweb client.

The client runs as short interactive sessions launched from one of several
frontends. Each session writes one rotating log file under
``Client/python/logs/`` named after the role and the session timestamp, so a
user can always line up the console they saw with the file they need to share
for debugging. The formatter is kept in lock-step with the cluster side
(``app/logging_setup.py``) so that operators reading both directories do not
have to retrain their eyes.
"""

from __future__ import annotations

import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .constants import (
    DEFAULT_LOG_FILE_BACKUP_COUNT,
    DEFAULT_LOG_FILE_MAX_BYTES,
    LOG_DIRECTORY_NAME,
    LOGGER_NAME,
    PYTHON_ROOT,
)

_SESSION_TIMESTAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
_CURRENT_VERBOSE = False
_CURRENT_ROLE: str | None = None
_CURRENT_LOG_PATH: Path | None = None


def _log_directory() -> Path:
    return PYTHON_ROOT / LOG_DIRECTORY_NAME


def _log_path_for_role(role: str) -> Path:
    normalized_role = role.strip().lower().replace(" ", "-") or "client"
    return _log_directory() / f"{normalized_role}-{_SESSION_TIMESTAMP}.txt"


def _reset_root_handlers(root_logger: logging.Logger) -> None:
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()


def _install_root_file_handler(*, role: str, verbose: bool) -> Path:
    log_path = _log_path_for_role(role)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    _reset_root_handlers(root_logger)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=DEFAULT_LOG_FILE_MAX_BYTES,
        backupCount=DEFAULT_LOG_FILE_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))

    level = logging.DEBUG if verbose else logging.INFO
    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)
    return log_path


def configure_logging(verbose: bool = False, *, role: str = "client") -> logging.Logger:
    """Configure a per-session rotating log file and return the app logger.

    Calling this more than once with the same ``role``/``verbose`` combination
    is a no-op so interactive menus can freely re-enter the helper without
    discarding earlier file handlers.
    """

    global _CURRENT_VERBOSE, _CURRENT_ROLE, _CURRENT_LOG_PATH

    if (
        _CURRENT_ROLE == role
        and _CURRENT_VERBOSE == verbose
        and logging.getLogger().handlers
    ):
        return logging.getLogger(LOGGER_NAME)

    log_path = _install_root_file_handler(role=role, verbose=verbose)
    _CURRENT_VERBOSE = verbose
    _CURRENT_ROLE = role
    _CURRENT_LOG_PATH = log_path

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.info("Logging configured for role=%s verbose=%s file=%s", role, verbose, log_path.name)
    return logger


def current_log_path() -> Path | None:
    """Return the log file path bound by the most recent ``configure_logging`` call."""

    return _CURRENT_LOG_PATH


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger under the shared ``superweb_client`` tree."""

    if not name:
        return logging.getLogger(LOGGER_NAME)
    if name == LOGGER_NAME or name.startswith(f"{LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
