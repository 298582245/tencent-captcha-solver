# -*- coding: utf-8 -*-
"""Synthetic mouse trajectories for TDC.js (slider / click / image_select)."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class TrajectoryPoint:
    x: int
    y: int
    t: int


@dataclass
class Trajectory:
    kind: str = "click"
    points: list[TrajectoryPoint] = field(default_factory=list)
    total_ms: int = 0


def _ease_in_out_cubic(t: float) -> float:
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - ((-2.0 * t + 2.0) ** 3) / 2.0


def generate_click_trajectory(
    from_x: int,
    from_y: int,
    to_x: int,
    to_y: int,
    *,
    duration_ms: Optional[int] = None,
    interval_ms: int = 30,
) -> Trajectory:
    """Bézier-like move between two points."""
    if duration_ms is None:
        duration_ms = random.randint(200, 600)

    n_points = max(duration_ms // interval_ms, 2)
    cx = (from_x + to_x) // 2 + random.randint(-30, 30)
    cy = (from_y + to_y) // 2 + random.randint(-20, 20)
    points: list[TrajectoryPoint] = []

    for i in range(n_points):
        t = i / (n_points - 1)
        elapsed = int(t * duration_ms)
        x = int((1 - t) ** 2 * from_x + 2 * (1 - t) * t * cx + t**2 * to_x)
        y = int((1 - t) ** 2 * from_y + 2 * (1 - t) * t * cy + t**2 * to_y)
        points.append(TrajectoryPoint(x=x, y=y, t=elapsed))

    points[-1] = TrajectoryPoint(x=to_x, y=to_y, t=duration_ms)
    return Trajectory(kind="click", points=points, total_ms=duration_ms)


def merge_trajectories(segments: list[Trajectory], pause_range: tuple[int, int] = (100, 250)) -> Trajectory:
    if not segments:
        return Trajectory(kind="multi_click", points=[], total_ms=0)

    merged: list[TrajectoryPoint] = []
    offset = 0
    kind = segments[-1].kind if segments else "multi_click"
    for i, seg in enumerate(segments):
        for pt in seg.points:
            merged.append(TrajectoryPoint(x=pt.x, y=pt.y, t=pt.t + offset))
        offset += seg.total_ms
        if i < len(segments) - 1:
            offset += random.randint(*pause_range)

    return Trajectory(kind=kind, points=merged, total_ms=offset)


def _jittered_drift(cx: int, cy: int, *, n: int = 5, spread: int = 80, start_t: int = 0) -> tuple[list[TrajectoryPoint], int]:
    pts: list[TrajectoryPoint] = []
    t = start_t
    for _ in range(n):
        dx = random.randint(-spread, spread)
        dy = random.randint(-spread // 2, spread // 2)
        t += random.randint(80, 180)
        pts.append(TrajectoryPoint(x=cx + dx, y=cy + dy, t=t))
    return pts, t


def build_click_trajectory(
    target_x: int,
    target_y: int,
    *,
    canvas_w: int = 672,
    canvas_h: int = 480,
) -> Trajectory:
    sx = random.randint(50, max(51, canvas_w - 50))
    sy = random.randint(50, max(51, canvas_h - 50))
    drift_center_x = (sx + target_x) // 2
    drift_center_y = (sy + target_y) // 2

    drift_pts, drift_end_t = _jittered_drift(
        drift_center_x, drift_center_y, n=random.randint(4, 6)
    )
    approach = generate_click_trajectory(
        drift_pts[-1].x if drift_pts else sx,
        drift_pts[-1].y if drift_pts else sy,
        target_x,
        target_y,
        duration_ms=random.randint(250, 500),
    )
    approach_pts = [TrajectoryPoint(x=p.x, y=p.y, t=p.t + drift_end_t) for p in approach.points]
    all_pts = drift_pts + approach_pts
    if not all_pts or (all_pts[-1].x, all_pts[-1].y) != (target_x, target_y):
        all_pts.append(
            TrajectoryPoint(
                x=target_x,
                y=target_y,
                t=(all_pts[-1].t + 20) if all_pts else 0,
            )
        )
    return Trajectory(kind="click", points=all_pts, total_ms=all_pts[-1].t)


def build_image_select_trajectory(target_x: int, target_y: int, *, canvas_w: int = 672, canvas_h: int = 480) -> Trajectory:
    traj = build_click_trajectory(target_x, target_y, canvas_w=canvas_w, canvas_h=canvas_h)
    return Trajectory(points=traj.points, total_ms=traj.total_ms, kind="multi_click")
