"""Full-trajectory extraction for the PDF flight test summary.

Same topic precedence and fix-gating as `gps.resolve_start_position`
(`gps.py`): primary GPS topics first, requiring a usable fix where a
`fix_type` field is available, falling back to `vehicle_global_position`
only if nothing usable was found there. Unlike `gps.py`, which picks the
single sample closest to liftoff, this module returns every plausible
point whose timestamp falls inside the segment's own
`[start_us, end_us]` window -- there is no time-delta bound here, since
the window itself is already the filter (every point considered is
already known to belong to this flight).
"""

from __future__ import annotations

from typing import List, Tuple

from .geo import _MIN_USABLE_FIX_TYPE, extract_lat_lon_arrays, is_plausible_coordinate
from .gps import FALLBACK_POSITION_TOPIC, GPS_POSITION_TOPICS
from .models import FlightSegment, TrajectoryPoint
from .parser import get_dataset_safe


def _collect_points_in_window(
    ulog, topic_names: Tuple[str, ...], segment: FlightSegment, require_fix: bool
) -> List[TrajectoryPoint]:
    points: List[TrajectoryPoint] = []
    for topic in topic_names:
        data = get_dataset_safe(ulog, topic)
        if data is None:
            continue
        extracted = extract_lat_lon_arrays(data)
        if extracted is None or "timestamp" not in data.data:
            continue
        lat_arr, lon_arr = extracted
        timestamp_arr = data.data["timestamp"]

        has_fix_type = "fix_type" in data.data
        fix_mask = (data.data["fix_type"] > _MIN_USABLE_FIX_TYPE) if (require_fix and has_fix_type) else None

        for i in range(len(timestamp_arr)):
            if fix_mask is not None and not bool(fix_mask[i]):
                continue
            sample_timestamp_us = int(timestamp_arr[i])
            if not (segment.start_us <= sample_timestamp_us <= segment.end_us):
                continue
            latitude_deg = float(lat_arr[i])
            longitude_deg = float(lon_arr[i])
            if not is_plausible_coordinate(latitude_deg, longitude_deg):
                continue
            points.append(TrajectoryPoint(latitude_deg, longitude_deg, sample_timestamp_us))

    points.sort(key=lambda point: point.timestamp_us)
    return points


def extract_trajectory(ulog, segment: FlightSegment) -> List[TrajectoryPoint]:
    """Extract every plausible trajectory point within `segment`'s window.

    :return: chronologically ordered points from `GPS_POSITION_TOPICS`
        (fix-gated), or from `FALLBACK_POSITION_TOPIC` if the primary
        topics yielded nothing usable in this window. `[]` if neither
        source has any plausible point -- callers must render a map
        without a trajectory for this flight, never invent one.
    """
    primary = _collect_points_in_window(ulog, GPS_POSITION_TOPICS, segment, require_fix=True)
    if primary:
        return primary
    return _collect_points_in_window(ulog, (FALLBACK_POSITION_TOPIC,), segment, require_fix=False)
