"""Tests for GPS-UTC offset resolution and time formatting (supports brief #45)."""

from __future__ import annotations

import datetime as dt

from zoneinfo import ZoneInfo

from helpers.ulog_builder import UlogBuilder
from px4_flight_analyzer.models import FlightSegment, SegmentStatus, TimeReferenceSource
from px4_flight_analyzer.parser import load_ulog
from px4_flight_analyzer.time_resolution import (
    find_utc_offset,
    format_absolute_time,
    format_relative_time,
    make_time_label_fn,
    resolve_file_time_reference,
    resolve_segment_time,
)

_BASE_US = 500_000


def _epoch_us(year, month, day, hour=0, minute=0, second=0) -> int:
    moment = dt.datetime(year, month, day, hour, minute, second, tzinfo=dt.timezone.utc)
    return int(moment.timestamp() * 1_000_000)


def test_find_utc_offset_from_vehicle_gps_position(tmp_path):
    anchor_us = _epoch_us(2026, 6, 15, 12, 0, 0)
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "vehicle_gps_position",
        [("uint64_t", "timestamp"), ("uint8_t", "fix_type"), ("uint64_t", "time_utc_usec")],
    )
    builder.add_sample(gps, 1_000_000, fix_type=3, time_utc_usec=anchor_us)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    offset = find_utc_offset(ulog)
    assert offset == anchor_us - 1_000_000


def test_find_utc_offset_none_when_no_gps_topic(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    land = builder.add_topic("vehicle_land_detected", [("uint64_t", "timestamp"), ("bool", "landed")])
    builder.add_sample(land, _BASE_US, landed=True)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    assert find_utc_offset(ulog) is None


def test_find_utc_offset_none_when_time_utc_usec_always_zero(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "vehicle_gps_position",
        [("uint64_t", "timestamp"), ("uint8_t", "fix_type"), ("uint64_t", "time_utc_usec")],
    )
    builder.add_sample(gps, 1_000_000, fix_type=3, time_utc_usec=0)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    assert find_utc_offset(ulog) is None


def test_find_utc_offset_none_without_usable_fix(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "vehicle_gps_position",
        [("uint64_t", "timestamp"), ("uint8_t", "fix_type"), ("uint64_t", "time_utc_usec")],
    )
    builder.add_sample(gps, 1_000_000, fix_type=0, time_utc_usec=_epoch_us(2026, 6, 15))
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    assert find_utc_offset(ulog) is None


def test_find_utc_offset_rejects_implausible_pre_2000_value(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "vehicle_gps_position",
        [("uint64_t", "timestamp"), ("uint8_t", "fix_type"), ("uint64_t", "time_utc_usec")],
    )
    builder.add_sample(gps, 1_000_000, fix_type=3, time_utc_usec=1_000_000)  # 1970-01-01 + 1s
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    assert find_utc_offset(ulog) is None


def test_find_utc_offset_picks_earliest_anchor_across_topics(tmp_path):
    anchor_early_us = _epoch_us(2026, 1, 1, 0, 0, 0)
    anchor_late_us = _epoch_us(2026, 1, 1, 0, 10, 0)
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps_old = builder.add_topic(
        "vehicle_gps_position",
        [("uint64_t", "timestamp"), ("uint8_t", "fix_type"), ("uint64_t", "time_utc_usec")],
    )
    gps_new = builder.add_topic(
        "sensor_gps",
        [("uint64_t", "timestamp"), ("uint8_t", "fix_type"), ("uint64_t", "time_utc_usec")],
    )
    builder.add_sample(gps_new, 5_000_000, fix_type=3, time_utc_usec=anchor_late_us)
    builder.add_sample(gps_old, 1_000_000, fix_type=3, time_utc_usec=anchor_early_us)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    offset = find_utc_offset(ulog)
    assert offset == anchor_early_us - 1_000_000


def test_resolve_file_time_reference_gps_utc(tmp_path):
    anchor_us = _epoch_us(2026, 6, 15, 12, 0, 0)
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "vehicle_gps_position",
        [("uint64_t", "timestamp"), ("uint8_t", "fix_type"), ("uint64_t", "time_utc_usec")],
    )
    builder.add_sample(gps, 1_000_000, fix_type=3, time_utc_usec=anchor_us)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    source, offset = resolve_file_time_reference(ulog)
    assert source is TimeReferenceSource.GPS_UTC
    assert offset == anchor_us - 1_000_000


