"""Finite discovery covers and deterministic open routes for CUMCM B.

The cover concerns discovery (a direction OR near response), not localization.
It contains vertices of *every closed cell* intersecting the source disk.  In
particular, cells touching the disk at a single lattice vertex are retained.
See docs/coverage_proofs.md for continuous-domain and degeneracy proofs.
"""

from __future__ import annotations

from functools import lru_cache
import math
from typing import Iterable

Point = tuple[float, float]
DOMAIN_RADIUS = 1800.0
MIN_RECEPTION_RADIUS = 1000.0
INTERSECTION_TOLERANCE_M = 1e-7


def _point(value: Iterable[float]) -> Point:
    x, y = value
    x, y = float(x), float(y)
    if not (math.isfinite(x) and math.isfinite(y)):
        raise ValueError("Point coordinates must be finite")
    return x, y


def _distance_sq(a: Point, b: Point) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _segment_distance_sq(a: Point, b: Point) -> float:
    """Squared distance from the origin to a closed segment."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    denom = dx * dx + dy * dy
    if denom == 0:
        return a[0] ** 2 + a[1] ** 2
    t = max(0.0, min(1.0, -(a[0] * dx + a[1] * dy) / denom))
    return (a[0] + t * dx) ** 2 + (a[1] + t * dy) ** 2


def _triangle_distance_sq(vertices: tuple[Point, Point, Point]) -> float:
    """Conservative squared origin distance to a closed CCW triangle."""
    crosses = []
    edge_scale = 1.0
    for a, b in zip(vertices, vertices[1:] + vertices[:1]):
        crosses.append((b[0] - a[0]) * (-a[1]) - (b[1] - a[1]) * (-a[0]))
        edge_scale = max(edge_scale, math.dist(a, b))
    # Treat points microscopically outside an edge as inside, never conversely.
    if min(crosses) >= -INTERSECTION_TOLERANCE_M * edge_scale:
        return 0.0
    return min(_segment_distance_sq(a, b) for a, b in
               zip(vertices, vertices[1:] + vertices[:1]))


def _spacing(kind: str, spacing: float | None) -> float:
    defaults = {"square": 700.0, "triangular": 950.0}
    if kind not in defaults:
        raise ValueError("kind must be 'square' or 'triangular'")
    value = defaults[kind] if spacing is None else float(spacing)
    maximum = MIN_RECEPTION_RADIUS / math.sqrt(2.0) if kind == "square" else MIN_RECEPTION_RADIUS
    if not math.isfinite(value) or value <= 0 or value > maximum:
        raise ValueError(f"{kind} spacing must be finite and in (0, {maximum}]")
    return value


@lru_cache(maxsize=16)
def _directional_construction(kind: str, spacing: float) -> tuple[tuple[Point, ...], int]:
    selected: set[tuple[int, int]] = set()
    cell_count = 0
    limit_sq = (DOMAIN_RADIUS + INTERSECTION_TOLERANCE_M) ** 2
    if kind == "square":
        lo = math.floor(-DOMAIN_RADIUS / spacing) - 1
        hi = math.ceil(DOMAIN_RADIUS / spacing) + 1
        for i in range(lo, hi + 1):
            for j in range(lo, hi + 1):
                x0, x1 = i * spacing, (i + 1) * spacing
                y0, y1 = j * spacing, (j + 1) * spacing
                dx = max(x0, 0.0, -x1)
                dy = max(y0, 0.0, -y1)
                if dx * dx + dy * dy <= limit_sq:
                    cell_count += 1
                    selected.update(((i, j), (i + 1, j), (i, j + 1), (i + 1, j + 1)))
        result = tuple(sorted((i * spacing, j * spacing) for i, j in selected))
    else:
        height = math.sqrt(3.0) * spacing / 2.0

        def vertex(index: tuple[int, int]) -> Point:
            i, j = index
            return spacing * (i + j / 2.0), height * j

        # Any intersecting cell has a vertex within R+s of the origin. In the
        # oblique lattice this bound covers |j| and |i|, plus a closed-cell halo.
        bound = math.ceil(2.0 * DOMAIN_RADIUS / spacing) + 4
        for i in range(-bound, bound + 1):
            for j in range(-bound, bound + 1):
                a, b, c, d = (i, j), (i + 1, j), (i, j + 1), (i + 1, j + 1)
                for indices in ((a, b, c), (b, d, c)):
                    triangle = tuple(vertex(index) for index in indices)
                    if _triangle_distance_sq(triangle) <= limit_sq:
                        cell_count += 1
                        selected.update(indices)
        result = tuple(sorted(vertex(index) for index in selected))
    return result, cell_count


def omnidirectional_points() -> list[Point]:
    """Origin plus six points on radius 900*sqrt(3); covering radius <=900 m."""
    r = 900.0 * math.sqrt(3.0)
    result = [(0.0, 0.0)]
    for k in range(6):
        x, y = r * math.cos(k * math.pi / 3.0), r * math.sin(k * math.pi / 3.0)
        result.append((0.0 if abs(x) < 1e-10 else x, 0.0 if abs(y) < 1e-10 else y))
    return result


def directional_points(kind: str = "square", spacing: float | None = None) -> list[Point]:
    """Vertices of all closed cells intersecting the radius-1800 source disk.

    Defaults: square side 700 m, equilateral-triangle side 950 m.  A triangle
    side of 990 m is also supported. Cell diameter must not exceed 1000 m.
    Returned vertices are unique, sorted, and may lie outside the source disk.
    """
    points, _ = _directional_construction(kind, _spacing(kind, spacing))
    return list(points)


def coverage_certificate(kind: str = "square", spacing: float | None = None) -> dict:
    """JSON-serializable construction metadata; proof is analytic, not a grid test."""
    common = {
        "certificate_type": "analytic_construction_metadata",
        "proof_path": "docs/coverage_proofs.md",
        "domain_radius_m": DOMAIN_RADIUS,
        "minimum_reception_radius_m": MIN_RECEPTION_RADIUS,
        "guarantees_localization": False,
        "requires_scan_of_each_unresolved_channel": True,
    }
    if kind in ("omnidirectional", "omni"):
        if spacing is not None:
            raise ValueError("The omnidirectional construction has a fixed ring radius")
        return {**common, "kind": "omnidirectional", "point_count": 7,
                "cell_count": 0, "spacing_m": None, "max_cell_diameter_m": None,
                "ring_radius_m": 900.0 * math.sqrt(3.0), "covering_radius_bound_m": 900.0,
                "guarantee": "Every source in the closed disk is within 900 m of a scan point."}
    h = _spacing(kind, spacing)
    points, count = _directional_construction(kind, h)
    diameter = h * math.sqrt(2.0) if kind == "square" else h
    return {**common, "kind": kind, "point_count": len(points), "cell_count": count,
            "spacing_m": h, "max_cell_diameter_m": diameter,
            "distance_margin_m": MIN_RECEPTION_RADIUS - diameter,
            "intersection_tolerance_m": INTERSECTION_TOLERANCE_M,
            "cell_selection": "closed cell minimum distance to disk center <= disk radius (outward tolerance)",
            "includes_all_incident_cells_at_edges_and_vertices": True,
            "guarantee": "For every source and directional normal, a distinct selected vertex is within the cell-diameter bound and has strictly positive projection on the normal."}


def route_length(points: Iterable[Point], start: Point = (0.0, 0.0)) -> float:
    """Length of an open route, including travel from start, without returning."""
    previous = _point(start)
    total = 0.0
    for raw in points:
        point = _point(raw)
        total += math.dist(previous, point)
        previous = point
    return total


def order_route(points: Iterable[Point], start: Point = (0.0, 0.0)) -> list[Point]:
    """Nearest-neighbor followed by open 2-opt; preserve every distinct vertex.

    The fixed initial location is not inserted unless it is a supplied point.
    This function reorders only: it does not prune coverage points or mutate
    its input. The returned open route has a free terminal point.
    """
    start = _point(start)
    remaining = set(_point(point) for point in points)
    route: list[Point] = []
    previous = start
    while remaining:
        chosen = min(remaining, key=lambda p: (_distance_sq(previous, p), p[0], p[1]))
        route.append(chosen)
        remaining.remove(chosen)
        previous = chosen
    n = len(route)
    while n > 1:
        best_gain = 1e-8
        best = None
        for i in range(n - 1):
            before = start if i == 0 else route[i - 1]
            for j in range(i + 1, n):
                removed = math.dist(before, route[i])
                added = math.dist(before, route[j])
                if j + 1 < n:
                    removed += math.dist(route[j], route[j + 1])
                    added += math.dist(route[i], route[j + 1])
                gain = removed - added
                if gain > best_gain:
                    best_gain, best = gain, (i, j)
        if best is None:
            break
        i, j = best
        route[i:j + 1] = reversed(route[i:j + 1])
    return route
