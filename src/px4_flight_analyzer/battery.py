"""Battery-level resolution for the PDF flight test summary.

`battery_status.remaining` is a 0..1 fraction of remaining capacity,
logged continuously throughout a flight (unlike GPS, which can drop
out) -- so the closest-in-time search here uses a tight 30-second bound
rather than GPS's 5-minute bound (`gps.MAX_GPS_TIME_DELTA_US`). This is
a deliberate deviation, not an oversight: a real `battery_status`
stream should always have a sample within a few seconds of any
reference instant, so 30 seconds is already generous while keeping the
bound meaningful rather than aspirational.

Only the primary vehicle instance (`multi_id=0`) is considered, matching
every other topic read in this project.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .models import BatteryReading
from .parser import get_dataset_safe

# battery_status is logged continuously through a flight, so a much
# tighter bound than GPS's 5 minutes is appropriate here.
MAX_BATTERY_TIME_DELTA_US = 30 * 1_000_000


def resolve_battery_level(ulog, reference_us: int) -> Optional[BatteryReading]:
    """Find the `battery_status.remaining` sample closest to `reference_us`.

    :return: the closest plausible (`0 <= remaining <= 1`) sample within
        `MAX_BATTERY_TIME_DELTA_US`, or None if no such sample exists --
        callers must leave the battery field blank in that case, never
        guess.
    """
    data = get_dataset_safe(ulog, "battery_status")
    if data is None or "remaining" not in data.data or "timestamp" not in data.data:
        return None

    remaining_arr = data.data["remaining"]
    timestamp_arr = data.data["timestamp"]

    candidates: List[Tuple[int, float]] = []
    for i in range(len(timestamp_arr)):
        remaining_fraction = float(remaining_arr[i])
        if not (0.0 <= remaining_fraction <= 1.0):
            continue
        sample_timestamp_us = int(timestamp_arr[i])
        delta_us = abs(sample_timestamp_us - reference_us)
        candidates.append((delta_us, remaining_fraction))

    if not candidates:
        return None

    candidates.sort(key=lambda candidate: candidate[0])
    delta_us, remaining_fraction = candidates[0]
    if delta_us > MAX_BATTERY_TIME_DELTA_US:
        return None
    return BatteryReading(remaining_fraction=remaining_fraction)
