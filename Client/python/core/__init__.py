"""Cross-cutting glue for the superweb client: constants, logging, types, codec."""

import logging as _logging

from .constants import LOGGER_NAME

_logging.getLogger(LOGGER_NAME).addHandler(_logging.NullHandler())
