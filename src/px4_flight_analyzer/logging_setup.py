"""Console logging configuration.

A single place to configure the standard library `logging` module so
every other module can just do `logging.getLogger(__name__)` and get
consistent, timestamped, leveled output.
"""

from __future__ import annotations

import logging
import sys

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(verbose: bool) -> None:
    """(Re-)configure the root logger for console output.

    :param verbose: when True, set the level to DEBUG so detailed
        flight-detection/GPS/error-analysis diagnostics are shown; when
        False, keep the console at INFO and above.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    # Avoid duplicate handlers if configure_logging is called more than once
    # (e.g. across repeated test runs within the same process).
    for existing_handler in list(root_logger.handlers):
        root_logger.removeHandler(existing_handler)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    root_logger.addHandler(handler)
