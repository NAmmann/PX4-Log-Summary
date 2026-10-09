"""Tests for GPS start-coordinate resolution (brief 15.3, #21-25)."""

from __future__ import annotations

from helpers.ulog_builder import UlogBuilder
from px4_flight_analyzer.gps import MAX_GPS_TIME_DELTA_US, resolve_start_position
from px4_flight_analyzer.models import FlightSegment, SegmentStatus
from px4_flight_analyzer.parser import load_ulog

_BASE_US = 500_000


def _segment(path, start_us=2_000_000, end_us=22_000_000):
    return FlightSegment(0, path, start_us, end_us, SegmentStatus.COMPLETE)


# --- #21: valid GPS start coordinate ------------------------------------------------------------


def test_valid_gps_start_coordinate_old_style_scaled_int32(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "vehicle_gps_position",
        [
            ("uint64_t", "timestamp"),
            ("int32_t", "lat"),
            ("int32_t", "lon"),
            ("uint8_t", "fix_type"),
        ],
    )
    builder.add_sample(gps, 1_950_000, lat=509375000, lon=69603000, fix_type=3)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    position = resolve_start_position(ulog, _segment(path))
    assert position is not None
    assert position.latitude_deg == 50.9375
    assert position.longitude_deg == 6.9603
    assert position.fix_gated is True


def test_valid_gps_start_coordinate_new_style_float_degrees(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "sensor_gps",
        [
            ("uint64_t", "timestamp"),
            ("double", "latitude_deg"),
            ("double", "longitude_deg"),
            ("uint8_t", "fix_type"),
        ],
    )
    builder.add_sample(gps, 1_950_000, latitude_deg=50.9375, longitude_deg=6.9603, fix_type=3)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    position = resolve_start_position(ulog, _segment(path))
    assert position is not None
    assert position.latitude_deg == 50.9375
    assert position.longitude_deg == 6.9603
    assert position.source_topic == "sensor_gps"


# --- #22: invalid GPS values ---------------------------------------------------------------------


def test_invalid_gps_values_zero_sentinel_rejected(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "vehicle_gps_position",
        [("uint64_t", "timestamp"), ("int32_t", "lat"), ("int32_t", "lon"), ("uint8_t", "fix_type")],
    )
    builder.add_sample(gps, 1_950_000, lat=0, lon=0, fix_type=3)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    assert resolve_start_position(ulog, _segment(path)) is None


def test_invalid_gps_values_out_of_range_rejected(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "vehicle_gps_position",
        [("uint64_t", "timestamp"), ("int32_t", "lat"), ("int32_t", "lon"), ("uint8_t", "fix_type")],
    )
    # 1_000_000_000 / 1e7 = 100 degrees: out of the valid latitude range.
    builder.add_sample(gps, 1_950_000, lat=1_000_000_000, lon=69603000, fix_type=3)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    assert resolve_start_position(ulog, _segment(path)) is None


# --- #23: missing GPS data -----------------------------------------------------------------------


def test_missing_gps_data_entirely(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    land = builder.add_topic("vehicle_land_detected", [("uint64_t", "timestamp"), ("bool", "landed")])
    builder.add_sample(land, _BASE_US, landed=True)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    assert resolve_start_position(ulog, _segment(path)) is None


def test_fallback_to_global_position_when_no_fix(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "vehicle_gps_position",
        [("uint64_t", "timestamp"), ("int32_t", "lat"), ("int32_t", "lon"), ("uint8_t", "fix_type")],
    )
    builder.add_sample(gps, 1_950_000, lat=509375000, lon=69603000, fix_type=0)  # no fix
    global_pos = builder.add_topic(
        "vehicle_global_position", [("uint64_t", "timestamp"), ("double", "lat"), ("double", "lon")]
    )
    builder.add_sample(global_pos, 1_950_000, lat=50.9, lon=6.96)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    position = resolve_start_position(ulog, _segment(path))
    assert position is not None
    assert position.source_topic == "vehicle_global_position"
    assert position.fix_gated is False


# --- #24: GPS fix only acquired after recording start (closest-to-liftoff selection) -------------


def test_gps_fix_acquired_after_recording_start_picks_closest_sample(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "vehicle_gps_position",
        [("uint64_t", "timestamp"), ("int32_t", "lat"), ("int32_t", "lon"), ("uint8_t", "fix_type")],
    )
    builder.add_sample(gps, _BASE_US, lat=0, lon=0, fix_type=0)  # no fix yet, at recording start
    builder.add_sample(gps, 1_900_000, lat=509375000, lon=69603000, fix_type=3)  # fix acquired, close to liftoff
    builder.add_sample(gps, 10_000_000, lat=509400000, lon=69700000, fix_type=3)  # fix, but farther away
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    position = resolve_start_position(ulog, _segment(path, start_us=2_000_000))
    assert position is not None
    assert position.sample_timestamp_us == 1_900_000
    assert position.latitude_deg == 50.9375


def test_gps_sample_too_far_from_liftoff_is_rejected(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "vehicle_gps_position",
        [("uint64_t", "timestamp"), ("int32_t", "lat"), ("int32_t", "lon"), ("uint8_t", "fix_type")],
    )
    builder.add_sample(gps, _BASE_US, lat=509375000, lon=69603000, fix_type=3)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    far_start_us = _BASE_US + MAX_GPS_TIME_DELTA_US + 1_000_000
    position = resolve_start_position(ulog, _segment(path, start_us=far_start_us, end_us=far_start_us + 20_000_000))
    assert position is None


# --- #25: correct lat/lon order (never swapped) --------------------------------------------------


def test_latitude_longitude_order_never_swapped(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "vehicle_gps_position",
        [("uint64_t", "timestamp"), ("int32_t", "lat"), ("int32_t", "lon"), ("uint8_t", "fix_type")],
    )
    # Deliberately distinct, non-symmetric values so a swap would be obvious.
    builder.add_sample(gps, 1_950_000, lat=123456789, lon=-987654321, fix_type=3)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    position = resolve_start_position(ulog, _segment(path))
    assert position is not None
    assert position.latitude_deg == 12.3456789
    assert position.longitude_deg == -98.7654321


def test_first_gps_sample_in_file_not_blindly_used(tmp_path):
    # Two flights in sequence (not represented as segments here, just two
    # unrelated GPS samples): the resolver must pick the one close to
    # *this* segment's liftoff, not simply the first sample in the file.
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "vehicle_gps_position",
        [("uint64_t", "timestamp"), ("int32_t", "lat"), ("int32_t", "lon"), ("uint8_t", "fix_type")],
    )
    builder.add_sample(gps, _BASE_US, lat=100000000, lon=100000000, fix_type=3)  # far-earlier, unrelated
    builder.add_sample(gps, 49_900_000, lat=509375000, lon=69603000, fix_type=3)  # close to this segment
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    position = resolve_start_position(ulog, _segment(path, start_us=50_000_000, end_us=70_000_000))
    assert position is not None
    assert position.latitude_deg == 50.9375
    assert position.longitude_deg == 6.9603
