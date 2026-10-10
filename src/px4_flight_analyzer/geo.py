"""Shared GPS field-sniffing logic, used by both `gps.py` and `trajectory.py`.

PX4 logs the vehicle's position under different topic/field names
depending on firmware version: older firmware logs `vehicle_gps_position`
with integer `lat`/`lon` fields scaled by 1e7, newer firmware logs
`sensor_gps` with floating-point `latitude_deg`/`longitude_deg` fields
already in degrees (confirmed by reading `pyulog/ulog2kml.py`, which
handles exactly this same old/new naming split). Rather than branch on
topic name, this module inspects the *actual* field names and declared
field *types* present in each dataset and scales accordingly -- this
also lets the same extraction logic handle `vehicle_global_position`
(typically `lat`/`lon` as `double`, already in degrees, no `fix_type`),
used only as a fallback when no real GPS fix is available.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

# Minimum fix_type considered a "real" fix: 0=no fix, 1=dead reckoning
# only, 2=2D fix, 3=3D fix and above -- matches the same threshold used
# by pyulog's own ulog2kml converter.
_MIN_USABLE_FIX_TYPE = 2


def extract_lat_lon_arrays(data) -> Optional[Tuple[object, object]]:
    """Extract (lat_degrees, lon_degrees) arrays from a dataset, scaling if needed.

    Checks the field's declared type (not the topic name) to decide
    whether integer-degrees-times-1e7 scaling applies, so this works
    uniformly across `vehicle_gps_position` (int32, scaled),
    `sensor_gps` (float, unscaled) and `vehicle_global_position`
    (double, unscaled) without hardcoding per-topic assumptions.
    """
    if "lat" in data.data and "lon" in data.data:
        lat_field_type = next(
            (f.type_str for f in data.field_data if f.field_name == "lat"), None
        )
        if lat_field_type == "int32_t":
            return data.data["lat"] / 1e7, data.data["lon"] / 1e7
        return data.data["lat"].astype(float), data.data["lon"].astype(float)

    if "latitude_deg" in data.data and "longitude_deg" in data.data:
        return data.data["latitude_deg"].astype(float), data.data["longitude_deg"].astype(float)

    return None


def is_plausible_coordinate(latitude_deg: float, longitude_deg: float) -> bool:
    if math.isnan(latitude_deg) or math.isnan(longitude_deg):
        return False
    if not (-90.0 <= latitude_deg <= 90.0):
        return False
    if not (-180.0 <= longitude_deg <= 180.0):
        return False
    if latitude_deg == 0.0 and longitude_deg == 0.0:
        # (0, 0) is the overwhelmingly common "GPS module never got a fix" sentinel.
        return False
    return True
