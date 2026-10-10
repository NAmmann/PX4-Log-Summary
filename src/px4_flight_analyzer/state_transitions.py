"""Nav-state (flight mode) and armed-state transition tracking for the PDF report.

PX4's `nav_state` enum (`vehicle_status.nav_state`) is not decoded by
`pyulog` -- classic ULog `FORMAT` messages carry no enum metadata, and
the richer `event`-topic metadata that *would* carry it is deliberately
not decoded by this project (see README "Known limitations": a
fallback path in `pyulog.px4_events` can attempt a network download).
This module instead ships a best-effort, hardcoded mapping of the
`nav_state` values most commonly seen in the field; any value outside
this set renders as `nav_state=<N> (unknown)` rather than guessing a
name.

Each file's nav_state/armed change history is collected exactly once
(`analyze_file_transitions`), mirroring `error_analysis.analyze_file`'s
split between file-level collection and per-segment filtering
(`build_segment_transitions`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional, Tuple

from .flight_detection import derive_armed_changes
from .models import FlightSegment
from .parser import get_dataset_safe, list_value_changes_safe

# Best-effort mapping of PX4's `nav_state` enum (commonly seen values
# only -- pyulog has no enum metadata to decode this from). Anything
# outside this set is rendered via `format_nav_state`'s fallback branch,
# never guessed.
NAV_STATE_NAMES = {
    0: "MANUAL",
    1: "ALTCTL",
    2: "POSCTL",
    3: "AUTO_MISSION",
    4: "AUTO_LOITER",
    5: "AUTO_RTL",
    10: "ACRO",
    14: "OFFBOARD",
    15: "STAB",
    17: "AUTO_TAKEOFF",
    18: "AUTO_LAND",
}


def format_nav_state(value: int) -> str:
    name = NAV_STATE_NAMES.get(value)
    if name is not None:
        return name
    return f"nav_state={value} (unknown)"


class TransitionKind(Enum):
    """Which step-function a `StateTransitionEvent` came from."""

    NAV_STATE = "NAV_STATE"
    ARMED = "ARMED"


@dataclass(frozen=True)
class StateTransitionEvent:
    """One nav_state or armed-state change, or the synthetic value-at-liftoff entry."""

    timestamp_us: int
    kind: TransitionKind
    label: str
    is_synthetic: bool


@dataclass
class FileTransitionData:
    """A file's nav_state/armed change histories, computed once per file."""

    nav_state_changes: List[Tuple[int, int]]
    armed_changes: List[Tuple[int, bool]]


def analyze_file_transitions(ulog) -> FileTransitionData:
    """Collect nav_state and armed-state change histories exactly once per file.

    Armed-state derivation is reused as-is from `flight_detection`,
    rather than re-derived here, so both modules always agree on the
    same armed step function for a given file.
    """
    status_data = get_dataset_safe(ulog, "vehicle_status")
    nav_state_changes = [
        (int(t), int(v)) for t, v in list_value_changes_safe(status_data, "nav_state")
    ]
    armed_changes, _ = derive_armed_changes(ulog)
    return FileTransitionData(nav_state_changes=nav_state_changes, armed_changes=armed_changes)


def _value_at_or_before(changes: List[Tuple[int, object]], timestamp_us: int) -> Optional[object]:
    value = None
    for change_us, change_value in changes:
        if change_us <= timestamp_us:
            value = change_value
        else:
            break
    return value


def build_segment_transitions(
    file_data: FileTransitionData, segment: FlightSegment
) -> List[StateTransitionEvent]:
    """Build the chronological transition list for one flight segment.

    Includes a synthetic entry *at* `segment.start_us` for whichever
    nav_state/armed value was already in effect at liftoff -- without
    it, a flight with zero in-flight mode changes (e.g. a short hover
    flown entirely in one mode) would show an empty transitions list,
    which is unhelpful on a printed page -- plus every genuine change
    inside `(start_us, end_us]`.
    """
    events: List[StateTransitionEvent] = []

    nav_state_at_liftoff = _value_at_or_before(file_data.nav_state_changes, segment.start_us)
    if nav_state_at_liftoff is not None:
        events.append(
            StateTransitionEvent(
                timestamp_us=segment.start_us,
                kind=TransitionKind.NAV_STATE,
                label=format_nav_state(nav_state_at_liftoff),
                is_synthetic=True,
            )
        )
    for timestamp_us, value in file_data.nav_state_changes:
        if segment.start_us < timestamp_us <= segment.end_us:
            events.append(
                StateTransitionEvent(
                    timestamp_us=timestamp_us,
                    kind=TransitionKind.NAV_STATE,
                    label=format_nav_state(value),
                    is_synthetic=False,
                )
            )

    armed_at_liftoff = _value_at_or_before(file_data.armed_changes, segment.start_us)
    if armed_at_liftoff is not None:
        events.append(
            StateTransitionEvent(
                timestamp_us=segment.start_us,
                kind=TransitionKind.ARMED,
                label=str(bool(armed_at_liftoff)),
                is_synthetic=True,
            )
        )
    for timestamp_us, value in file_data.armed_changes:
        if segment.start_us < timestamp_us <= segment.end_us:
            events.append(
                StateTransitionEvent(
                    timestamp_us=timestamp_us,
                    kind=TransitionKind.ARMED,
                    label=str(bool(value)),
                    is_synthetic=False,
                )
            )

    events.sort(key=lambda event: (event.timestamp_us, event.kind.value))
    return events


def format_transition_lines(
    events: List[StateTransitionEvent], time_label_fn: Callable[[int], str]
) -> List[str]:
    """Format transition events into display lines.

    Uses the same `time_label_fn` convention as
    `error_analysis._format_message_lines`, so transition and message
    timestamps use identical absolute/relative labeling on the same
    page.
    """
    lines: List[str] = []
    for event in events:
        time_label = time_label_fn(event.timestamp_us)
        suffix = " (at liftoff)" if event.is_synthetic else ""
        lines.append(f"{event.kind.value} [{time_label}]: {event.label}{suffix}")
    return lines
