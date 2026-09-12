"""Observation-only continuous optical covers of a conservative convex hull.

The certificate is a finite partition proof, not a sampled coverage claim.
Every retained rectangle lies inside its center's clearance disk. A rectangle
may be removed only if that entire rectangle is disjoint from the hull.
"""
from __future__ import annotations

import math

from bsolver.coverage import order_route, route_length
from bsolver.geometry import distance_to_polygon
from bsolver.sensing import optical_fallback_points

_TOL = 1e-6


def cover_cost(points, current):
    """Worst remaining virtual seconds, including the final success premium."""
    return route_length(points, current) / 5.0 + 3.0 * len(points) + (2.0 if points else 0.0)


def _finite_point(raw):
    point = float(raw[0]), float(raw[1])
    if not all(math.isfinite(x) for x in point):
        raise ValueError("cover coordinates must be finite")
    return point


def _project(p, origin, c, s):
    x, y = p[0] - origin[0], p[1] - origin[1]
    return c * x + s * y, -s * x + c * y


def _world(p, origin, c, s):
    return origin[0] + c * p[0] - s * p[1], origin[1] + s * p[0] + c * p[1]


def _supporting_slabs(hull):
    """Unit-normal slabs containing H, valid for either orientation/degeneracy."""
    directions = {(1.0, 0.0), (0.0, 1.0)}
    for p, q in zip(hull, hull[1:] + hull[:1]):
        dx, dy = q[0] - p[0], q[1] - p[1]
        length = math.hypot(dx, dy)
        if length > _TOL:
            directions.add((-dy / length, dx / length))
    slabs = []
    for a, b in sorted(directions):
        values = [a * p[0] + b * p[1] for p in hull]
        slabs.append((a, b, min(values), max(values)))
    return slabs


def _distance_lower_bound(point, slabs):
    # Unit-normal projection is 1-Lipschitz. Any distance to a containing
    # slab is therefore a lower bound on distance to H. Conservative even at
    # corners and for point/segment hulls; no per-cell polygon reconstruction.
    bound = 0.0
    for a, b, lo, hi in slabs:
        value = a * point[0] + b * point[1]
        bound = max(bound, lo - value, value - hi)
    return bound


def _axes(knowledge, hull, mode):
    first = getattr(knowledge, "first_direction", None)
    bearing = math.radians(first.angle) if first is not None else 0.0
    raw = [bearing]
    if mode == "bbox":
        raw.append(0.0)
        for p, q in zip(hull, hull[1:] + hull[:1]):
            if math.dist(p, q) > _TOL:
                raw.append(math.atan2(q[1] - p[1], q[0] - p[0]))
    # Rectangle axes are unchanged by a quarter turn. Deduplication affects
    # candidate search only; each retained orientation has a fresh certificate.
    unique = {}
    for angle in raw:
        angle %= math.pi / 2
        unique.setdefault(round(angle, 10), angle)
    return list(unique.values())


def _dimensions(width, height, radius):
    """Enumerate anisotropic cells with half diagonal strictly below radius."""
    diameter = 2 * (radius - 1e-7)
    candidates = set()
    # For any fixed number of rows, choose the smallest certified column count.
    # Also enumerate the transposed construction so elongated cells work along
    # either axis. This is a finite candidate search, not a global optimum claim.
    for a, b, transpose in ((width, height, False), (height, width, True)):
        upper = max(1, math.ceil(b / (radius * math.sqrt(2))) + 2)
        for nb in range(1, upper + 1):
            side_b = b / nb
            if side_b >= diameter:
                continue
            side_a = math.sqrt(max(0.0, diameter * diameter - side_b * side_b))
            if side_a <= 0:
                continue
            na = max(1, math.ceil(a / side_a))
            candidates.add((nb, na) if transpose else (na, nb))
    # Dominated subdivisions may still reduce travel, so retain them.
    return sorted(candidates, key=lambda n: (n[0] * n[1], n))


def _snake_routes(indexed, current):
    """Eight row/column snakes preserve the complete set of cells."""
    best, length = None, math.inf
    for transpose in (False, True):
        for outer_reverse in (False, True):
            for inner_reverse in (False, True):
                def key(item):
                    i, j, _ = item
                    outer, inner = (i, j) if transpose else (j, i)
                    backwards = bool(outer % 2) ^ inner_reverse
                    return (-outer if outer_reverse else outer, -inner if backwards else inner)
                route = [item[2] for item in sorted(indexed, key=key)]
                candidate_length = route_length(route, current)
                if candidate_length < length:
                    best, length = route, candidate_length
    return best or []


def _baseline(knowledge, hull, current, radius):
    first = getattr(knowledge, "first_direction", None)
    if first is None:
        return None, []
    backup = [_finite_point(p) for p in optical_fallback_points(first)]
    # Check that the actual hull fits the rectangle tiled by the original grid.
    # This is needed for standalone use and unusual epsilon settings.
    a = math.radians(first.angle)
    local = [_project(p, first.position, math.cos(a), math.sin(a)) for p in hull]
    bound = math.hypot(14.0, 14.0)
    eligible = radius >= bound + _TOL and all(
        -14.0 + _TOL <= x <= 1526.0 - _TOL and -28.0 + _TOL <= y <= 28.0 - _TOL
        for x, y in local
    )
    if not eligible:
        return None, backup
    ordered = list(backup)
    if math.dist(current, ordered[-1]) < math.dist(current, ordered[0]):
        ordered.reverse()
    points = [p for p in ordered if distance_to_polygon(hull, p) <= 20.0 + 1e-5]
    if not points:
        return None, backup
    return (points, {
        "kind": "original_strip_cover",
        "covering_radius_bound_m": bound,
        "original_bounds": [-14.0, 1526.0, -28.0, 28.0],
        "first_direction_position": list(first.position),
        "first_direction_deg": first.angle,
        "pruning_rule": "discard center only when distance(center,hull)>20+1e-5",
        "discarded_count": len(backup) - len(points),
    }), backup


