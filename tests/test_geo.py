"""Tests for shared GPS field-sniffing logic (relocated from gps.py)."""

from __future__ import annotations

from helpers.ulog_builder import UlogBuilder
from px4_flight_analyzer.geo import extract_lat_lon_arrays, is_plausible_coordinate
from px4_flight_analyzer.parser import get_dataset_safe, load_ulog

_BASE_US = 500_000


def test_extract_lat_lon_arrays_scales_int32_degrees(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "vehicle_gps_position",
        [("uint64_t", "timestamp"), ("int32_t", "lat"), ("int32_t", "lon"), ("uint8_t", "fix_type")],
    )
    builder.add_sample(gps, _BASE_US, lat=509375000, lon=69603000, fix_type=3)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    data = get_dataset_safe(ulog, "vehicle_gps_position")
    extracted = extract_lat_lon_arrays(data)
    assert extracted is not None
    lat_arr, lon_arr = extracted
    assert lat_arr[0] == 50.9375
    assert lon_arr[0] == 6.9603


def test_extract_lat_lon_arrays_passes_through_float_degrees(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    gps = builder.add_topic(
        "sensor_gps",
        [("uint64_t", "timestamp"), ("double", "latitude_deg"), ("double", "longitude_deg")],
    )
    builder.add_sample(gps, _BASE_US, latitude_deg=50.9375, longitude_deg=6.9603)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    data = get_dataset_safe(ulog, "sensor_gps")
    extracted = extract_lat_lon_arrays(data)
    assert extracted is not None
    lat_arr, lon_arr = extracted
    assert lat_arr[0] == 50.9375
    assert lon_arr[0] == 6.9603


def test_extract_lat_lon_arrays_none_when_fields_absent(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    land = builder.add_topic("vehicle_land_detected", [("uint64_t", "timestamp"), ("bool", "landed")])
    builder.add_sample(land, _BASE_US, landed=True)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    data = get_dataset_safe(ulog, "vehicle_land_detected")
    assert extract_lat_lon_arrays(data) is None


def test_is_plausible_coordinate_rejects_zero_sentinel():
    assert is_plausible_coordinate(0.0, 0.0) is False


def test_is_plausible_coordinate_rejects_out_of_range():
    assert is_plausible_coordinate(100.0, 6.9603) is False
    assert is_plausible_coordinate(50.9375, 200.0) is False


def test_is_plausible_coordinate_rejects_nan():
    assert is_plausible_coordinate(float("nan"), 6.9603) is False


def test_is_plausible_coordinate_accepts_valid_point():
    assert is_plausible_coordinate(50.9375, 6.9603) is True
