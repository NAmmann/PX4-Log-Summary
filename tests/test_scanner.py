"""Tests for file discovery and UAV/Pilot folder-name resolution (brief 15.1, #1-10)."""

from __future__ import annotations

import os
import stat
import sys

import pytest

from px4_flight_analyzer.scanner import find_ulog_files, parse_uav_pilot_folder_name, resolve_uav_pilot


def _touch(path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# --- #1: single log file in the root folder -------------------------------------------------


def test_single_file_in_root(tmp_path):
    _touch(tmp_path / "flight_001.ulg")
    found = find_ulog_files(tmp_path, recursive=True)
    assert found == [tmp_path / "flight_001.ulg"]


# --- #2: multiple log files in subfolders ----------------------------------------------------


def test_multiple_files_in_subfolders(tmp_path):
    _touch(tmp_path / "Falcon01_Max" / "flight_001.ulg")
    _touch(tmp_path / "Falcon02_Anna" / "flight_002.ulg")
    found = find_ulog_files(tmp_path, recursive=True)
    assert len(found) == 2


# --- #3: nested directories -------------------------------------------------------------------


def test_nested_directories(tmp_path):
    _touch(tmp_path / "Falcon01_Max" / "2026-08-12" / "flight_001.ulg")
    _touch(tmp_path / "Falcon01_Max" / "archived" / "old" / "flight_002.ulg")
    found = find_ulog_files(tmp_path, recursive=True)
    assert len(found) == 2


def test_non_recursive_search_ignores_subfolders(tmp_path):
    _touch(tmp_path / "flight_root.ulg")
    _touch(tmp_path / "Falcon01_Max" / "flight_nested.ulg")
    found = find_ulog_files(tmp_path, recursive=False)
    assert found == [tmp_path / "flight_root.ulg"]


def test_case_insensitive_extension_matching(tmp_path):
    _touch(tmp_path / "a.ulg")
    _touch(tmp_path / "b.ULG")
    _touch(tmp_path / "c.Ulg")
    _touch(tmp_path / "d.txt")
    found = find_ulog_files(tmp_path, recursive=True)
    assert len(found) == 3


def test_non_ulg_files_are_ignored(tmp_path):
    _touch(tmp_path / "notes.txt")
    _touch(tmp_path / "flight.ulgx")
    found = find_ulog_files(tmp_path, recursive=True)
    assert found == []


# --- #4: correct UAV/Pilot assignment ----------------------------------------------------------


@pytest.mark.parametrize(
    ("folder_name", "expected"),
    [
        ("Falcon01_Max", ("Falcon01", "Max")),
        ("Falcon02_Anna", ("Falcon02", "Anna")),
        ("Ranger01_Tom", ("Ranger01", "Tom")),
    ],
)
def test_correct_uav_pilot_assignment(tmp_path, folder_name, expected):
    log_path = tmp_path / folder_name / "flight.ulg"
    _touch(log_path)
    assert resolve_uav_pilot(log_path, tmp_path) == expected


def test_uav_pilot_assignment_from_nested_date_folder(tmp_path):
    log_path = tmp_path / "Falcon01_Max" / "2026-08-12" / "flight.ulg"
    _touch(log_path)
    assert resolve_uav_pilot(log_path, tmp_path) == ("Falcon01", "Max")


def test_uav_pilot_assignment_from_nested_archived_folder(tmp_path):
    log_path = tmp_path / "Falcon02_Anna" / "archived" / "flight.ulg"
    _touch(log_path)
    assert resolve_uav_pilot(log_path, tmp_path) == ("Falcon02", "Anna")


def test_nearest_ancestor_wins_over_further_one(tmp_path):
    # A UAV/Pilot-shaped folder nested inside another one: the nearer
    # (more specific) folder must win, per the documented priority rule.
    log_path = tmp_path / "Outer01_Alice" / "Inner02_Bob" / "flight.ulg"
    _touch(log_path)
    assert resolve_uav_pilot(log_path, tmp_path) == ("Inner02", "Bob")


# --- #5: pilot names with additional underscores ----------------------------------------------


def test_pilot_name_with_additional_underscores(tmp_path):
    assert parse_uav_pilot_folder_name("Falcon03_Max_Mustermann") == ("Falcon03", "Max_Mustermann")
    log_path = tmp_path / "Falcon03_Max_Mustermann" / "flight.ulg"
    _touch(log_path)
    assert resolve_uav_pilot(log_path, tmp_path) == ("Falcon03", "Max_Mustermann")


# --- #6: invalid folder names -------------------------------------------------------------------


@pytest.mark.parametrize(
    "folder_name",
    ["NoUnderscoreAtAll", "_MissingUav", "MissingPilot_", "_", ""],
)
def test_invalid_folder_names_rejected(folder_name):
    assert parse_uav_pilot_folder_name(folder_name) is None


def test_file_directly_in_input_root_has_no_valid_assignment(tmp_path):
    log_path = tmp_path / "flight.ulg"
    _touch(log_path)
    assert resolve_uav_pilot(log_path, tmp_path) is None


def test_file_under_invalid_folder_name_has_no_valid_assignment(tmp_path):
    log_path = tmp_path / "NoUnderscoreHere" / "flight.ulg"
    _touch(log_path)
    assert resolve_uav_pilot(log_path, tmp_path) is None


# --- #7: empty input folder ----------------------------------------------------------------------


def test_empty_input_directory(tmp_path):
    assert find_ulog_files(tmp_path, recursive=True) == []


# --- #8: unreadable files -------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits don't apply on Windows")
def test_unreadable_file_is_still_discovered_by_scanner(tmp_path):
    # Discovery itself should not choke on permission issues; parsing
    # (which is where a real PermissionError would surface) is a
    # separate module's responsibility (see test_parser.py).
    log_path = tmp_path / "flight.ulg"
    _touch(log_path)
    os.chmod(log_path, 0)
    try:
        found = find_ulog_files(tmp_path, recursive=True)
        assert found == [log_path]
    finally:
        os.chmod(log_path, stat.S_IRUSR | stat.S_IWUSR)


# --- #9: corrupted ULog files (discovery-level smoke test; parsing covered in test_parser.py) ---


def test_corrupted_file_is_still_discovered_by_scanner(tmp_path):
    log_path = tmp_path / "corrupt.ulg"
    _touch(log_path, content="not a real ulog file")
    found = find_ulog_files(tmp_path, recursive=True)
    assert found == [log_path]


# --- #10: correct resolution of relative paths independent of CWD -------------------------------
# (Covered end-to-end in test_config.py / test_integration.py, since it's a
# project-root-resolution concern, not a scanner concern.)


def test_duplicate_file_via_symlink_is_only_returned_once(tmp_path):
    real_file = tmp_path / "Falcon01_Max" / "flight.ulg"
    _touch(real_file)
    link_file = tmp_path / "Falcon01_Max" / "link.ulg"
    try:
        link_file.symlink_to(real_file)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform/filesystem")

    found = find_ulog_files(tmp_path, recursive=True)
    resolved = {p.resolve() for p in found}
    assert resolved == {real_file.resolve()}
