"""Tests for CSV row construction and file writing (brief 15.5, #39-46)."""

from __future__ import annotations

import csv
import datetime as dt
from zoneinfo import ZoneInfo

from px4_flight_analyzer.csv_export import CSV_COLUMNS, build_rows, write_csv
from px4_flight_analyzer.models import (
    ErrorAnalysisResult,
    ErrorAnalysisStatus,
    ErrorPresence,
    FlightRecord,
    FlightSegment,
    GpsPosition,
    SegmentStatus,
    TimeReferenceSource,
    TimeResolution,
)


def _make_record(
    source_file,
    segment_index=0,
    start_us=2_000_000,
    end_us=22_000_000,
    uav_name="Falcon01",
    pilot_name="Max",
    gps_position=None,
    resolved_start_utc=None,
    resolved_end_utc=None,
    time_source=TimeReferenceSource.NICHT_ERMITTELBAR,
    presence=ErrorPresence.NEIN,
    error_status=ErrorAnalysisStatus.VOLLSTAENDIG,
    details_text="",
):
    segment = FlightSegment(segment_index, source_file, start_us, end_us, SegmentStatus.COMPLETE)
    time_resolution = TimeResolution(
        source=time_source,
        utc_offset_us=0 if time_source is TimeReferenceSource.GPS_UTC else None,
        resolved_start_utc=resolved_start_utc,
        resolved_end_utc=resolved_end_utc,
    )
    error_analysis = ErrorAnalysisResult(
        status=error_status, presence=presence, details_text=details_text, matched_message_count=0
    )
    return FlightRecord(
        segment=segment,
        uav_name=uav_name,
        pilot_name=pilot_name,
        gps_position=gps_position,
        time_resolution=time_resolution,
        error_analysis=error_analysis,
    )


# --- #39/#40: correct header and exact column order ------------------------------------------------


def test_csv_header_and_column_order(tmp_path):
    output_path = tmp_path / "output" / "flights.csv"
    write_csv([], output_path, delimiter=";", input_directory=tmp_path)
    with output_path.open("r", encoding="utf-8", newline="") as csv_file:
        header_line = csv_file.readline().strip()
    assert header_line.split(";") == list(CSV_COLUMNS)
    assert CSV_COLUMNS == (
        "Datum",
        "UAV",
        "Pilot",
        "StartZeit",
        "LandeZeit",
        "StartZeitUTC",
        "LandeZeitUTC",
        "FlugDauerSekunden",
        "StartLatitude",
        "StartLongitude",
        "Fehlermeldungen",
        "FehlermeldungenDetails",
        "FehleranalyseStatus",
        "ZeitreferenzQuelle",
    )


# --- #41: correct semicolon separation ---------------------------------------------------------------


def test_semicolon_delimiter_used_by_default(tmp_path):
    record = _make_record(tmp_path / "f.ulg")
    output_path = tmp_path / "output" / "flights.csv"
    write_csv([record], output_path, delimiter=";", input_directory=tmp_path)

    lines = output_path.read_text(encoding="utf-8").splitlines()
    assert lines[1].count(";") == len(CSV_COLUMNS) - 1


def test_custom_delimiter_is_respected(tmp_path):
    record = _make_record(tmp_path / "f.ulg")
    output_path = tmp_path / "output" / "flights.csv"
    write_csv([record], output_path, delimiter=",", input_directory=tmp_path)

    lines = output_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(CSV_COLUMNS)


# --- #42: correct escaping of special characters -----------------------------------------------------


def test_special_characters_correctly_escaped(tmp_path):
    record = _make_record(
        tmp_path / "f.ulg",
        details_text='ERROR [12:00:00]: Quote " and semicolon ; and newline\nhandled',
    )
    output_path = tmp_path / "output" / "flights.csv"
    write_csv([record], output_path, delimiter=";", input_directory=tmp_path)

    with output_path.open("r", encoding="utf-8", newline="") as csv_file:
        rows = list(csv.DictReader(csv_file, delimiter=";"))
    assert rows[0]["FehlermeldungenDetails"] == (
        'ERROR [12:00:00]: Quote " and semicolon ; and newline\nhandled'
    )


# --- #43: chronological sorting -----------------------------------------------------------------------


def test_chronological_sorting_by_resolved_start_time(tmp_path):
    berlin = ZoneInfo("Europe/Berlin")
    later = _make_record(
        tmp_path / "a.ulg",
        resolved_start_utc=dt.datetime(2026, 6, 15, 14, 0, 0, tzinfo=berlin),
        resolved_end_utc=dt.datetime(2026, 6, 15, 14, 1, 0, tzinfo=berlin),
        time_source=TimeReferenceSource.GPS_UTC,
        uav_name="Later",
    )
    earlier = _make_record(
        tmp_path / "b.ulg",
        resolved_start_utc=dt.datetime(2026, 6, 15, 10, 0, 0, tzinfo=berlin),
        resolved_end_utc=dt.datetime(2026, 6, 15, 10, 1, 0, tzinfo=berlin),
        time_source=TimeReferenceSource.GPS_UTC,
        uav_name="Earlier",
    )
    rows = build_rows([later, earlier], tmp_path)
    assert [row["UAV"] for row in rows] == ["Earlier", "Later"]


