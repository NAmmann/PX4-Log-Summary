"""Flight-segment detection from PX4 state-estimation topics.

Strategy
--------
A ULog recording can start while already airborne and/or end before
touchdown, so the recording's own start/end timestamps are never used as
a flight's start/end. Instead, this module derives actual liftoff and
touchdown instants from `vehicle_land_detected.landed`, which PX4 drives
from its land-detector state machine (not from raw sensor thresholds).

`ULog.Data.list_value_changes` (see `pyulog.core`) returns only genuine
value transitions -- messages with timestamp 0 are filtered out, and the
first entry is always kept even though it's not technically a "change".
That first entry is exactly the ambiguous case: if the file's first
recorded `landed` sample is already `False`, the real liftoff happened
before recording started, so that boundary is flagged as having a
missing/unknown start. The mirror case -- a recording that ends while
still airborne -- is flagged as having a missing/unknown end.

A pure `landed` state machine cannot be fully trusted by itself (land
detectors can mis-fire briefly, e.g. on a hard bounce). As a second,
independent check, each otherwise-plausible segment is cross-checked
against the vehicle's armed state (`actuator_armed.armed`, falling back
to the legacy `vehicle_status.arming_state` enum on older logs): if
neither topic is logged at all, the check is skipped entirely (benefit
of the doubt, since no armed data cannot itself invalidate a segment);
if a signal is present, the vehicle must have been armed at or across
the segment, or the segment is marked incomplete.

Multiple flights within a single file fall out naturally: every
liftoff/touchdown pair becomes its own `FlightSegment`, independently
evaluated and independently markable as incomplete.

Known, deliberate limitations (see README for the full list):
 - Only the primary vehicle instance (`multi_id=0`) is considered for
   `vehicle_land_detected`, `actuator_armed` and `vehicle_status`.
 - A rapid post-touchdown bounce (landed False->True->False) can in
   principle produce a short extra "segment"; no merge heuristic is
   applied, since merging risks silently collapsing two genuinely
   separate short flights. In practice such bounces are almost always
   filtered out by `MINIMUM_FLIGHT_DURATION_SECONDS`.
 - The armed-overlap check is deliberately lenient (armed at any point
   touching the window, not armed for the entire window) to tolerate
   normal timing skew between the land detector and the arming state
   machine.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

from .models import FlightSegment, IncompleteReason, SegmentStatus
from .parser import get_dataset_safe, list_value_changes_safe

logger = logging.getLogger(__name__)

MAX_PLAUSIBLE_FLIGHT_DURATION_US = 24 * 3600 * 1_000_000

# Legacy `vehicle_status.arming_state` enum value meaning "armed",
# used only as a fallback when `actuator_armed` was not logged.
_LEGACY_ARMING_STATE_ARMED = 2

RawSegmentTuple = Tuple[int, int, bool, bool]  # (start_us, end_us, missing_start, missing_end)


def build_raw_segments(landed_changes: List[Tuple[int, bool]], last_timestamp_us: int) -> List[RawSegmentTuple]:
    """Pair up `landed` transitions into raw liftoff/touchdown windows.

    Pure function over a plain list of `(timestamp, landed)` tuples (as
    returned by `list_value_changes_safe`), so it can be unit-tested
    without any `pyulog` object.

    :param landed_changes: chronological `(timestamp_us, landed)` pairs;
        must be non-empty.
    :param last_timestamp_us: timestamp of the last message in the file,
        used as the end boundary for a segment that is still airborne
        when the recording ends.
    """
    segments: List[RawSegmentTuple] = []
    pending_liftoff: Optional[int] = None
    pending_missing_start = False
    prev_landed: Optional[bool] = None

    for index, (timestamp_us, landed_raw) in enumerate(landed_changes):
        landed = bool(landed_raw)
        if index == 0:
            prev_landed = landed
            if landed is False:
                # Recording starts already airborne: the real liftoff is unknown.
                pending_liftoff = timestamp_us
                pending_missing_start = True
            continue

        if prev_landed is True and landed is False:
            pending_liftoff = timestamp_us
            pending_missing_start = False
        elif prev_landed is False and landed is True:
            if pending_liftoff is not None:
                segments.append((pending_liftoff, timestamp_us, pending_missing_start, False))
            pending_liftoff = None
        prev_landed = landed

    if pending_liftoff is not None:
        # Recording ends while still airborne: the real touchdown is unknown.
        segments.append((pending_liftoff, last_timestamp_us, pending_missing_start, True))

    return segments


def is_armed_overlap(armed_changes: List[Tuple[int, bool]], start_us: int, end_us: int) -> bool:
    """Check whether the armed step-function overlaps `[start_us, end_us]`.

    True if the vehicle was armed at `start_us`, or became armed at any
    recorded change inside the window. Deliberately lenient: it does not
    require the vehicle to be armed for the *entire* window, only that
    arming and the detected flight window are not clearly contradictory.

    :param armed_changes: chronological `(timestamp_us, armed)` pairs;
        must be non-empty (callers should skip this check entirely when
        no armed signal was available at all).
    """
    value_at_start = False
    for timestamp_us, armed in armed_changes:
        if timestamp_us <= start_us:
            value_at_start = bool(armed)
        else:
            break
    if value_at_start:
        return True

    for timestamp_us, armed in armed_changes:
        if start_us <= timestamp_us <= end_us and bool(armed):
            return True
    return False


def _derive_armed_changes(ulog) -> Tuple[List[Tuple[int, bool]], Optional[str]]:
    """Get the best available armed-state step function for `ulog`.

    Prefers `actuator_armed.armed`; falls back to the legacy
    `vehicle_status.arming_state` enum when that topic is absent (older
    PX4 versions / minimal logging profiles).

    :return: `(changes, source_topic)`; `changes` is `[]` and
        `source_topic` is None when neither topic yields any data, which
        callers must treat as "no signal available" rather than "vehicle
        never armed".
    """
    armed_data = get_dataset_safe(ulog, "actuator_armed")
    armed_changes = list_value_changes_safe(armed_data, "armed")
    if armed_changes:
        return [(int(t), bool(v)) for t, v in armed_changes], "actuator_armed.armed"

    status_data = get_dataset_safe(ulog, "vehicle_status")
    status_changes = list_value_changes_safe(status_data, "arming_state")
    if status_changes:
        return (
            [(int(t), bool(v == _LEGACY_ARMING_STATE_ARMED)) for t, v in status_changes],
            "vehicle_status.arming_state",
        )

    return [], None


def detect_flight_segments(ulog, source_file: Path) -> List[FlightSegment]:
    """Detect flight segments in a loaded ULog file.

    Does NOT apply `MINIMUM_FLIGHT_DURATION_SECONDS` -- that filtering
    is the orchestrator's responsibility, since it is a CSV-export
    decision, not a detection decision (a too-short segment is still a
    detected segment, just not an exported flight).

    :return: `[]` if `vehicle_land_detected` is missing or has no usable
        data (logged as a warning); otherwise one `FlightSegment` per
        detected liftoff/touchdown pair, each independently marked
        COMPLETE or INCOMPLETE.
    """
    land_data = get_dataset_safe(ulog, "vehicle_land_detected")
    landed_changes = list_value_changes_safe(land_data, "landed")
    if not landed_changes:
        logger.warning(
            "%s: land detection unavailable (vehicle_land_detected missing or empty); "
            "no flight segments can be detected in this file.",
            source_file,
        )
        return []

    raw_segments = build_raw_segments(
        [(int(t), bool(v)) for t, v in landed_changes], int(ulog.last_timestamp)
    )
    armed_changes, armed_source = _derive_armed_changes(ulog)

    segments: List[FlightSegment] = []
    for index, (start_us, end_us, missing_start, missing_end) in enumerate(raw_segments):
        reasons: List[IncompleteReason] = []
        if missing_start:
            reasons.append(IncompleteReason.MISSING_START_STATE)
        if missing_end:
            reasons.append(IncompleteReason.MISSING_END_STATE)

        if not reasons:
            duration_us = end_us - start_us
            if duration_us <= 0 or duration_us > MAX_PLAUSIBLE_FLIGHT_DURATION_US:
                reasons.append(IncompleteReason.IMPLAUSIBLE_DURATION)
            elif armed_changes and not is_armed_overlap(armed_changes, start_us, end_us):
                reasons.append(IncompleteReason.ARMED_SIGNAL_MISMATCH)

        status = SegmentStatus.INCOMPLETE if reasons else SegmentStatus.COMPLETE
        segment = FlightSegment(
            segment_index=index,
            source_file=source_file,
            start_us=start_us,
            end_us=end_us,
            status=status,
            incomplete_reasons=reasons,
        )
        if status is SegmentStatus.INCOMPLETE:
            logger.info(
                "%s: segment #%d (%d - %d us) marked incomplete: %s",
                source_file,
                index,
                start_us,
                end_us,
                ", ".join(reason.value for reason in reasons),
            )
        else:
            logger.debug(
                "%s: segment #%d (%d - %d us) detected as complete (armed source: %s)",
                source_file,
                index,
                start_us,
                end_us,
                armed_source,
            )
        segments.append(segment)

    return segments
