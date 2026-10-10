"""Tests for nav_state/armed transition tracking (`state_transitions.py`)."""

from __future__ import annotations

from helpers.ulog_builder import UlogBuilder
from px4_flight_analyzer.flight_detection import derive_armed_changes
from px4_flight_analyzer.models import FlightSegment, SegmentStatus
from px4_flight_analyzer.parser import load_ulog
from px4_flight_analyzer.state_transitions import (
    TransitionKind,
    analyze_file_transitions,
    build_segment_transitions,
    format_nav_state,
    format_transition_lines,
)

_BASE_US = 500_000


def _segment(path, start_us=2_000_000, end_us=22_000_000):
    return FlightSegment(0, path, start_us, end_us, SegmentStatus.COMPLETE)


def test_format_nav_state_known_value():
    assert format_nav_state(4) == "AUTO_LOITER"


def test_format_nav_state_unknown_value():
    assert format_nav_state(99) == "nav_state=99 (unknown)"


def test_renamed_derive_armed_changes_is_public_and_importable(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    armed = builder.add_topic("actuator_armed", [("uint64_t", "timestamp"), ("bool", "armed")])
    builder.add_sample(armed, _BASE_US, armed=False)
    builder.add_sample(armed, 1_000_000, armed=True)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    changes, source = derive_armed_changes(ulog)
    assert source == "actuator_armed.armed"
    assert changes == [(_BASE_US, False), (1_000_000, True)]


def test_synthetic_liftoff_entry_plus_in_window_changes(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    status = builder.add_topic("vehicle_status", [("uint64_t", "timestamp"), ("uint8_t", "nav_state")])
    armed = builder.add_topic("actuator_armed", [("uint64_t", "timestamp"), ("bool", "armed")])
    builder.add_sample(status, _BASE_US, nav_state=3)  # AUTO_MISSION, in effect at liftoff
    builder.add_sample(armed, _BASE_US, armed=False)
    builder.add_sample(armed, 1_000_000, armed=True)  # before liftoff (start_us=2_000_000)
    builder.add_sample(status, 10_000_000, nav_state=4)  # AUTO_LOITER, inside window
    builder.add_sample(status, 30_000_000, nav_state=5)  # AUTO_RTL, outside window (after end_us)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    file_data = analyze_file_transitions(ulog)
    events = build_segment_transitions(file_data, _segment(path, start_us=2_000_000, end_us=22_000_000))

    nav_events = [e for e in events if e.kind is TransitionKind.NAV_STATE]
    armed_events = [e for e in events if e.kind is TransitionKind.ARMED]

    assert [(e.timestamp_us, e.label, e.is_synthetic) for e in nav_events] == [
        (2_000_000, "AUTO_MISSION", True),
        (10_000_000, "AUTO_LOITER", False),
    ]
    assert [(e.timestamp_us, e.label, e.is_synthetic) for e in armed_events] == [
        (2_000_000, "True", True),
    ]


def test_out_of_window_changes_excluded(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    status = builder.add_topic("vehicle_status", [("uint64_t", "timestamp"), ("uint8_t", "nav_state")])
    builder.add_sample(status, _BASE_US, nav_state=2)
    builder.add_sample(status, 1_000_000, nav_state=3)  # before this segment's start_us
    builder.add_sample(status, 25_000_000, nav_state=5)  # after this segment's end_us
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    file_data = analyze_file_transitions(ulog)
    events = build_segment_transitions(file_data, _segment(path, start_us=2_000_000, end_us=22_000_000))
    nav_events = [e for e in events if e.kind is TransitionKind.NAV_STATE]

    # The value in effect at liftoff (set by the 1_000_000 change) appears as the
    # synthetic entry; neither the 1_000_000 nor the 25_000_000 change appears directly.
    assert [(e.timestamp_us, e.label, e.is_synthetic) for e in nav_events] == [
        (2_000_000, "AUTO_MISSION", True),
    ]


def test_no_transition_data_produces_empty_list(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    land = builder.add_topic("vehicle_land_detected", [("uint64_t", "timestamp"), ("bool", "landed")])
    builder.add_sample(land, _BASE_US, landed=True)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    file_data = analyze_file_transitions(ulog)
    events = build_segment_transitions(file_data, _segment(path))
    assert events == []


def test_format_transition_lines(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    status = builder.add_topic("vehicle_status", [("uint64_t", "timestamp"), ("uint8_t", "nav_state")])
    builder.add_sample(status, _BASE_US, nav_state=3)
    builder.add_sample(status, 10_000_000, nav_state=4)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    file_data = analyze_file_transitions(ulog)
    events = build_segment_transitions(file_data, _segment(path, start_us=2_000_000, end_us=22_000_000))
    lines = format_transition_lines(events, lambda ts: f"T{ts}")

    assert lines == [
        "NAV_STATE [T2000000]: AUTO_MISSION (at liftoff)",
        "NAV_STATE [T10000000]: AUTO_LOITER",
    ]
