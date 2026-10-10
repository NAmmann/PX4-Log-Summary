"""Tests for cross-UAV time-overlap flight grouping (`flight_grouping.py`).

Builds `FlightPdfData` fixtures directly (no `.ulg` file needed) since
grouping operates purely on already-resolved time-resolution data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from px4_flight_analyzer.flight_grouping import group_overlapping_flights
from px4_flight_analyzer.models import (
    ErrorAnalysisResult,
    ErrorAnalysisStatus,
    ErrorPresence,
    FlightPdfData,
    FlightRecord,
    FlightSegment,
    SegmentStatus,
    TimeReferenceSource,
    TimeResolution,
)

_INPUT_DIR = Path("/input")


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _resolved_entry(uav_name, start, end, source_file="flight.ulg", segment_index=0):
    segment = FlightSegment(
        segment_index, _INPUT_DIR / uav_name / source_file, 0, 1, SegmentStatus.COMPLETE
    )
    time_resolution = TimeResolution(
        source=TimeReferenceSource.GPS_UTC,
        utc_offset_us=0,
        resolved_start_utc=start,
        resolved_end_utc=end,
    )
    error_analysis = ErrorAnalysisResult(ErrorAnalysisStatus.VOLLSTAENDIG, ErrorPresence.NEIN, "", 0)
    record = FlightRecord(segment, uav_name, "Pilot", None, time_resolution, error_analysis)
    return FlightPdfData(record, [], None, None, [])


def _unresolved_entry(uav_name, source_file="flight.ulg", segment_index=0):
    segment = FlightSegment(
        segment_index, _INPUT_DIR / uav_name / source_file, 0, 1, SegmentStatus.COMPLETE
    )
    time_resolution = TimeResolution(TimeReferenceSource.NICHT_ERMITTELBAR, None, None, None)
    error_analysis = ErrorAnalysisResult(ErrorAnalysisStatus.VOLLSTAENDIG, ErrorPresence.NEIN, "", 0)
    record = FlightRecord(segment, uav_name, "Pilot", None, time_resolution, error_analysis)
    return FlightPdfData(record, [], None, None, [])


def test_pairwise_overlap_merges_into_one_group():
    a = _resolved_entry("Falcon01", _utc(2026, 6, 15, 10, 0, 0), _utc(2026, 6, 15, 10, 10, 0))
    b = _resolved_entry("Falcon02", _utc(2026, 6, 15, 10, 5, 0), _utc(2026, 6, 15, 10, 15, 0))

    groups = group_overlapping_flights([a, b], _INPUT_DIR)
    assert len(groups) == 1
    assert set(groups[0].uav_names) == {"Falcon01", "Falcon02"}


def test_transitive_three_way_chaining():
    # A overlaps B, B overlaps C, but A and C do not directly overlap.
    a = _resolved_entry("A", _utc(2026, 6, 15, 10, 0, 0), _utc(2026, 6, 15, 10, 5, 0))
    b = _resolved_entry("B", _utc(2026, 6, 15, 10, 4, 0), _utc(2026, 6, 15, 10, 9, 0))
    c = _resolved_entry("C", _utc(2026, 6, 15, 10, 8, 0), _utc(2026, 6, 15, 10, 12, 0))

    groups = group_overlapping_flights([a, b, c], _INPUT_DIR)
    assert len(groups) == 1
    assert len(groups[0].members) == 3
    assert set(groups[0].uav_names) == {"A", "B", "C"}


def test_disjoint_flights_stay_separate():
    a = _resolved_entry("Falcon01", _utc(2026, 6, 15, 10, 0, 0), _utc(2026, 6, 15, 10, 5, 0))
    b = _resolved_entry("Falcon02", _utc(2026, 6, 15, 11, 0, 0), _utc(2026, 6, 15, 11, 5, 0))

    groups = group_overlapping_flights([a, b], _INPUT_DIR)
    assert len(groups) == 2
    assert all(len(g.members) == 1 for g in groups)


def test_touching_boundary_counts_as_overlap():
    a = _resolved_entry("Falcon01", _utc(2026, 6, 15, 10, 0, 0), _utc(2026, 6, 15, 10, 10, 0))
    b = _resolved_entry("Falcon02", _utc(2026, 6, 15, 10, 10, 0), _utc(2026, 6, 15, 10, 20, 0))

    groups = group_overlapping_flights([a, b], _INPUT_DIR)
    assert len(groups) == 1
    assert len(groups[0].members) == 2


def test_nicht_ermittelbar_entries_are_always_singleton():
    unresolved_1 = _unresolved_entry("Falcon01", source_file="a.ulg")
    unresolved_2 = _unresolved_entry("Falcon02", source_file="b.ulg")
    resolved = _resolved_entry("Falcon03", _utc(2026, 6, 15, 10, 0, 0), _utc(2026, 6, 15, 10, 5, 0))

    groups = group_overlapping_flights([unresolved_1, unresolved_2, resolved], _INPUT_DIR)
    assert len(groups) == 3
    assert all(len(g.members) == 1 for g in groups)


def test_ordering_resolved_groups_first_by_earliest_start_then_unresolved():
    later_resolved = _resolved_entry("Falcon02", _utc(2026, 6, 15, 12, 0, 0), _utc(2026, 6, 15, 12, 5, 0))
    earlier_resolved = _resolved_entry("Falcon01", _utc(2026, 6, 15, 9, 0, 0), _utc(2026, 6, 15, 9, 5, 0))
    # Same UAV folder for both unresolved entries so only the filename differs,
    # keeping the expected relative-path ordering unambiguous.
    unresolved_b = _unresolved_entry("FalconX", source_file="b.ulg")
    unresolved_a = _unresolved_entry("FalconX", source_file="a.ulg")

    groups = group_overlapping_flights(
        [later_resolved, earlier_resolved, unresolved_b, unresolved_a], _INPUT_DIR
    )

    assert groups[0].uav_names == ["Falcon01"]
    assert groups[1].uav_names == ["Falcon02"]
    assert groups[2].members[0].flight_record.segment.source_file.name == "a.ulg"
    assert groups[3].members[0].flight_record.segment.source_file.name == "b.ulg"
