"""Single chokepoint for loading PX4 ULog files via `pyulog`.

`pyulog.ULog.__init__` can raise a fairly wide and not fully documented
set of exceptions on malformed input (`OSError`/`TypeError`/`ValueError`/
`NotImplementedError`/`KeyError`/`IndexError`/`struct.error`, confirmed by
reading `pyulog/core.py`). This module is the only place that calls
`pyulog.ULog(...)` directly, so every caller only has to deal with one
narrow, documented exception type: `ParseError`.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import Any, List, Optional, Tuple

import pyulog

logger = logging.getLogger(__name__)

# The exact set of lower-level exceptions `pyulog.ULog` is known to raise
# for malformed/corrupt/truncated files, verified by reading its source.
_ULOG_LOAD_EXCEPTIONS = (
    OSError,
    TypeError,
    ValueError,
    NotImplementedError,
    KeyError,
    IndexError,
    struct.error,
    UnicodeDecodeError,
)


class ParseError(Exception):
    """Raised when a `.ulg` file cannot be loaded or parsed by `pyulog`."""


def load_ulog(file_path: Path) -> "pyulog.ULog":
    """Load a PX4 ULog file.

    Never lets a raw `pyulog`/stdlib exception escape -- everything is
    wrapped in `ParseError` so callers have one exception type to catch.
    The original file is only ever opened for reading; `pyulog` never
    writes to it.

    :raises ParseError: if the file cannot be opened or parsed.
    """
    try:
        return pyulog.ULog(str(file_path), disable_str_exceptions=True)
    except _ULOG_LOAD_EXCEPTIONS as error:
        raise ParseError(f"Failed to parse ULog file {file_path}: {error}") from error


def get_dataset_safe(ulog: "pyulog.ULog", name: str, multi_id: int = 0) -> Optional[Any]:
    """Look up a named dataset without raising if it is missing.

    Equivalent to `pyulog.ULog.get_dataset`, except it returns None
    instead of raising `IndexError` when the topic was not logged (or not
    logged at all file versions / PX4 versions) -- most topics in this
    project are optional, not guaranteed.
    """
    matches = [elem for elem in ulog.data_list if elem.name == name and elem.multi_id == multi_id]
    if not matches:
        return None
    return matches[0]


def list_value_changes_safe(data: Optional[Any], field_name: str) -> List[Tuple[int, Any]]:
    """Safely call `ULog.Data.list_value_changes`, tolerating missing fields.

    :param data: a `pyulog.ULog.Data` instance, or None (e.g. when the
        topic itself was missing -- `get_dataset_safe` returned None).
    :param field_name: the field to track value changes for.
    :return: list of `(timestamp_us, value)` tuples, or `[]` if `data` is
        None, or the dataset doesn't actually contain `field_name` or a
        `timestamp` field.
    """
    if data is None:
        return []
    if "timestamp" not in data.data or field_name not in data.data:
        return []
    try:
        return list(data.list_value_changes(field_name))
    except (KeyError, IndexError, ValueError) as error:
        logger.warning("Could not read value changes for field '%s': %s", field_name, error)
        return []
