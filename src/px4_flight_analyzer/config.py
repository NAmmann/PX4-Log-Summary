"""Validation and resolution of the user-editable `settings.py` module.

This is the single place where the raw `settings` module (plain
attributes, no validation of its own) is turned into a `ResolvedSettings`
object with concrete, checked, absolute values. Every other module only
ever sees the resolved form.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class SettingsError(Exception):
    """Raised when `settings.py` contains an invalid or unusable value."""


@dataclass(frozen=True)
class ResolvedSettings:
    """Fully validated, absolute-path form of `settings.py`."""

    input_directory: Path
    output_csv: Path
    output_pdf: Path
    minimum_flight_duration_seconds: float
    csv_delimiter: str
    timezone_name: str
    timezone: ZoneInfo
    recursive_search: bool
    verbose_logging: bool
    overwrite_output: bool
    map_fetch_online_tiles: bool


def _require_bool(settings_module: ModuleType, attribute_name: str) -> bool:
    if not hasattr(settings_module, attribute_name):
        raise SettingsError(f"settings.py is missing required setting '{attribute_name}'.")
    value = getattr(settings_module, attribute_name)
    if not isinstance(value, bool):
        raise SettingsError(
            f"settings.py: '{attribute_name}' must be a bool (True/False), "
            f"got {type(value).__name__}: {value!r}."
        )
    return value


def _require_path(settings_module: ModuleType, attribute_name: str, project_root: Path) -> Path:
    if not hasattr(settings_module, attribute_name):
        raise SettingsError(f"settings.py is missing required setting '{attribute_name}'.")
    value = getattr(settings_module, attribute_name)
    if not isinstance(value, Path):
        raise SettingsError(
            f"settings.py: '{attribute_name}' must be a pathlib.Path, "
            f"got {type(value).__name__}: {value!r}."
        )
    if value.is_absolute():
        return value
    return (project_root / value).resolve()


def resolve_settings(settings_module: ModuleType, project_root: Path) -> ResolvedSettings:
    """Validate every field of `settings_module` and resolve it into a `ResolvedSettings`.

    :param settings_module: the imported `settings` module.
    :param project_root: directory the paths in `settings.py` are relative to
        (the directory containing `px4_flight_analyzer.py`, not the CWD).
    :raises SettingsError: on any invalid or missing setting, or when
        `OVERWRITE_OUTPUT` is False and the resolved output file already
        exists -- this is the one case where the whole run is aborted
        before any scanning takes place.
    """
    input_directory = _require_path(settings_module, "INPUT_DIRECTORY", project_root)
    output_csv = _require_path(settings_module, "OUTPUT_CSV", project_root)
    output_pdf = _require_path(settings_module, "OUTPUT_PDF", project_root)

    if not hasattr(settings_module, "MINIMUM_FLIGHT_DURATION_SECONDS"):
        raise SettingsError("settings.py is missing required setting 'MINIMUM_FLIGHT_DURATION_SECONDS'.")
    min_duration = settings_module.MINIMUM_FLIGHT_DURATION_SECONDS
    if isinstance(min_duration, bool) or not isinstance(min_duration, (int, float)):
        raise SettingsError(
            "settings.py: 'MINIMUM_FLIGHT_DURATION_SECONDS' must be a number, "
            f"got {type(min_duration).__name__}: {min_duration!r}."
        )
    if min_duration < 0:
        raise SettingsError(
            "settings.py: 'MINIMUM_FLIGHT_DURATION_SECONDS' must be >= 0, "
            f"got {min_duration!r}."
        )

    if not hasattr(settings_module, "CSV_DELIMITER"):
        raise SettingsError("settings.py is missing required setting 'CSV_DELIMITER'.")
    delimiter = settings_module.CSV_DELIMITER
    if not isinstance(delimiter, str) or len(delimiter) != 1:
        raise SettingsError(
            "settings.py: 'CSV_DELIMITER' must be a string of length 1, "
            f"got {delimiter!r}."
        )

    if not hasattr(settings_module, "TIMEZONE"):
        raise SettingsError("settings.py is missing required setting 'TIMEZONE'.")
    timezone_name = settings_module.TIMEZONE
    if not isinstance(timezone_name, str):
        raise SettingsError(
            "settings.py: 'TIMEZONE' must be a string naming an IANA timezone "
            f"(e.g. 'UTC', 'Europe/Berlin'), got {type(timezone_name).__name__}: {timezone_name!r}."
        )
    try:
        timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise SettingsError(
            f"settings.py: 'TIMEZONE' value {timezone_name!r} is not a valid IANA timezone "
            f"name ({error}). If this is a fresh Python install, install the 'tzdata' package "
            "(it is already listed in requirements.txt)."
        ) from error

    recursive_search = _require_bool(settings_module, "RECURSIVE_SEARCH")
    verbose_logging = _require_bool(settings_module, "VERBOSE_LOGGING")
    overwrite_output = _require_bool(settings_module, "OVERWRITE_OUTPUT")
    map_fetch_online_tiles = _require_bool(settings_module, "MAP_FETCH_ONLINE_TILES")

    if not input_directory.exists() or not input_directory.is_dir():
        raise SettingsError(
            "settings.py: 'INPUT_DIRECTORY' does not resolve to an existing directory: "
            f"{input_directory}"
        )

    if output_csv.resolve() == output_pdf.resolve():
        raise SettingsError(
            "settings.py: 'OUTPUT_CSV' and 'OUTPUT_PDF' must not point to the same file: "
            f"{output_csv}"
        )

    if not overwrite_output and output_csv.exists():
        raise SettingsError(
            f"Output file already exists and OVERWRITE_OUTPUT is False: {output_csv}\n"
            "Either delete/move the existing file, or set OVERWRITE_OUTPUT = True in settings.py."
        )

    if not overwrite_output and output_pdf.exists():
        raise SettingsError(
            f"Output file already exists and OVERWRITE_OUTPUT is False: {output_pdf}\n"
            "Either delete/move the existing file, or set OVERWRITE_OUTPUT = True in settings.py."
        )

    return ResolvedSettings(
        input_directory=input_directory,
        output_csv=output_csv,
        output_pdf=output_pdf,
        minimum_flight_duration_seconds=float(min_duration),
        csv_delimiter=delimiter,
        timezone_name=timezone_name,
        timezone=timezone,
        recursive_search=recursive_search,
        verbose_logging=verbose_logging,
        overwrite_output=overwrite_output,
        map_fetch_online_tiles=map_fetch_online_tiles,
    )
