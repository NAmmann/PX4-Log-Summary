"""File discovery and UAV/Pilot folder-name resolution.

Pure filesystem logic with no dependency on `pyulog` -- this module only
ever looks at paths and folder names, never file contents.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def find_ulog_files(input_directory: Path, recursive: bool) -> list:
    """Find all `.ulg` log files under `input_directory`.

    The file extension is matched case-insensitively (`.ulg`, `.ULG`,
    `.Ulg`, ...). Files are deduplicated by resolved (canonical) path, so
    a file reachable through two different paths (e.g. via a symlink) is
    only returned once. The result is sorted by path for a deterministic
    processing order.

    :param input_directory: root directory to search.
    :param recursive: search all subdirectories when True; only the
        immediate contents of `input_directory` when False.
    :return: sorted list of matching file paths (not resolved/absolute,
        preserving however `input_directory` was given).
    """
    candidates = input_directory.rglob("*") if recursive else input_directory.glob("*")

    seen_resolved = set()
    found = []
    for candidate in candidates:
        try:
            if not candidate.is_file():
                continue
        except OSError as error:
            logger.warning("Skipping unreadable path %s: %s", candidate, error)
            continue
        if candidate.suffix.lower() != ".ulg":
            continue
        try:
            resolved = candidate.resolve()
        except OSError as error:
            logger.warning("Skipping unresolvable path %s: %s", candidate, error)
            continue
        if resolved in seen_resolved:
            logger.debug("Skipping duplicate file (same resolved path): %s", candidate)
            continue
        seen_resolved.add(resolved)
        found.append(candidate)

    found.sort(key=lambda p: p.as_posix().lower())
    return found


def parse_uav_pilot_folder_name(folder_name: str) -> Optional[Tuple[str, str]]:
    """Split a folder name of the form `UAV_NAME_PILOT_NAME` into (uav, pilot).

    Only the first underscore is significant: it separates the UAV name
    from the pilot name. Any further underscores are part of the pilot
    name (e.g. "Falcon03_Max_Mustermann" -> ("Falcon03", "Max_Mustermann")).

    :return: `(uav_name, pilot_name)`, or None if `folder_name` has no
        underscore, or either resulting part would be empty (e.g.
        "_Max" or "Falcon01_").
    """
    if "_" not in folder_name:
        return None
    uav_name, _, pilot_name = folder_name.partition("_")
    if not uav_name or not pilot_name:
        return None
    return uav_name, pilot_name


def resolve_uav_pilot(ulog_path: Path, input_directory: Path) -> Optional[Tuple[str, str]]:
    """Determine the (UAV, Pilot) responsible for `ulog_path`.

    Walks upward from the file's immediate parent folder toward (but
    excluding) `input_directory`, and returns the UAV/Pilot parsed from
    the *nearest* ancestor folder whose name matches the
    `UAV_NAME_PILOT_NAME` scheme. This means a log nested under a date or
    "archived" subfolder is still correctly attributed to the UAV/Pilot
    folder further up, while a UAV/Pilot folder nested inside another
    UAV/Pilot-shaped folder name takes the closer (more specific) one.

    :return: `(uav_name, pilot_name)` of the nearest matching ancestor,
        or None if no ancestor between the file and `input_directory`
        matches the naming scheme.
    """
    root = input_directory.resolve()
    resolved_file = ulog_path.resolve()
    try:
        relative = resolved_file.relative_to(root)
    except ValueError:
        logger.warning(
            "Cannot resolve UAV/Pilot for %s: file is not located under input directory %s",
            ulog_path,
            input_directory,
        )
        return None

    # relative.parts is (folder1, folder2, ..., filename); drop the filename
    # and walk from the nearest enclosing folder outward.
    ancestor_names = relative.parts[:-1][::-1]
    for folder_name in ancestor_names:
        match = parse_uav_pilot_folder_name(folder_name)
        if match is not None:
            return match

    logger.warning(
        "No ancestor folder of %s matches the 'UAV_PILOT' naming scheme between it and %s",
        ulog_path,
        input_directory,
    )
    return None
