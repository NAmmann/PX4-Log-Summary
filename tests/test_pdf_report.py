"""Tests for PDF flight test summary rendering (`pdf_report.write_pdf_report`).

Builds `FlightEventGroup`/`FlightPdfData` fixtures directly (no `.ulg`
file needed) and monkeypatches `render_trajectory_map` so these tests
exercise only the PDF layout, independent of map rendering.
"""

from __future__ import annotations

import datetime as dt

import pypdf
from PIL import Image

import px4_flight_analyzer.pdf_report as pdf_report
from px4_flight_analyzer.models import (
    BatteryReading,
    ErrorAnalysisResult,
    ErrorAnalysisStatus,
    ErrorPresence,
    FlightEventGroup,
    FlightPdfData,
    FlightRecord,
    FlightSegment,
    ReportHeader,
    SegmentStatus,
    TimeReferenceSource,
    TimeResolution,
    TrajectoryPoint,
)


def _utc(*args) -> dt.datetime:
    return dt.datetime(*args, tzinfo=dt.timezone.utc)


def _flight_pdf_data(
    uav_name,
    pilot_name,
    source_file,
    start,
    end,
    error_text="",
    presence=ErrorPresence.NEIN,
    transition_lines=None,
):
    segment = FlightSegment(0, source_file, 0, 1_000_000, SegmentStatus.COMPLETE)
    time_resolution = TimeResolution(TimeReferenceSource.GPS_UTC, 0, start, end)
    error_analysis = ErrorAnalysisResult(ErrorAnalysisStatus.VOLLSTAENDIG, presence, error_text, 0)
    record = FlightRecord(segment, uav_name, pilot_name, None, time_resolution, error_analysis)
    return FlightPdfData(
        flight_record=record,
        trajectory=[TrajectoryPoint(50.93, 6.95, 0), TrajectoryPoint(50.94, 6.96, 500_000)],
        battery_start=BatteryReading(0.90),
        battery_end=BatteryReading(0.54),
        transition_lines=transition_lines or ["NAV_STATE [10:00:00]: AUTO_MISSION (at liftoff)"],
    )


def _extract_text(pdf_path) -> str:
    reader = pypdf.PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def test_full_report_contains_expected_content(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pdf_report, "render_trajectory_map", lambda trajectories, fetch_online_tiles: Image.new("RGB", (10, 10))
    )

    entry = _flight_pdf_data(
        "Falcon01",
        "Max Mustermann",
        tmp_path / "flight.ulg",
        _utc(2026, 6, 15, 10, 0, 0),
        _utc(2026, 6, 15, 10, 20, 0),
        error_text="ERROR [10:00:05]: Estimator issue",
        presence=ErrorPresence.JA,
    )
    group = FlightEventGroup(members=[entry])
    header = ReportHeader(
        year="2026",
        mmdd="0609",
        project="Testflug",
        place="Hamburg",
        header_label="2026/0609_Testflug_Hamburg",
        flight_count=1,
        total_flight_hours=0.33,
    )

    output_path = tmp_path / "output" / "flight_test_summary.pdf"
    pdf_report.write_pdf_report([group], header, output_path, map_fetch_online_tiles=False)

    assert output_path.exists()
    assert output_path.stat().st_size > 500

    text = _extract_text(output_path)
    assert "2026/0609_Testflug_Hamburg" in text
    assert "Falcon01" in text
    assert "Max Mustermann" in text
    assert "Estimator issue" in text
    assert "AUTO_MISSION" in text
    assert "Notes" in text
    assert "Number of flights: 1" in text


def test_multi_uav_group_shows_every_member(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pdf_report, "render_trajectory_map", lambda trajectories, fetch_online_tiles: Image.new("RGB", (10, 10))
    )

    member_a = _flight_pdf_data(
        "Falcon01", "Max", tmp_path / "a.ulg", _utc(2026, 6, 15, 10, 0, 0), _utc(2026, 6, 15, 10, 10, 0)
    )
    member_b = _flight_pdf_data(
        "Falcon02", "Erika", tmp_path / "b.ulg", _utc(2026, 6, 15, 10, 5, 0), _utc(2026, 6, 15, 10, 15, 0)
    )
    group = FlightEventGroup(members=[member_a, member_b])
    header = ReportHeader(None, None, None, None, "", flight_count=1, total_flight_hours=0.33)

    output_path = tmp_path / "output" / "flight_test_summary.pdf"
    pdf_report.write_pdf_report([group], header, output_path, map_fetch_online_tiles=False)

    text = _extract_text(output_path)
    assert "Falcon01" in text
    assert "Falcon02" in text
    assert "Max" in text
    assert "Erika" in text


def test_empty_groups_still_produces_valid_pdf_with_notes_page(tmp_path):
    header = ReportHeader(None, None, None, None, "", flight_count=0, total_flight_hours=0.0)
    output_path = tmp_path / "output" / "flight_test_summary.pdf"

    pdf_report.write_pdf_report([], header, output_path, map_fetch_online_tiles=False)

    assert output_path.exists()
    text = _extract_text(output_path)
    assert "Notes" in text
    assert "Number of flights: 0" in text


def test_unparseable_header_label_renders_placeholder(tmp_path):
    header = ReportHeader(None, None, None, None, "", flight_count=0, total_flight_hours=0.0)
    output_path = tmp_path / "output" / "flight_test_summary.pdf"

    pdf_report.write_pdf_report([], header, output_path, map_fetch_online_tiles=False)

    text = _extract_text(output_path)
    assert "could not be determined" in text
