"""Tests for settings.py validation and project-root-anchored path resolution.

Covers brief #10 (correct resolution of relative paths independent of
CWD) and the "Ungültige Einstellungen in settings.py" requirement from
section 10.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from px4_flight_analyzer.config import SettingsError, resolve_settings


def _valid_settings(**overrides):
    base = dict(
        INPUT_DIRECTORY=Path("./flight_data"),
        OUTPUT_CSV=Path("./output/flights.csv"),
        MINIMUM_FLIGHT_DURATION_SECONDS=10,
        CSV_DELIMITER=";",
        TIMEZONE="UTC",
        RECURSIVE_SEARCH=True,
        VERBOSE_LOGGING=False,
        OVERWRITE_OUTPUT=True,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def test_valid_settings_resolve_successfully(tmp_path):
    (tmp_path / "flight_data").mkdir()
    resolved = resolve_settings(_valid_settings(), tmp_path)
    assert resolved.input_directory == (tmp_path / "flight_data").resolve()
    assert resolved.output_csv == (tmp_path / "output" / "flights.csv").resolve()
    assert resolved.minimum_flight_duration_seconds == 10.0
    assert resolved.csv_delimiter == ";"
    assert resolved.recursive_search is True


# --- #10: relative paths resolve against project_root, not CWD -------------------------------------


def test_relative_paths_resolve_against_project_root_not_cwd(tmp_path, monkeypatch):
    (tmp_path / "flight_data").mkdir()
    other_cwd = tmp_path / "somewhere_else"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    resolved = resolve_settings(_valid_settings(), tmp_path)
    assert resolved.input_directory == (tmp_path / "flight_data").resolve()
    assert str(resolved.input_directory).startswith(str(tmp_path))


def test_absolute_paths_used_as_is(tmp_path):
    absolute_input = tmp_path / "custom_logs"
    absolute_input.mkdir()
    resolved = resolve_settings(
        _valid_settings(INPUT_DIRECTORY=absolute_input, OUTPUT_CSV=tmp_path / "out.csv"), tmp_path
    )
    assert resolved.input_directory == absolute_input.resolve()


# --- invalid settings.py values (brief section 10 + 19 acceptance criteria) ------------------------


def test_missing_input_directory_raises(tmp_path):
    with pytest.raises(SettingsError, match="does not resolve to an existing directory"):
        resolve_settings(_valid_settings(), tmp_path)


def test_input_directory_not_a_path_raises(tmp_path):
    (tmp_path / "flight_data").mkdir()
    with pytest.raises(SettingsError, match="INPUT_DIRECTORY"):
        resolve_settings(_valid_settings(INPUT_DIRECTORY="flight_data"), tmp_path)


def test_negative_minimum_duration_raises(tmp_path):
    (tmp_path / "flight_data").mkdir()
    with pytest.raises(SettingsError, match="MINIMUM_FLIGHT_DURATION_SECONDS"):
        resolve_settings(_valid_settings(MINIMUM_FLIGHT_DURATION_SECONDS=-5), tmp_path)


def test_non_numeric_minimum_duration_raises(tmp_path):
    (tmp_path / "flight_data").mkdir()
    with pytest.raises(SettingsError, match="MINIMUM_FLIGHT_DURATION_SECONDS"):
        resolve_settings(_valid_settings(MINIMUM_FLIGHT_DURATION_SECONDS="ten"), tmp_path)


def test_boolean_minimum_duration_rejected(tmp_path):
    # bool is a subclass of int in Python; must not silently pass as a duration.
    (tmp_path / "flight_data").mkdir()
    with pytest.raises(SettingsError, match="MINIMUM_FLIGHT_DURATION_SECONDS"):
        resolve_settings(_valid_settings(MINIMUM_FLIGHT_DURATION_SECONDS=True), tmp_path)


def test_multi_character_delimiter_raises(tmp_path):
    (tmp_path / "flight_data").mkdir()
    with pytest.raises(SettingsError, match="CSV_DELIMITER"):
        resolve_settings(_valid_settings(CSV_DELIMITER=";;"), tmp_path)


def test_empty_delimiter_raises(tmp_path):
    (tmp_path / "flight_data").mkdir()
    with pytest.raises(SettingsError, match="CSV_DELIMITER"):
        resolve_settings(_valid_settings(CSV_DELIMITER=""), tmp_path)


def test_invalid_timezone_name_raises(tmp_path):
    (tmp_path / "flight_data").mkdir()
    with pytest.raises(SettingsError, match="TIMEZONE"):
        resolve_settings(_valid_settings(TIMEZONE="Not/ARealZone"), tmp_path)


def test_valid_iana_timezone_accepted(tmp_path):
    (tmp_path / "flight_data").mkdir()
    resolved = resolve_settings(_valid_settings(TIMEZONE="Europe/Berlin"), tmp_path)
    assert resolved.timezone_name == "Europe/Berlin"


@pytest.mark.parametrize("field", ["RECURSIVE_SEARCH", "VERBOSE_LOGGING", "OVERWRITE_OUTPUT"])
def test_non_bool_flags_raise(tmp_path, field):
    (tmp_path / "flight_data").mkdir()
    with pytest.raises(SettingsError, match=field):
        resolve_settings(_valid_settings(**{field: "yes"}), tmp_path)


def test_missing_setting_attribute_raises(tmp_path):
    (tmp_path / "flight_data").mkdir()
    settings = _valid_settings()
    del settings.CSV_DELIMITER
    with pytest.raises(SettingsError, match="CSV_DELIMITER"):
        resolve_settings(settings, tmp_path)


# --- #47: existing output file behavior -------------------------------------------------------------


def test_overwrite_output_false_aborts_when_file_exists(tmp_path):
    (tmp_path / "flight_data").mkdir()
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "flights.csv").write_text("existing")

    with pytest.raises(SettingsError, match="already exists"):
        resolve_settings(_valid_settings(OVERWRITE_OUTPUT=False), tmp_path)


def test_overwrite_output_false_passes_when_file_absent(tmp_path):
    (tmp_path / "flight_data").mkdir()
    resolved = resolve_settings(_valid_settings(OVERWRITE_OUTPUT=False), tmp_path)
    assert resolved.overwrite_output is False


def test_overwrite_output_true_passes_even_when_file_exists(tmp_path):
    (tmp_path / "flight_data").mkdir()
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "flights.csv").write_text("existing")

    resolved = resolve_settings(_valid_settings(OVERWRITE_OUTPUT=True), tmp_path)
    assert resolved.overwrite_output is True