def test_resolve_file_time_reference_nicht_ermittelbar(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    land = builder.add_topic("vehicle_land_detected", [("uint64_t", "timestamp"), ("bool", "landed")])
    builder.add_sample(land, _BASE_US, landed=True)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    source, offset = resolve_file_time_reference(ulog)
    assert source is TimeReferenceSource.NICHT_ERMITTELBAR
    assert offset is None


def test_resolve_segment_time_converts_and_applies_timezone(tmp_path):
    anchor_us = _epoch_us(2026, 6, 15, 12, 0, 0)  # summer -> CEST (+2)
    offset_us = anchor_us - 1_000_000
    segment = FlightSegment(0, tmp_path / "f.ulg", 2_000_000, 22_000_000, SegmentStatus.COMPLETE)

    resolution = resolve_segment_time(
        TimeReferenceSource.GPS_UTC, offset_us, segment, ZoneInfo("Europe/Berlin")
    )
    assert resolution.source is TimeReferenceSource.GPS_UTC
    assert resolution.resolved_start_utc.strftime("%Y-%m-%d %H:%M:%S") == "2026-06-15 14:00:01"
    assert resolution.resolved_end_utc.strftime("%Y-%m-%d %H:%M:%S") == "2026-06-15 14:00:21"


def test_resolve_segment_time_handles_dst_date_change():
    # 2026-03-29 00:30 UTC is 2026-03-29 01:30 CET, just before the
    # European spring-forward to CEST at 02:00 CET (01:00 UTC).
    anchor_us = _epoch_us(2026, 3, 29, 0, 30, 0)
    offset_us = anchor_us - 1_000_000
    segment = FlightSegment(0, "f.ulg", 1_000_000, 1_000_000 + 3_600 * 1_000_000, SegmentStatus.COMPLETE)

    resolution = resolve_segment_time(
        TimeReferenceSource.GPS_UTC, offset_us, segment, ZoneInfo("Europe/Berlin")
    )
    # One hour later is 01:30 UTC, already past the 01:00 UTC spring-forward
    # instant, so Berlin is at UTC+2 (CEST) by then: 01:30 UTC -> 03:30 CEST.
    assert resolution.resolved_end_utc.strftime("%H:%M:%S %Z") == "03:30:00 CEST"


def test_resolve_segment_time_blank_when_no_reference():
    segment = FlightSegment(0, "f.ulg", 1_000_000, 2_000_000, SegmentStatus.COMPLETE)
    resolution = resolve_segment_time(
        TimeReferenceSource.NICHT_ERMITTELBAR, None, segment, ZoneInfo("UTC")
    )
    assert resolution.resolved_start_utc is None
    assert resolution.resolved_end_utc is None


def test_format_absolute_time():
    moment = dt.datetime(2026, 6, 15, 14, 0, 1, tzinfo=ZoneInfo("Europe/Berlin"))
    assert format_absolute_time(moment) == "14:00:01"


def test_format_relative_time():
    assert format_relative_time(5_123_456, 2_000_000) == "+00:00:03"
    assert format_relative_time(2_000_000, 2_000_000) == "+00:00:00"


def test_format_relative_time_never_negative():
    # Defensive: a message technically before segment start (shouldn't
    # happen given inclusive window filtering upstream) must still clamp.
    assert format_relative_time(1_000_000, 2_000_000) == "+00:00:00"


def test_make_time_label_fn_absolute_mode():
    anchor_us = _epoch_us(2026, 6, 15, 12, 0, 0)
    offset_us = anchor_us - 1_000_000
    label_fn = make_time_label_fn(TimeReferenceSource.GPS_UTC, offset_us, 2_000_000, ZoneInfo("UTC"))
    assert label_fn(2_000_000) == "12:00:01"


def test_make_time_label_fn_relative_mode():
    label_fn = make_time_label_fn(TimeReferenceSource.NICHT_ERMITTELBAR, None, 2_000_000, ZoneInfo("UTC"))
    assert label_fn(5_000_000) == "+00:00:03"
