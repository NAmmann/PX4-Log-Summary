"""Grouping overlapping multi-UAV flights into single PDF report entries.

If multiple UAVs are flying at the same time, they are merged into one
"flight event" entry on the printed flight list -- while each UAV still
gets its own CSV row (see `csv_export.py`), unaffected by this module.

Overlap is computed purely on each entry's resolved GPS-UTC time window
(`[resolved_start_utc, resolved_end_utc]`); entries with no resolved
time reference (`TimeReferenceSource.NICHT_ERMITTELBAR`) cannot be
compared to anything and are therefore always their own singleton
group, never merged by guesswork.

The merge is a standard sort-and-sweep interval union: sorting resolved
entries by start time and tracking the running maximum end time lets a
chain of 3+ overlapping entries (A overlaps B, B overlaps C, but A and
C don't directly overlap) collapse into one group transitively, which a
pairwise "do these two overlap" check alone could not do. Touching
endpoints (`end_A == start_B`) count as overlapping -- the simpler,
inclusive interpretation.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from .models import FlightEventGroup, FlightPdfData, TimeReferenceSource


def _is_resolved(entry: FlightPdfData) -> bool:
    return entry.flight_record.time_resolution.source is TimeReferenceSource.GPS_UTC


def _merge_resolved(entries: List[FlightPdfData]) -> List[FlightEventGroup]:
    ordered = sorted(
        entries,
        key=lambda e: (
            e.flight_record.time_resolution.resolved_start_utc,
            e.flight_record.time_resolution.resolved_end_utc,
            e.flight_record.uav_name,
        ),
    )

    groups: List[FlightEventGroup] = []
    current_members: List[FlightPdfData] = []
    running_end = None

    for entry in ordered:
        start = entry.flight_record.time_resolution.resolved_start_utc
        end = entry.flight_record.time_resolution.resolved_end_utc
        if current_members and running_end is not None and start <= running_end:
            current_members.append(entry)
            running_end = max(running_end, end)
        else:
            if current_members:
                groups.append(FlightEventGroup(members=current_members))
            current_members = [entry]
            running_end = end

    if current_members:
        groups.append(FlightEventGroup(members=current_members))

    return groups


def group_overlapping_flights(
    entries: List[FlightPdfData], input_directory: Path
) -> List[FlightEventGroup]:
    """Merge time-overlapping flights (across UAVs) into `FlightEventGroup`s.

    :return: one group per merged cluster of resolved, time-overlapping
        entries, plus one singleton group per unresolved entry. Ordered
        with every GPS-UTC-resolved group first (by earliest start),
        followed by unresolved singleton groups (by source file path,
        then segment index) -- mirroring `FlightRecord.sort_key`.
    """
    resolved = [entry for entry in entries if _is_resolved(entry)]
    unresolved = [entry for entry in entries if not _is_resolved(entry)]

    groups = _merge_resolved(resolved)
    groups.extend(FlightEventGroup(members=[entry]) for entry in unresolved)

    def _group_sort_key(group: FlightEventGroup):
        if group.earliest_start_utc is not None:
            return (0, group.earliest_start_utc, "", 0)
        return group.members[0].sort_key(input_directory)

    groups.sort(key=_group_sort_key)
    return groups
