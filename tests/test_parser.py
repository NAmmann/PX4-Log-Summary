"""Tests for the `pyulog.ULog` loading chokepoint (supports brief 15.1 #8-9)."""

from __future__ import annotations

import pytest

from helpers.ulog_builder import UlogBuilder
from px4_flight_analyzer.parser import ParseError, get_dataset_safe, list_value_changes_safe, load_ulog


def test_load_valid_file(tmp_path):
    builder = UlogBuilder(start_timestamp_us=1000)
    topic = builder.add_topic("vehicle_land_detected", [("uint64_t", "timestamp"), ("bool", "landed")])
    builder.add_sample(topic, 1000, landed=True)
    path = builder.write(tmp_path / "flight.ulg")

    ulog = load_ulog(path)
    assert ulog.get_dataset("vehicle_land_detected") is not None


def test_load_nonexistent_file_raises_parse_error(tmp_path):
    with pytest.raises(ParseError):
        load_ulog(tmp_path / "does_not_exist.ulg")


def test_load_garbage_file_raises_parse_error(tmp_path):
    path = tmp_path / "garbage.ulg"
    path.write_bytes(b"this is not a ulog file at all, just plain bytes")
    with pytest.raises(ParseError):
        load_ulog(path)


def test_load_truncated_header_raises_parse_error(tmp_path):
    path = tmp_path / "truncated.ulg"
    path.write_bytes(b"\x55\x4c\x6f\x67\x01\x12\x35\x01")  # magic + version, no timestamp
    with pytest.raises(ParseError):
        load_ulog(path)


def test_load_file_with_data_corruption_does_not_raise(tmp_path):
    # A file can be internally corrupt (mismatched data sizes) yet still
    # load successfully -- pyulog recovers and flags `file_corruption`.
    builder = UlogBuilder(start_timestamp_us=1000)
    topic = builder.add_topic("vehicle_land_detected", [("uint64_t", "timestamp"), ("bool", "landed")])
    builder.add_sample(topic, 1000, landed=True)
    builder.add_truncated_sample(topic, 2000, landed=False)
    builder.add_sample(topic, 3000, landed=True)
    path = builder.write(tmp_path / "corrupt.ulg")

    ulog = load_ulog(path)
    assert ulog.file_corruption is True


def test_get_dataset_safe_returns_none_for_missing_topic(tmp_path):
    builder = UlogBuilder(start_timestamp_us=1000)
    topic = builder.add_topic("vehicle_land_detected", [("uint64_t", "timestamp"), ("bool", "landed")])
    builder.add_sample(topic, 1000, landed=True)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    assert get_dataset_safe(ulog, "nonexistent_topic") is None


def test_get_dataset_safe_returns_dataset_for_present_topic(tmp_path):
    builder = UlogBuilder(start_timestamp_us=1000)
    topic = builder.add_topic("vehicle_land_detected", [("uint64_t", "timestamp"), ("bool", "landed")])
    builder.add_sample(topic, 1000, landed=True)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    data = get_dataset_safe(ulog, "vehicle_land_detected")
    assert data is not None
    assert data.name == "vehicle_land_detected"


def test_get_dataset_safe_respects_multi_id(tmp_path):
    builder = UlogBuilder(start_timestamp_us=1000)
    primary = builder.add_topic(
        "vehicle_gps_position", [("uint64_t", "timestamp"), ("int32_t", "lat")], multi_id=0
    )
    secondary = builder.add_topic(
        "vehicle_gps_position", [("uint64_t", "timestamp"), ("int32_t", "lat")], multi_id=1
    )
    builder.add_sample(primary, 1000, lat=1)
    builder.add_sample(secondary, 1000, lat=2)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    assert get_dataset_safe(ulog, "vehicle_gps_position", multi_id=0).data["lat"][0] == 1
    assert get_dataset_safe(ulog, "vehicle_gps_position", multi_id=1).data["lat"][0] == 2


def test_list_value_changes_safe_on_none_dataset():
    assert list_value_changes_safe(None, "landed") == []


def test_list_value_changes_safe_on_missing_field(tmp_path):
    builder = UlogBuilder(start_timestamp_us=1000)
    topic = builder.add_topic("vehicle_land_detected", [("uint64_t", "timestamp"), ("bool", "landed")])
    builder.add_sample(topic, 1000, landed=True)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)
    data = get_dataset_safe(ulog, "vehicle_land_detected")

    assert list_value_changes_safe(data, "field_that_does_not_exist") == []