def build_cover(knowledge, current, mode="bbox", radius=19.999):
    """Return ``(ordered_points, certificate)`` using only legal knowledge.

    ``bbox`` considers each hull-edge orientation and the initial bearing;
    ``strip`` uses only the initial bearing. Both compete against the original
    finite fallback when its rectangle provably contains this hull. Neither
    uses particles, actual source positions, radii, orientations, or case IDs.

    Callers should first try the existing single-point safe-clear certificate.
    Keep the original fallback on numerical/construction failure, and execute
    these as attempts (not individually guaranteed successes). Do not mark a
    source cleared until the API explicitly returns success.
    """
    if mode not in ("bbox", "strip"):
        raise ValueError("cover mode must be bbox or strip")
    if not math.isfinite(radius) or not 0.001 < radius <= 19.999:
        raise ValueError("radius must be finite, >0.001 and <=19.999")
    hull = [_finite_point(p) for p in knowledge.hull]
    if not hull:
        raise ValueError("empty hull cannot certify an optical cover")
    current = _finite_point(current)
    origin = hull[0]
    slabs = _supporting_slabs(hull)
    baseline, backup = _baseline(knowledge, hull, current, radius)
    best_points, best_certificate = baseline if baseline is not None else (None, None)
    baseline_cost = cover_cost(best_points, current) if best_points is not None else None
    best_cost = baseline_cost if baseline_cost is not None else math.inf
    finalists = []
    searched = 0
    for angle in _axes(knowledge, hull, mode):
        c, s = math.cos(angle), math.sin(angle)
        local = [_project(p, origin, c, s) for p in hull]
        # Outward padding covers projection, reconstruction, and cell-edge
        # floating error. It also treats a point or segment without division by 0.
        lo_x, hi_x = min(p[0] for p in local) - _TOL, max(p[0] for p in local) + _TOL
        lo_y, hi_y = min(p[1] for p in local) - _TOL, max(p[1] for p in local) + _TOL
        width, height = hi_x - lo_x, hi_y - lo_y
        for nx, ny in _dimensions(width, height, radius):
            # A grossly overfine candidate cannot beat a known cost even if all
            # of its walking were free. We cannot apply this count bound before
            # hull-disjoint pruning, so only bound memory here.
            if nx * ny > 25000:
                continue
            dx, dy = width / nx, height / ny
            cell_radius = math.hypot(dx, dy) / 2
            if cell_radius > radius - 5e-8:
                continue
            indexed, omitted = [], []
            for j in range(ny):
                for i in range(nx):
                    point = _world((lo_x + (i + .5) * dx, lo_y + (j + .5) * dy), origin, c, s)
                    distance_bound = _distance_lower_bound(point, slabs)
                    # The cell is inside this smaller circumdisk; strict
                    # separation implies the ENTIRE cell misses the hull.
                    if distance_bound > cell_radius + _TOL:
                        omitted.append([i, j, distance_bound])
                    else:
                        indexed.append((i, j, point))
            if not indexed or 3 * len(indexed) + 2 > best_cost:
                continue
            points = _snake_routes(indexed, current)
            cost = cover_cost(points, current)
            searched += 1
            cert = {
                "kind": "rotated_rectangle_partition",
                "origin": list(origin), "angle_rad": angle,
                "bounds": [lo_x, hi_x, lo_y, hi_y],
                "subdivisions": [nx, ny], "cell_size_m": [dx, dy],
                "covering_radius_bound_m": cell_radius,
                "kept_cells": [[i, j] for i, j, _ in indexed],
                "omitted_cells": omitted,
                "pruning_rule": "supporting_slab_distance_lower_bound(center,hull)>cell_half_diagonal+1e-6",
                "supporting_slabs": [list(slab) for slab in slabs],
                "domain": "entire conservative convex hull; cell partition proof",
            }
            finalists.append((cost, points, cert))
            if cost < best_cost:
                best_points, best_certificate, best_cost = points, cert, cost
    # More expensive reorder is limited to six already competitive candidates.
    # A route permutation does not affect any of the coverage certificates.
    for _, points, cert in sorted(finalists, key=lambda x: x[0])[:6]:
        optimized = order_route(points, current)
        cost = cover_cost(optimized, current)
        if cost < best_cost:
            best_points, best_certificate, best_cost = optimized, cert, cost
    if best_points is None:
        raise ValueError("no bounded-size certified cover; use original finite fallback")
    certificate = {
        **best_certificate,
        "type": "continuous_optical_cover_attempt",
        "requested_mode": mode,
        "radius_m": radius,
        "point_count": len(best_points),
        "route_distance_m": route_length(best_points, current),
        "worst_case_cost_s": best_cost,
        "baseline_cost_s": baseline_cost,
        "baseline_point_count": len(baseline[0]) if baseline else None,
        "candidate_count": searched,
        "backup_points": backup,
        "individual_attempt_guaranteed": False,
        "failed_clear_pruning": False,
        "proof": "docs/clear_cover.md",
    }
    return best_points, certificate
