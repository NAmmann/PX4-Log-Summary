"""GPS start-coordinate resolution.

Field-sniffing (which topic/field names hold lat/lon, and how to scale
them) lives in `geo.py`, shared with `trajectory.py`'s full-track
extraction. This module adds the start-position-specific policy on top:
within whichever topic has usable data, the GPS sample *closest in time*
to the flight's actual liftoff is selected -- never simply the first GPS
sample in the file, which could belong to a different, earlier part of
a multi-flight recording, or to a GPS dropout/recovery. A hard 5-minute
search bound (`MAX_GPS_TIME_DELTA_US`) prevents attributing a position
that is technically "the closest match in the file" but still clearly
unrelated to this particular liftoff.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from .geo import _MIN_USABLE_FIX_TYPE, extract_lat_lon_arrays, is_plausible_coordinate
from .models import FlightSegment, GpsPosition
from .parser import get_dataset_safe

logger = logging.getLogger(__name__)

GPS_POSITION_TOPICS = ("vehicle_gps_position", "sensor_gps")
FALLBACK_POSITION_TOPIC = "vehicle_global_position"

# A GPS sample farther than this from the detected liftoff is considered
# unrelated to the flight, even if it is technically the closest one
# found in the file.
MAX_GPS_TIME_DELTA_US = 5 * 60 * 1_000_000

_CandidateTuple = Tuple[int, int, float, float, str, bool]  # (delta, ts, lat, lon, topic, fix_gated)


def _collect_candidates(
    ulog, topic_names: Tuple[str, ...], reference_us: int, require_fix: bool
) -> List[_CandidateTuple]:
    candidates: List[_CandidateTuple] = []
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
        fix_gated_applied = require_fix and has_fix_type
        fix_mask = (data.data["fix_type"] > _MIN_USABLE_FIX_TYPE) if fix_gated_applied else None

        for i in range(len(timestamp_arr)):
            if fix_mask is not None and not bool(fix_mask[i]):
                continue
            latitude_deg = float(lat_arr[i])
            longitude_deg = float(lon_arr[i])
            if not is_plausible_coordinate(latitude_deg, longitude_deg):
                continue
            sample_timestamp_us = int(timestamp_arr[i])
            delta_us = abs(sample_timestamp_us - reference_us)
            candidates.append(
                (delta_us, sample_timestamp_us, latitude_deg, longitude_deg, topic, fix_gated_applied)
            )
    return candidates


def _best_within_bound(candidates: List[_CandidateTuple]) -> Optional[GpsPosition]:
    if not candidates:
        return None
    candidates.sort(key=lambda candidate: candidate[0])
    delta_us, sample_timestamp_us, latitude_deg, longitude_deg, topic, fix_gated = candidates[0]
    if delta_us > MAX_GPS_TIME_DELTA_US:
        return None
    return GpsPosition(
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        source_topic=topic,
        sample_timestamp_us=sample_timestamp_us,
        fix_gated=fix_gated,
    )


def resolve_start_position(ulog, segment: FlightSegment) -> Optional[GpsPosition]:
    """Find the best available GPS start position for `segment`.

    Tries `GPS_POSITION_TOPICS` first, requiring a usable fix where a
    `fix_type` field is available; falls back to `FALLBACK_POSITION_TOPIC`
    (no fix gating available there) only if nothing usable was found.

    :return: the closest plausible position within `MAX_GPS_TIME_DELTA_US`
        of `segment.start_us`, or None if no such position exists --
        callers must leave `StartLatitude`/`StartLongitude` blank in that
        case, never guess.
    """
    primary_candidates = _collect_candidates(ulog, GPS_POSITION_TOPICS, segment.start_us, require_fix=True)
    primary = _best_within_bound(primary_candidates)
    if primary is not None:
        return primary

    fallback_candidates = _collect_candidates(
        ulog, (FALLBACK_POSITION_TOPIC,), segment.start_us, require_fix=False
    )
    fallback = _best_within_bound(fallback_candidates)
    if fallback is not None:
        logger.info(
            "%s segment #%d: no GPS fix available near liftoff; using fallback topic '%s'",
            segment.source_file,
            segment.segment_index,
            FALLBACK_POSITION_TOPIC,
        )
        return fallback

    logger.warning(
        "%s segment #%d: no reliable GPS start position found (missing data, no fix, "
        "implausible coordinates, or nothing within %.0f s of liftoff)",
        segment.source_file,
        segment.segment_index,
        MAX_GPS_TIME_DELTA_US / 1_000_000,
    )
    return None
