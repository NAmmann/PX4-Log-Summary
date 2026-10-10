"""Tests for full-trajectory extraction (`trajectory.extract_trajectory`)."""

from __future__ import annotations

from helpers.ulog_builder import UlogBuilder
from px4_flight_analyzer.models import FlightSegment, SegmentStatus
from px4_flight_analyzer.parser import load_ulog
from px4_flight_analyzer.trajectory import extract_trajectory

_BASE_US = 500_000


def _segment(path, start_us=2_000_000, end_us=22_000_000):
    return FlightSegment(0, path, start_us, end_us, SegmentStatus.COMPLETE)


def test_returns_ordered_in_window_points(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "vehicle_gps_position",
        [("uint64_t", "timestamp"), ("int32_t", "lat"), ("int32_t", "lon"), ("uint8_t", "fix_type")],
    )
    # Deliberately out of chronological insertion order; the result must still be sorted.
    builder.add_sample(gps, 10_000_000, lat=509400000, lon=69700000, fix_type=3)
    builder.add_sample(gps, 5_000_000, lat=509380000, lon=69650000, fix_type=3)
    builder.add_sample(gps, 2_000_000, lat=509375000, lon=69603000, fix_type=3)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    points = extract_trajectory(ulog, _segment(path))
    assert [p.timestamp_us for p in points] == [2_000_000, 5_000_000, 10_000_000]
    assert points[0].latitude_deg == 50.9375
    assert points[0].longitude_deg == 6.9603


def test_boundaries_are_inclusive(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "vehicle_gps_position",
        [("uint64_t", "timestamp"), ("int32_t", "lat"), ("int32_t", "lon"), ("uint8_t", "fix_type")],
    )
    builder.add_sample(gps, 2_000_000, lat=509375000, lon=69603000, fix_type=3)  # exactly at start_us
    builder.add_sample(gps, 22_000_000, lat=509380000, lon=69650000, fix_type=3)  # exactly at end_us
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    points = extract_trajectory(ulog, _segment(path, start_us=2_000_000, end_us=22_000_000))
    assert [p.timestamp_us for p in points] == [2_000_000, 22_000_000]


def test_points_outside_window_are_excluded(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "vehicle_gps_position",
        [("uint64_t", "timestamp"), ("int32_t", "lat"), ("int32_t", "lon"), ("uint8_t", "fix_type")],
    )
    builder.add_sample(gps, 1_999_999, lat=509370000, lon=69600000, fix_type=3)  # just before start_us
    builder.add_sample(gps, 10_000_000, lat=509375000, lon=69603000, fix_type=3)  # inside window
    builder.add_sample(gps, 22_000_001, lat=509390000, lon=69700000, fix_type=3)  # just after end_us
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    points = extract_trajectory(ulog, _segment(path, start_us=2_000_000, end_us=22_000_000))
    assert [p.timestamp_us for p in points] == [10_000_000]


def test_falls_back_to_global_position_when_no_fix(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "vehicle_gps_position",
        [("uint64_t", "timestamp"), ("int32_t", "lat"), ("int32_t", "lon"), ("uint8_t", "fix_type")],
    )
    builder.add_sample(gps, 5_000_000, lat=509375000, lon=69603000, fix_type=0)  # no fix
    global_pos = builder.add_topic(
        "vehicle_global_position", [("uint64_t", "timestamp"), ("double", "lat"), ("double", "lon")]
    )
    builder.add_sample(global_pos, 5_000_000, lat=50.9, lon=6.96)
    builder.add_sample(global_pos, 8_000_000, lat=50.91, lon=6.97)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    points = extract_trajectory(ulog, _segment(path))
    assert [p.timestamp_us for p in points] == [5_000_000, 8_000_000]
    assert points[0].latitude_deg == 50.9


def test_fix_type_gating_excludes_unfixed_samples(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "vehicle_gps_position",
        [("uint64_t", "timestamp"), ("int32_t", "lat"), ("int32_t", "lon"), ("uint8_t", "fix_type")],
    )
    builder.add_sample(gps, 3_000_000, lat=509375000, lon=69603000, fix_type=0)  # no fix, rejected
    builder.add_sample(gps, 5_000_000, lat=509380000, lon=69650000, fix_type=3)  # fix, kept
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    points = extract_trajectory(ulog, _segment(path))
    assert [p.timestamp_us for p in points] == [5_000_000]


def test_multi_flight_windowing_isolates_each_segment(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "vehicle_gps_position",
        [("uint64_t", "timestamp"), ("int32_t", "lat"), ("int32_t", "lon"), ("uint8_t", "fix_type")],
    )
    builder.add_sample(gps, 5_000_000, lat=509375000, lon=69603000, fix_type=3)  # flight 1
    builder.add_sample(gps, 50_000_000, lat=509400000, lon=69700000, fix_type=3)  # flight 2
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    flight_1 = extract_trajectory(ulog, _segment(path, start_us=2_000_000, end_us=22_000_000))
    flight_2 = extract_trajectory(ulog, _segment(path, start_us=40_000_000, end_us=60_000_000))
    assert [p.timestamp_us for p in flight_1] == [5_000_000]
    assert [p.timestamp_us for p in flight_2] == [50_000_000]


def test_no_data_returns_empty_list(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    land = builder.add_topic("vehicle_land_detected", [("uint64_t", "timestamp"), ("bool", "landed")])
    builder.add_sample(land, _BASE_US, landed=True)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    assert extract_trajectory(ulog, _segment(path)) == []
