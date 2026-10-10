"""Tests for PDF title-page metadata parsing and flight-hour computation (`report_metadata.py`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from px4_flight_analyzer.models import (
    ErrorAnalysisResult,
    ErrorAnalysisStatus,
    ErrorPresence,
    FlightRecord,
    FlightSegment,
    SegmentStatus,
    TimeReferenceSource,
    TimeResolution,
)
from px4_flight_analyzer.report_metadata import (
    build_report_header,
    compute_flight_hours,
    parse_report_header_fields,
)


def _make_record(source_file, duration_seconds):
    segment = FlightSegment(0, source_file, 0, int(duration_seconds * 1_000_000), SegmentStatus.COMPLETE)
    time_resolution = TimeResolution(TimeReferenceSource.NICHT_ERMITTELBAR, None, None, None)
    error_analysis = ErrorAnalysisResult(ErrorAnalysisStatus.VOLLSTAENDIG, ErrorPresence.NEIN, "", 0)
    return FlightRecord(segment, "Falcon01", "Max", None, time_resolution, error_analysis)


# --- folder-name parsing: valid/invalid matrix ------------------------------------------------------


@pytest.mark.parametrize(
    "year_folder, mmdd_project_place, expected",
    [
        ("2026", "0609_Testflug_Hamburg", ("2026", "0609", "Testflug", "Hamburg")),
        ("2026", "0609_FieldTest_Los_Angeles", ("2026", "0609", "FieldTest", "Los_Angeles")),
    ],
)
def test_valid_folder_names_parse(tmp_path, year_folder, mmdd_project_place, expected):
    input_directory = tmp_path / year_folder / mmdd_project_place
    assert parse_report_header_fields(input_directory) == expected


@pytest.mark.parametrize(
    "year_folder, mmdd_project_place",
    [
        ("26", "0609_Testflug_Hamburg"),  # year not 4 digits
        ("Project", "0609_Testflug_Hamburg"),  # year not numeric
        ("2026", "TestOnly_Hamburg"),  # mmdd not numeric
        ("2026", "060_Testflug_Hamburg"),  # mmdd not 4 digits
        ("2026", "0609_Hamburg"),  # only two parts, no separate project/place
        ("2026", "0609"),  # no underscore at all
    ],
)
def test_invalid_folder_names_return_none(tmp_path, year_folder, mmdd_project_place):
    input_directory = tmp_path / year_folder / mmdd_project_place
    assert parse_report_header_fields(input_directory) is None


# --- compute_flight_hours: summation, not deduplicated ----------------------------------------------


def test_compute_flight_hours_sums_every_record():
    records = [
        _make_record(Path("a.ulg"), duration_seconds=1800),  # 0.5h
        _make_record(Path("b.ulg"), duration_seconds=3600),  # 1.0h
    ]
    assert compute_flight_hours(records) == pytest.approx(1.5)


def test_compute_flight_hours_zero_for_no_records():
    assert compute_flight_hours([]) == 0.0


# --- build_report_header: ties parsing + counts + hours together -----------------------------------


def test_build_report_header_with_valid_folder_name(tmp_path):
    input_directory = tmp_path / "2026" / "0609_Testflug_Hamburg"
    records = [_make_record(Path("a.ulg"), duration_seconds=3600)]

    header = build_report_header(input_directory, groups=[object(), object()], records=records)
    assert header.header_label == "2026/0609_Testflug_Hamburg"
    assert header.flight_count == 2
    assert header.total_flight_hours == pytest.approx(1.0)


def test_build_report_header_with_unparseable_folder_name(tmp_path):
    input_directory = tmp_path / "flight_data"
    records = [_make_record(Path("a.ulg"), duration_seconds=3600)]

    header = build_report_header(input_directory, groups=[], records=records)
    assert header.header_label == ""
    assert header.year is None
    assert header.flight_count == 0
