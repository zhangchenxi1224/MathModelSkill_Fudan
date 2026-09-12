"""Coverage-preserving orderings for estimated first-clear latency.

Uniform hull mass is a deterministic working approximation, not a calibrated
posterior. Only the order of supplied cover points changes; every original
point, including zero-mass and repeated points, remains in the result.
"""
from __future__ import annotations

from bisect import bisect_left
import math

_SAMPLE_COUNT = 96
_CLEAR_RADIUS_SQ = 20.0 ** 2


def _point(raw):
    value = float(raw[0]), float(raw[1])
    if not all(math.isfinite(x) for x in value):
        raise ValueError("cover-order coordinates must be finite")
    return value


def _radical_inverse(number, base):
    result, scale = 0.0, 1.0 / base
    while number:
        number, digit = divmod(number, base)
        result += digit * scale
        scale /= base
    return result


def working_samples(hull, count=_SAMPLE_COUNT):
    """Fixed-size deterministic samples lying in the supplied convex hull.

    Positive-area hulls use an area-weighted triangle fan and square-root
    barycentric mapping. Each triangle stratum uses low-discrepancy coordinates.
    Segments use equally spaced midpoints; points repeat their one position.
    The input is assumed to be a convex polygon in boundary order.
    """
    vertices = [_point(p) for p in hull]
    if not vertices:
        raise ValueError("empty hull cannot define working position mass")
    if not isinstance(count, int) or not 1 <= count <= 128:
        raise ValueError("sample count must be an integer from 1 to 128")
    anchor = vertices[0]
    triangles, cumulative, area_twice = [], [], 0.0
    for p, q in zip(vertices[1:-1], vertices[2:]):
        area = abs((p[0] - anchor[0]) * (q[1] - anchor[1])
                   - (p[1] - anchor[1]) * (q[0] - anchor[0]))
        if area > 0.0:
            triangles.append((p, q))
            area_twice += area
            cumulative.append(area_twice)
    if area_twice <= 1e-12:
        spread_x = max(p[0] for p in vertices) - min(p[0] for p in vertices)
        spread_y = max(p[1] for p in vertices) - min(p[1] for p in vertices)
        axis = int(spread_y > spread_x)
        left, right = min(vertices, key=lambda p: p[axis]), max(vertices, key=lambda p: p[axis])
        return [(left[0] + (right[0] - left[0]) * (i + .5) / count,
                 left[1] + (right[1] - left[1]) * (i + .5) / count)
                for i in range(count)]
    samples = []
    for i in range(count):
        target_area = area_twice * (i + .5) / count
        triangle = min(bisect_left(cumulative, target_area), len(triangles) - 1)
        p, q = triangles[triangle]
        radial = math.sqrt(_radical_inverse(i + 1, 2))
        side = _radical_inverse(i + 1, 3)
        # Convex combinations guarantee containment; no rejection loop or
        # hidden geometry oracle enters the sampling process.
        wa, wp, wq = 1.0 - radial, radial * (1.0 - side), radial * side
        samples.append((wa * anchor[0] + wp * p[0] + wq * q[0],
                        wa * anchor[1] + wp * p[1] + wq * q[1]))
    return samples


def _masks(points, samples):
    result = []
    for px, py in points:
        mask = 0
        for i, (sx, sy) in enumerate(samples):
            if (px - sx) ** 2 + (py - sy) ** 2 <= _CLEAR_RADIUS_SQ:
                mask |= 1 << i
        result.append(mask)
    return result


def _nearest_indices(points, current, remaining=None):
    remaining = list(range(len(points))) if remaining is None else list(remaining)
    route = []
    while remaining:
        chosen = min(remaining, key=lambda i: (math.dist(current, points[i]), i))
        remaining.remove(chosen)
        route.append(chosen)
        current = points[chosen]
    return route


