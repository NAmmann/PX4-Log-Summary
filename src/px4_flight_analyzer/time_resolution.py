"""Mapping a file's internal (boot-relative) clock to real-world time.

PX4 timestamps everything internally in microseconds since boot -- a
monotonic counter that resets on every power cycle and has no inherent
relationship to the wall clock. GPS modules are the only onboard source
of an actual UTC reference: `vehicle_gps_position`/`sensor_gps` carry a
`time_utc_usec` field (UTC epoch microseconds) alongside the same
internal timestamp used by every other topic. Finding one plausible
`(internal_timestamp, time_utc_usec)` pair gives a single scalar offset
that converts *any* internal timestamp in that file to UTC, because the
internal clock is one continuous monotonic counter for the whole boot
session -- a per-segment offset would be meaningless (and wrong, since
it would imply the clock restarted between flights in the same file).

If no such anchor exists anywhere in the file (no GPS topic logged, no
fix ever acquired, or only implausible/zero `time_utc_usec` values), the
file's flights are still exported, just with blank `Datum`/`StartZeit`/
`LandeZeit` -- never a guessed or invented time.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Callable, Optional, Tuple
from zoneinfo import ZoneInfo

from .models import FlightSegment, TimeReferenceSource, TimeResolution
from .parser import get_dataset_safe

# Sanity window for an acceptable `time_utc_usec` anchor value: a GPS
# module reporting a date before 2000 or more than 5 years in the future
# is treated as not having a trustworthy time fix yet, not as "the
# flight happened at that date".
_MIN_PLAUSIBLE_UTC_US = 946_684_800_000_000  # 2000-01-01T00:00:00Z in epoch microseconds
_FUTURE_SANITY_WINDOW_DAYS = 5 * 365

# Minimum fix_type considered a "real" fix (matches gps.py's threshold).
_MIN_USABLE_FIX_TYPE = 2

_UTC_TIME_TOPICS = ("vehicle_gps_position", "sensor_gps")


def _max_plausible_utc_us() -> int:
    horizon = datetime.now(dt_timezone.utc) + timedelta(days=_FUTURE_SANITY_WINDOW_DAYS)
    return int(horizon.timestamp() * 1_000_000)


def find_utc_offset(ulog) -> Optional[int]:
    """Find the GPS-UTC offset for a file, if any plausible anchor exists.

    Scans every sample (not just transitions) of `time_utc_usec` across
    both GPS topics at the primary vehicle instance, and picks the
    *earliest* (by internal timestamp) sample that has a nonzero,
    plausible UTC value and, where `fix_type` is available, an actual
    fix. Using the earliest qualifying anchor keeps the offset stable
    and representative of the whole recording rather than of whichever
    GPS topic happened to be scanned first.

    :return: `time_utc_usec - internal_timestamp` of the chosen anchor
        sample, or None if no topic yields a usable anchor at all.
    """
    max_utc_us = _max_plausible_utc_us()
    best_internal_us: Optional[int] = None
    best_offset_us: Optional[int] = None

    for topic in _UTC_TIME_TOPICS:
        data = get_dataset_safe(ulog, topic)
        if data is None or "time_utc_usec" not in data.data or "timestamp" not in data.data:
            continue

        internal_timestamps = data.data["timestamp"]
        utc_values = data.data["time_utc_usec"]
        fix_values = data.data["fix_type"] if "fix_type" in data.data else None

        for index in range(len(internal_timestamps)):
            utc_us = int(utc_values[index])
            if utc_us == 0:
                continue
            if not (_MIN_PLAUSIBLE_UTC_US <= utc_us <= max_utc_us):
                continue
            if fix_values is not None and int(fix_values[index]) < _MIN_USABLE_FIX_TYPE:
                continue

            internal_us = int(internal_timestamps[index])
            if best_internal_us is None or internal_us < best_internal_us:
                best_internal_us = internal_us
                best_offset_us = utc_us - internal_us

    return best_offset_us


def resolve_file_time_reference(ulog) -> Tuple[TimeReferenceSource, Optional[int]]:
    """Determine the time reference for an entire file, once per file.

    :return: `(TimeReferenceSource.GPS_UTC, offset_us)` if a plausible
        anchor was found, otherwise `(TimeReferenceSource.NICHT_ERMITTELBAR, None)`.
    """
    offset_us = find_utc_offset(ulog)
    if offset_us is None:
        return TimeReferenceSource.NICHT_ERMITTELBAR, None
    return TimeReferenceSource.GPS_UTC, offset_us


def resolve_segment_time(
    source: TimeReferenceSource,
    utc_offset_us: Optional[int],
    segment: FlightSegment,
    display_timezone: ZoneInfo,
) -> TimeResolution:
    """Apply a file's time reference to one specific segment's boundaries.

    Converts via true UTC first (`time_utc_usec` is UTC epoch
    microseconds) and only then into `display_timezone`, so DST
    transitions and date changes across a timezone boundary are handled
    by the standard library rather than by manual offset arithmetic.
    """
    if source is not TimeReferenceSource.GPS_UTC or utc_offset_us is None:
        return TimeResolution(
            source=TimeReferenceSource.NICHT_ERMITTELBAR,
            utc_offset_us=None,
            resolved_start_utc=None,
            resolved_end_utc=None,
        )

    start_utc = datetime.fromtimestamp((segment.start_us + utc_offset_us) / 1_000_000, tz=dt_timezone.utc)
    end_utc = datetime.fromtimestamp((segment.end_us + utc_offset_us) / 1_000_000, tz=dt_timezone.utc)
    return TimeResolution(
        source=TimeReferenceSource.GPS_UTC,
        utc_offset_us=utc_offset_us,
        resolved_start_utc=start_utc.astimezone(display_timezone),
        resolved_end_utc=end_utc.astimezone(display_timezone),
    )


def format_absolute_time(moment: datetime) -> str:
    """Format a tz-aware datetime as `HH:MM:SS` local wall-clock time."""
    return moment.strftime("%H:%M:%S")


def format_relative_time(internal_us: int, segment_start_us: int) -> str:
    """Format elapsed time since a segment's own liftoff as `+HH:MM:SS`.

    Used for message timestamps when a file has no GPS-UTC time
    reference at all: pure internal-microsecond arithmetic, always
    available, always non-negative given messages are only ever
    attributed to a segment within `[start_us, end_us]`.
    """
    delta_us = max(0, internal_us - segment_start_us)
    total_seconds = delta_us // 1_000_000
    hours, remainder_seconds = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder_seconds, 60)
    return f"+{hours:02d}:{minutes:02d}:{seconds:02d}"


def make_time_label_fn(
    source: TimeReferenceSource,
    utc_offset_us: Optional[int],
    segment_start_us: int,
    display_timezone: ZoneInfo,
) -> Callable[[int], str]:
    """Build a per-message timestamp formatter for one segment.

    Used by `error_analysis.analyze_segment` to label each individual
    message: absolute local wall-clock time when the file has a
    GPS-UTC reference, otherwise elapsed time since this segment's own
    liftoff.
    """
    if source is TimeReferenceSource.GPS_UTC and utc_offset_us is not None:

        def _absolute_label(internal_us: int) -> str:
            moment_utc = datetime.fromtimestamp((internal_us + utc_offset_us) / 1_000_000, tz=dt_timezone.utc)
            return format_absolute_time(moment_utc.astimezone(display_timezone))

        return _absolute_label

    def _relative_label(internal_us: int) -> str:
        return format_relative_time(internal_us, segment_start_us)

    return _relative_label
