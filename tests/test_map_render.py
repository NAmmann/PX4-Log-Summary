"""Tests for trajectory map rendering (`map_render.py`).

Zero real sockets are opened anywhere in this suite: the
`fetch_online_tiles=False` path never imports `staticmap` at all, and
every exercise of the online path monkeypatches `staticmap.StaticMap.render`
directly instead of letting it make a real HTTP request.
"""

from __future__ import annotations

import logging

import pytest
from PIL import Image

from px4_flight_analyzer.map_render import (
    MAP_HEIGHT_PX,
    MAP_WIDTH_PX,
    assign_uav_colors,
    render_trajectory_map,
)
from px4_flight_analyzer.models import TrajectoryPoint, UavTrajectory


def _trajectory(name, color, points):
    return UavTrajectory(uav_name=name, color=color, points=points)


# --- assign_uav_colors -------------------------------------------------------------------------------


def test_assign_uav_colors_is_stable_and_distinct():
    colors = assign_uav_colors(["Falcon01", "Falcon02", "Falcon03"])
    assert len(colors) == 3
    assert len(set(colors.values())) == 3


def test_assign_uav_colors_wraps_around_palette():
    names = [f"UAV{i}" for i in range(10)]
    colors = assign_uav_colors(names)
    assert colors["UAV0"] == colors["UAV8"]  # palette currently has 8 entries


# --- blank-grid offline path, exercised directly via fetch_online_tiles=False ------------------------


def test_offline_blank_grid_zero_trajectories_does_not_raise():
    image = render_trajectory_map([], fetch_online_tiles=False)
    assert isinstance(image, Image.Image)
    assert image.size == (MAP_WIDTH_PX, MAP_HEIGHT_PX)


def test_offline_blank_grid_single_point_track():
    trajectory = _trajectory("Falcon01", "#1f77b4", [TrajectoryPoint(50.9375, 6.9603, 1_000_000)])
    image = render_trajectory_map([trajectory], fetch_online_tiles=False)
    assert isinstance(image, Image.Image)
    assert image.size == (MAP_WIDTH_PX, MAP_HEIGHT_PX)


def test_offline_blank_grid_all_identical_coordinates():
    points = [TrajectoryPoint(50.9375, 6.9603, ts) for ts in (1_000_000, 2_000_000, 3_000_000)]
    trajectory = _trajectory("Falcon01", "#1f77b4", points)
    image = render_trajectory_map([trajectory], fetch_online_tiles=False)
    assert isinstance(image, Image.Image)


def test_offline_blank_grid_multi_uav_multi_point():
    track_a = _trajectory(
        "Falcon01",
        "#1f77b4",
        [TrajectoryPoint(50.93, 6.95, 1_000_000), TrajectoryPoint(50.94, 6.96, 2_000_000)],
    )
    track_b = _trajectory(
        "Falcon02",
        "#d62728",
        [TrajectoryPoint(50.95, 6.97, 1_000_000), TrajectoryPoint(50.96, 6.98, 2_000_000)],
    )
    image = render_trajectory_map([track_a, track_b], fetch_online_tiles=False)
    assert isinstance(image, Image.Image)
    assert image.size == (MAP_WIDTH_PX, MAP_HEIGHT_PX)


def test_fetch_online_tiles_false_never_touches_staticmap(monkeypatch):
    import px4_flight_analyzer.map_render as map_render

    def _boom(trajectories):
        raise AssertionError("_try_render_online must not be called when fetch_online_tiles=False")

    monkeypatch.setattr(map_render, "_try_render_online", _boom)

    trajectory = _trajectory("Falcon01", "#1f77b4", [TrajectoryPoint(50.9375, 6.9603, 1_000_000)])
    image = render_trajectory_map([trajectory], fetch_online_tiles=False)
    assert isinstance(image, Image.Image)


# --- online path: real network calls are never made, only the render() seam is monkeypatched --------


def test_online_success_path_returns_staticmap_image(monkeypatch):
    import staticmap

    fixed_image = Image.new("RGB", (MAP_WIDTH_PX, MAP_HEIGHT_PX), "#abcdef")
    monkeypatch.setattr(staticmap.StaticMap, "render", lambda self, *a, **k: fixed_image)

    trajectory = _trajectory(
        "Falcon01",
        "#1f77b4",
        [TrajectoryPoint(50.93, 6.95, 1_000_000), TrajectoryPoint(50.94, 6.96, 2_000_000)],
    )
    image = render_trajectory_map([trajectory], fetch_online_tiles=True)
    assert image is fixed_image


def test_online_failure_path_falls_back_to_blank_grid(monkeypatch, caplog):
    import staticmap

    def _raise(self, *a, **k):
        raise RuntimeError("simulated tile fetch failure")

    monkeypatch.setattr(staticmap.StaticMap, "render", _raise)

    trajectory = _trajectory(
        "Falcon01",
        "#1f77b4",
        [TrajectoryPoint(50.93, 6.95, 1_000_000), TrajectoryPoint(50.94, 6.96, 2_000_000)],
    )
    with caplog.at_level(logging.WARNING):
        image = render_trajectory_map([trajectory], fetch_online_tiles=True)

    assert isinstance(image, Image.Image)
    assert image.size == (MAP_WIDTH_PX, MAP_HEIGHT_PX)
    assert any("falling back to offline map" in record.message for record in caplog.records)


def test_online_path_with_zero_usable_points_falls_back_without_calling_staticmap(monkeypatch):
    import staticmap

    def _boom(self, *a, **k):
        raise AssertionError("render() must not be called with zero usable trajectory points")

    monkeypatch.setattr(staticmap.StaticMap, "render", _boom)

    image = render_trajectory_map([_trajectory("Falcon01", "#1f77b4", [])], fetch_online_tiles=True)
    assert isinstance(image, Image.Image)