def test_blank_date_rows_sort_after_resolved_rows_by_filename(tmp_path):
    berlin = ZoneInfo("Europe/Berlin")
    resolved = _make_record(
        tmp_path / "Falcon01_Max" / "flight.ulg",
        resolved_start_utc=dt.datetime(2026, 6, 15, 14, 0, 0, tzinfo=berlin),
        resolved_end_utc=dt.datetime(2026, 6, 15, 14, 1, 0, tzinfo=berlin),
        time_source=TimeReferenceSource.GPS_UTC,
        uav_name="Resolved",
    )
    unresolved_z = _make_record(tmp_path / "Zeta01_Bob" / "flight.ulg", uav_name="UnresolvedZ")
    unresolved_a = _make_record(tmp_path / "Alpha01_Ann" / "flight.ulg", uav_name="UnresolvedA")

    rows = build_rows([unresolved_z, resolved, unresolved_a], tmp_path)
    assert [row["UAV"] for row in rows] == ["Resolved", "UnresolvedA", "UnresolvedZ"]
    assert rows[1]["Datum"] == ""
    assert rows[2]["Datum"] == ""


# --- #44: empty CSV with header only --------------------------------------------------------------


def test_empty_csv_still_has_header(tmp_path):
    output_path = tmp_path / "output" / "flights.csv"
    write_csv([], output_path, delimiter=";", input_directory=tmp_path)
    content = output_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    assert len(lines) == 1
    assert lines[0].split(";") == list(CSV_COLUMNS)


# --- #45: correct timezone conversion (see also test_time_resolution.py) ---------------------------


def test_csv_row_reflects_timezone_converted_time(tmp_path):
    berlin = ZoneInfo("Europe/Berlin")
    record = _make_record(
        tmp_path / "f.ulg",
        resolved_start_utc=dt.datetime(2026, 6, 15, 14, 0, 1, tzinfo=berlin),
        resolved_end_utc=dt.datetime(2026, 6, 15, 14, 0, 21, tzinfo=berlin),
        time_source=TimeReferenceSource.GPS_UTC,
    )
    row = record.to_csv_row()
    assert row["Datum"] == "2026-06-15"
    assert row["StartZeit"] == "14:00:01"
    assert row["LandeZeit"] == "14:00:21"
    # Europe/Berlin is UTC+2 in June (CEST) -- the UTC columns must reflect that offset.
    assert row["StartZeitUTC"] == "2026-06-15 12:00:01"
    assert row["LandeZeitUTC"] == "2026-06-15 12:00:21"
    assert row["ZeitreferenzQuelle"] == "GPS-UTC"


def test_csv_row_utc_columns_cross_midnight_when_local_date_differs(tmp_path):
    berlin = ZoneInfo("Europe/Berlin")
    record = _make_record(
        tmp_path / "f.ulg",
        resolved_start_utc=dt.datetime(2026, 6, 16, 0, 30, 0, tzinfo=berlin),
        resolved_end_utc=dt.datetime(2026, 6, 16, 0, 50, 0, tzinfo=berlin),
        time_source=TimeReferenceSource.GPS_UTC,
    )
    row = record.to_csv_row()
    assert row["Datum"] == "2026-06-16"
    assert row["StartZeit"] == "00:30:00"
    # The UTC date rolls back to the previous day -- this is exactly why the UTC
    # columns carry a full date, not just a time, unlike the local StartZeit/LandeZeit.
    assert row["StartZeitUTC"] == "2026-06-15 22:30:00"
    assert row["LandeZeitUTC"] == "2026-06-15 22:50:00"


def test_csv_row_blank_time_fields_when_not_resolvable(tmp_path):
    record = _make_record(tmp_path / "f.ulg")
    row = record.to_csv_row()
    assert row["Datum"] == ""
    assert row["StartZeit"] == ""
    assert row["LandeZeit"] == ""
    assert row["StartZeitUTC"] == ""
    assert row["LandeZeitUTC"] == ""
    assert row["ZeitreferenzQuelle"] == "Nicht ermittelbar"


# --- #46: automatic creation of the output directory -----------------------------------------------


def test_output_directory_auto_created(tmp_path):
    output_path = tmp_path / "deeply" / "nested" / "output" / "flights.csv"
    assert not output_path.parent.exists()
    write_csv([], output_path, delimiter=";", input_directory=tmp_path)
    assert output_path.exists()


# --- GPS fields and error columns round-trip correctly ----------------------------------------------


def test_gps_fields_formatted_with_decimal_point(tmp_path):
    record = _make_record(
        tmp_path / "f.ulg",
        gps_position=GpsPosition(
            latitude_deg=50.9375,
            longitude_deg=6.9603,
            source_topic="vehicle_gps_position",
            sample_timestamp_us=2_000_000,
            fix_gated=True,
        ),
    )
    row = record.to_csv_row()
    assert row["StartLatitude"] == "50.9375000"
    assert row["StartLongitude"] == "6.9603000"
    assert "." in row["StartLatitude"]
    assert "," not in row["StartLatitude"]


def test_gps_fields_blank_when_no_position(tmp_path):
    record = _make_record(tmp_path / "f.ulg", gps_position=None)
    row = record.to_csv_row()
    assert row["StartLatitude"] == ""
    assert row["StartLongitude"] == ""


def test_flight_duration_is_numeric_seconds(tmp_path):
    record = _make_record(tmp_path / "f.ulg", start_us=2_000_000, end_us=22_000_000)
    row = record.to_csv_row()
    assert row["FlugDauerSekunden"] == "20.0"
    assert float(row["FlugDauerSekunden"]) == 20.0


def test_error_presence_and_status_values_are_literal_german_tokens(tmp_path):
    record = _make_record(
        tmp_path / "f.ulg", presence=ErrorPresence.JA, error_status=ErrorAnalysisStatus.VOLLSTAENDIG
    )
    row = record.to_csv_row()
    assert row["Fehlermeldungen"] == "Ja"
    assert row["FehleranalyseStatus"] == "Vollständig"
