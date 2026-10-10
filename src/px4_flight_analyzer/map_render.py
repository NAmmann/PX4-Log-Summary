"""Trajectory map rendering: real OpenStreetMap tiles when available, a
blank-grid fallback otherwise.

Fetching real map tiles is an explicit, documented deviation from this
project's previous "zero network calls" principle (see README
"Deliberate deviations") -- `staticmap` pulls tiles from
tile.openstreetmap.org over HTTPS. `fetch_online_tiles=False` skips
straight to the offline fallback without even importing `staticmap`;
any failure on the online path (DNS, connection, timeout, HTTP,
staticmap-internal) is caught and falls back the same way -- this
module's entry point never raises and never hangs indefinitely (the
installed `staticmap` version's own `tile_request_timeout` constructor
argument bounds every tile request).

The offline fallback draws a simple equirectangular-ish local-XY
projection (longitude scaled by `cos(mean_latitude)`) directly with
Pillow's `ImageDraw` -- already a `staticmap` dependency, so no second
heavy dependency like matplotlib is needed anywhere in this project.
"""

from __future__ import annotations

import logging
import math
from typing import Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

from .models import UavTrajectory

logger = logging.getLogger(__name__)

MAP_WIDTH_PX = 900
MAP_HEIGHT_PX = 650

# Tile fetch guard: never let a slow/unreachable tile server hang the run.
_TILE_REQUEST_TIMEOUT_SECONDS = 5.0
_TILE_USER_AGENT = "px4-flight-analyzer (flight-test-summary PDF map; no contact URL configured)"

_PALETTE = (
    "#1f77b4",  # blue
    "#d62728",  # red
    "#2ca02c",  # green
    "#ff7f0e",  # orange
    "#9467bd",  # purple
    "#8c564b",  # brown
    "#e377c2",  # pink
    "#17becf",  # cyan
)

_BLANK_BACKGROUND = "#ffffff"
_GRID_COLOR = "#dddddd"
_TEXT_COLOR = "#666666"
_GRID_STEP_PX = 50
_MAP_MARGIN_PX = 40

# Minimum degree span used as a divide-by-zero floor for degenerate
# (single-point or all-identical) trajectories, roughly 11 meters at
# the equator.
_MIN_DEGREE_SPAN = 0.0001


def assign_uav_colors(uav_names: List[str]) -> Dict[str, str]:
    """Assign each UAV name a stable color, cycling over the shared palette.

    Cycles over *every* UAV name seen across the whole report (not
    per-entry), so the same UAV keeps the same color on every page --
    the single source of truth shared by the map line color and the
    PDF legend swatch.
    """
    return {name: _PALETTE[index % len(_PALETTE)] for index, name in enumerate(uav_names)}


def _hex_to_rgb(color: str) -> Tuple[int, int, int]:
    stripped = color.lstrip("#")
    return tuple(int(stripped[i : i + 2], 16) for i in (0, 2, 4))


def render_trajectory_map(trajectories: List[UavTrajectory], fetch_online_tiles: bool) -> Image.Image:
    """Render a trajectory map for one flight event group.

    :param trajectories: one `UavTrajectory` per UAV in the group, each
        already colored via `assign_uav_colors`. Entries with zero
        points are tolerated (rendered without a track).
    :param fetch_online_tiles: `False` skips straight to the offline
        blank-grid renderer without importing `staticmap` at all.
    :return: always a valid `PIL.Image.Image` -- this function never
        raises, even when given zero usable trajectory points.
    """
    if fetch_online_tiles:
        online_image = _try_render_online(trajectories)
        if online_image is not None:
            return online_image
    return _render_blank_grid(trajectories)


def _try_render_online(trajectories: List[UavTrajectory]) -> Optional[Image.Image]:
    try:
        import staticmap  # imported lazily: never touched when fetch_online_tiles=False
    except Exception as error:  # pragma: no cover -- staticmap is a declared dependency
        logger.warning("Could not import 'staticmap' for online map rendering: %s", error)
        return None

    usable = [trajectory for trajectory in trajectories if trajectory.points]
    if not usable:
        return None

    try:
        static_map = staticmap.StaticMap(
            MAP_WIDTH_PX,
            MAP_HEIGHT_PX,
            padding_x=20,
            padding_y=20,
            tile_request_timeout=_TILE_REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": _TILE_USER_AGENT},
        )
        for trajectory in usable:
            coords = [(point.longitude_deg, point.latitude_deg) for point in trajectory.points]
            if len(coords) >= 2:
                static_map.add_line(staticmap.Line(coords, trajectory.color, 3))
            else:
                static_map.add_marker(staticmap.CircleMarker(coords[0], trajectory.color, 8))
        return static_map.render()
    except Exception as error:
        logger.warning("Online map tile fetch failed, falling back to offline map: %s", error)
        return None


def _project_points(trajectories: List[UavTrajectory]) -> Optional[Callable]:
    all_points = [point for trajectory in trajectories for point in trajectory.points]
    if not all_points:
        return None

    mean_lat = sum(point.latitude_deg for point in all_points) / len(all_points)
    lon_scale = math.cos(math.radians(mean_lat))

    min_lat = min(point.latitude_deg for point in all_points)
    max_lat = max(point.latitude_deg for point in all_points)
    min_lon = min(point.longitude_deg for point in all_points)
    max_lon = max(point.longitude_deg for point in all_points)

    lat_span = max(max_lat - min_lat, _MIN_DEGREE_SPAN)
    lon_span = max((max_lon - min_lon) * lon_scale, _MIN_DEGREE_SPAN)

    usable_width = MAP_WIDTH_PX - 2 * _MAP_MARGIN_PX
    usable_height = MAP_HEIGHT_PX - 2 * _MAP_MARGIN_PX
    scale = min(usable_width / lon_span, usable_height / lat_span)

    center_lat = (min_lat + max_lat) / 2
    center_lon = (min_lon + max_lon) / 2

    def _to_px(point):
        x = MAP_WIDTH_PX / 2 + (point.longitude_deg - center_lon) * lon_scale * scale
        y = MAP_HEIGHT_PX / 2 - (point.latitude_deg - center_lat) * scale
        return x, y

    return _to_px


def _render_blank_grid(trajectories: List[UavTrajectory]) -> Image.Image:
    image = Image.new("RGB", (MAP_WIDTH_PX, MAP_HEIGHT_PX), _BLANK_BACKGROUND)
    draw = ImageDraw.Draw(image)

    to_px = _project_points(trajectories)
    if to_px is None:
        text = "No trajectory data available"
        text_bbox = draw.textbbox((0, 0), text)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        draw.text(
            ((MAP_WIDTH_PX - text_width) / 2, (MAP_HEIGHT_PX - text_height) / 2),
            text,
            fill=_TEXT_COLOR,
        )
        return image

    for x in range(0, MAP_WIDTH_PX, _GRID_STEP_PX):
        draw.line([(x, 0), (x, MAP_HEIGHT_PX)], fill=_GRID_COLOR, width=1)
    for y in range(0, MAP_HEIGHT_PX, _GRID_STEP_PX):
        draw.line([(0, y), (MAP_WIDTH_PX, y)], fill=_GRID_COLOR, width=1)

    for trajectory in trajectories:
        if not trajectory.points:
            continue
        rgb = _hex_to_rgb(trajectory.color)
        pixel_points = [to_px(point) for point in trajectory.points]
        if len(pixel_points) >= 2:
            draw.line(pixel_points, fill=rgb, width=3)
        else:
            x, y = pixel_points[0]
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=rgb)

    return image
