"""Tests for battery-level resolution (`battery.resolve_battery_level`)."""

from __future__ import annotations

import pytest

from helpers.ulog_builder import UlogBuilder
from px4_flight_analyzer.battery import MAX_BATTERY_TIME_DELTA_US, resolve_battery_level
from px4_flight_analyzer.parser import load_ulog

_BASE_US = 500_000

_BATTERY_FIELDS = [("uint64_t", "timestamp"), ("float", "remaining")]


def test_closest_in_time_at_liftoff(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    battery = builder.add_topic("battery_status", _BATTERY_FIELDS)
    builder.add_sample(battery, 1_000_000, remaining=0.95)
    builder.add_sample(battery, 2_000_000, remaining=0.90)  # closest to start_us=2_000_000
    builder.add_sample(battery, 3_000_000, remaining=0.85)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    reading = resolve_battery_level(ulog, 2_000_000)
    assert reading is not None
    assert reading.remaining_fraction == pytest.approx(0.90)
    assert reading.percentage == pytest.approx(90.0)


def test_closest_in_time_at_touchdown(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    battery = builder.add_topic("battery_status", _BATTERY_FIELDS)
    builder.add_sample(battery, 20_000_000, remaining=0.40)
    builder.add_sample(battery, 22_000_000, remaining=0.35)  # closest to end_us=22_000_000
    builder.add_sample(battery, 24_000_000, remaining=0.30)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    reading = resolve_battery_level(ulog, 22_000_000)
    assert reading is not None
    assert reading.remaining_fraction == pytest.approx(0.35)


def test_bound_rejection(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    battery = builder.add_topic("battery_status", _BATTERY_FIELDS)
    far_timestamp_us = 2_000_000 + MAX_BATTERY_TIME_DELTA_US + 1_000_000
    builder.add_sample(battery, far_timestamp_us, remaining=0.5)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    assert resolve_battery_level(ulog, 2_000_000) is None


def test_implausible_fraction_rejected(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    battery = builder.add_topic("battery_status", _BATTERY_FIELDS)
    builder.add_sample(battery, 2_000_000, remaining=-1.0)
    builder.add_sample(battery, 2_100_000, remaining=1.5)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    assert resolve_battery_level(ulog, 2_000_000) is None


def test_only_primary_instance_considered(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    primary = builder.add_topic("battery_status", _BATTERY_FIELDS, multi_id=0)
    secondary = builder.add_topic("battery_status", _BATTERY_FIELDS, multi_id=1)
    builder.add_sample(primary, 2_000_000, remaining=0.77)
    builder.add_sample(secondary, 2_000_000, remaining=0.11)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    reading = resolve_battery_level(ulog, 2_000_000)
    assert reading is not None
    assert reading.remaining_fraction == pytest.approx(0.77)


def test_missing_topic_returns_none(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    land = builder.add_topic("vehicle_land_detected", [("uint64_t", "timestamp"), ("bool", "landed")])
    builder.add_sample(land, _BASE_US, landed=True)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    assert resolve_battery_level(ulog, 2_000_000) is None
