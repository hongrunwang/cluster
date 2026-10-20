"""Client-local tracing helpers.

The client package mirrors the cluster's ``trace_function`` decorator shape so
protocol helpers that come from the cluster side stay structurally aligned.
When logging has been configured (see ``core.logging_setup``) each
wrapped call emits one ``[TRACE] Entering ...`` line on the shared logger.
If logging has not been configured yet the decorator stays silent so early
bootstrap code never leaks half-formatted audit lines onto stdout.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from .constants import LOGGER_NAME

P = ParamSpec("P")
R = TypeVar("R")


def trace_function(func: Callable[P, R]) -> Callable[P, R]:
    """Emit one trace line before the wrapped function is invoked."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        qualified_name = f"{func.__module__}.{func.__qualname__}"
        if logging.getLogger().handlers:
            logging.getLogger(LOGGER_NAME).debug("[TRACE] Entering %s", qualified_name)
        return func(*args, **kwargs)

    return wrapper