def _mass_indices(points, masks, current):
    remaining = list(range(len(points)))
    route, covered = [], 0
    while remaining:
        chosen, best_score, best_cost = None, -1.0, math.inf
        for i in remaining:
            count = (masks[i] & ~covered).bit_count()
            cost = math.dist(current, points[i]) / 5.0 + 3.0
            score = count / cost
            if score > best_score or (score == best_score and cost < best_cost):
                chosen, best_score, best_cost = i, score, cost
        # A zero-sample circle still contributes to continuous coverage. Append
        # ALL remaining centers by nearest neighbor, even if every sample was
        # already hit or no sample lies in any remaining clearance circle.
        if best_score <= 0:
            route.extend(_nearest_indices(points, current, remaining))
            break
        remaining.remove(chosen)
        route.append(chosen)
        covered |= masks[chosen]
        current = points[chosen]
    return route


def _evaluate(indices, points, masks, current, sample_count):
    covered, elapsed, weighted_hit_time, length = 0, 0.0, 0.0, 0.0
    for i in indices:
        step = math.dist(current, points[i])
        length += step
        elapsed += step / 5.0 + 3.0
        newly_hit = masks[i] & ~covered
        weighted_hit_time += newly_hit.bit_count() * (elapsed + 2.0)
        covered |= masks[i]
        current = points[i]
    uncovered = sample_count - covered.bit_count()
    # If input circles do not cover some samples, report the count explicitly.
    # Their score is the full traversal time, a censored latency proxy; it is
    # not a success prediction. All competing permutations miss the same mass.
    end_time = elapsed + (2.0 if indices else 0.0)
    return {
        "predicted_latency_score_s": (weighted_hit_time + uncovered * end_time) / sample_count,
        "route_distance_m": length,
        "full_traversal_cost_s": end_time,
        "uncovered_sample_count": uncovered,
    }


def reorder_cover(points, hull, current, mode="mass"):
    """Return the same point multiset reordered, with explanatory metadata.

    ``mass`` chooses the lowest discrete latency score among the original,
    reversed original, nearest-neighbor, and marginal-mass-per-cost routes.
    ``nearest`` is the inexpensive distance-only ablation. No lookup by dataset
    label, case ID, true position, or actual source parameters is possible.
    """
    if mode not in ("mass", "nearest"):
        raise ValueError("cover-order mode must be mass or nearest")
    points = [_point(p) for p in points]
    current = _point(current)
    samples = working_samples(hull)
    masks = _masks(points, samples)
    original = list(range(len(points)))
    original_info = _evaluate(original, points, masks, current, len(samples))
    nearest = _nearest_indices(points, current)
    if mode == "nearest":
        candidates = [("nearest", nearest)]
    else:
        candidates = [("original", original), ("reverse", original[::-1]),
                      ("nearest", nearest), ("marginal_mass_per_cost", _mass_indices(points, masks, current))]
    scored = [(name, route, _evaluate(route, points, masks, current, len(samples)))
              for name, route in candidates]
    # Stable candidate tie-breaking favors the original route. No estimated
    # gain is invented when a mass approximation cannot distinguish routes.
    name, indices, metrics = min(scored, key=lambda item: item[2]["predicted_latency_score_s"])
    if sorted(indices) != original:
        raise RuntimeError("cover reordering did not preserve every original index")
    result = [points[i] for i in indices]
    info = {
        "mode": mode, "selected_ordering": name, **metrics,
        "original_predicted_latency_score_s": original_info["predicted_latency_score_s"],
        "original_full_traversal_cost_s": original_info["full_traversal_cost_s"],
        "point_count": len(points), "unique_point_count": len(set(points)),
        "same_point_multiset": True, "sample_count": len(samples),
        "sampling_method": "deterministic area-weighted convex fan; low-discrepancy barycentric coordinates",
        "mass_interpretation": "uniform-hull working approximation; not a calibrated posterior",
        "coverage_effect": "pure permutation; zero-sample and repeated centers are retained",
        "score_interpretation": "mean sampled first-hit time if all samples covered; otherwise censored traversal proxy",
        "candidates": {candidate: data for candidate, _, data in scored},
    }
    return result, info
