"""Continuous certificate checks, plus dense adversarial boundary smoke tests."""
from pathlib import Path
import math
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT))

from bsolver.coverage import route_length
from bsolver.knowledge import Observation
from methods.clear_cover import build_cover, cover_cost


def rectangle(width, height, angle, origin=(41., -39.)):
    c, s = math.cos(angle), math.sin(angle)
    return [(origin[0] + c * x - s * y, origin[1] + s * x + c * y)
            for x, y in ((0, 0), (width, 0), (width, height), (0, height))]


def knowledge(hull, first=None):
    return SimpleNamespace(hull=hull, first_direction=first, failed_clear_positions=[])


def verify_partition_certificate(hull, points, cert):
    assert cert["covering_radius_bound_m"] <= cert["radius_m"]
    if cert["kind"] != "rotated_rectangle_partition":
        return
    c, s = math.cos(cert["angle_rad"]), math.sin(cert["angle_rad"])
    ox, oy = cert["origin"]
    x0, x1, y0, y1 = cert["bounds"]
    nx, ny = cert["subdivisions"]
    dx, dy = cert["cell_size_m"]
    assert len(cert["kept_cells"]) + len(cert["omitted_cells"]) == nx * ny
    assert len(points) == len(cert["kept_cells"])
    for px, py in hull:
        x, y = c * (px - ox) + s * (py - oy), -s * (px - ox) + c * (py - oy)
        assert x0 <= x <= x1 and y0 <= y <= y1
    for i, j in cert["kept_cells"]:
        center_x, center_y = x0 + (i + .5) * dx, y0 + (j + .5) * dy
        center = (ox + c * center_x - s * center_y, oy + s * center_x + c * center_y)
        assert any(math.dist(center, p) < 1e-7 for p in points)
        for a, b in ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)):
            x, y = x0 + a * dx, y0 + b * dy
            corner = (ox + c * x - s * y, oy + s * x + c * y)
            assert math.dist(corner, center) <= cert["radius_m"] + 1e-9
    for a, b, lo, hi in cert["supporting_slabs"]:
        assert math.hypot(a, b) == pytest.approx(1.0)
        assert all(lo - 1e-9 <= a * x + b * y <= hi + 1e-9 for x, y in hull)
    for i, j, distance_bound in cert["omitted_cells"]:
        assert distance_bound > cert["covering_radius_bound_m"] + 1e-6
        x, y = x0 + (i + .5) * dx, y0 + (j + .5) * dy
        px, py = ox + c * x - s * y, oy + s * x + c * y
        rebuilt = max([0.] + [max(lo - a * px - b * py, a * px + b * py - hi)
                               for a, b, lo, hi in cert["supporting_slabs"]])
        assert distance_bound == pytest.approx(rebuilt)


@pytest.mark.parametrize("angle", [0., .13, .6, 1.2, 2.9, 4.8])
@pytest.mark.parametrize("size", [(300., 2.), (200., 39.9), (40., 40.)])
def test_rotated_rectangles_and_boundaries(angle, size):
    hull = rectangle(*size, angle)
    points, cert = build_cover(knowledge(hull), (-100., 30.))
    verify_partition_certificate(hull, points, cert)
    # Boundary samples and all corners; analytic checks above carry the proof.
    for p, q in zip(hull, hull[1:] + hull[:1]):
        for step in range(61):
            t = step / 60
            sample = (p[0] * (1 - t) + q[0] * t, p[1] * (1 - t) + q[1] * t)
            assert min(math.dist(sample, center) for center in points) <= 19.999
    assert cert["worst_case_cost_s"] == pytest.approx(route_length(points, (-100., 30.)) / 5 + 3 * len(points) + 2)


@pytest.mark.parametrize("hull", [[(10., 20.)], [(0., 0.), (400., 80.)]])
def test_point_and_segment(hull):
    points, cert = build_cover(knowledge(hull), (0., 0.))
    verify_partition_certificate(hull, points, cert)
    p, q = hull[0], hull[-1]
    for i in range(1001):
        sample = (p[0] + (q[0] - p[0]) * i / 1000, p[1] + (q[1] - p[1]) * i / 1000)
        assert min(math.dist(sample, center) for center in points) <= 19.999


@pytest.mark.parametrize("mode", ["bbox", "strip"])
def test_baseline_dominance_and_backup(mode):
    hull = [(150., -3.), (550., -3.), (550., 3.), (150., 3.)]
    k = knowledge(hull, Observation((0., 0.), "direction", 0.))
    points, cert = build_cover(k, (130., 70.), mode)
    assert len(cert["backup_points"]) == 110
    assert cert["baseline_cost_s"] is not None
    assert cert["worst_case_cost_s"] <= cert["baseline_cost_s"] + 1e-8
    assert len(points) < cert["baseline_point_count"]
    assert cert["worst_case_cost_s"] == pytest.approx(cover_cost(points, (130., 70.)))
    verify_partition_certificate(hull, points, cert)


def test_disjoint_pruning_does_not_lose_triangle_edges_or_interior():
    hull = [(0., 0.), (300., 0.), (300., 50.)]
    points, cert = build_cover(knowledge(hull), (0., -10.))
    verify_partition_certificate(hull, points, cert)
    for i in range(101):
        for j in range(i + 1):
            sample = (300 * i / 100, 50 * j / 100)
            assert min(math.dist(sample, p) for p in points) <= 19.999


def test_no_truth_or_failed_clear_pruning_and_invalid_inputs():
    k = knowledge(rectangle(70., 6., .5))
    first, cert = build_cover(k, (0., 0.))
    k.failed_clear_positions = list(first)
    second, _ = build_cover(k, (0., 0.))
    assert first == second
    with pytest.raises(ValueError, match="empty hull"):
        build_cover(knowledge([]), (0., 0.))
    with pytest.raises(ValueError, match="finite"):
        build_cover(knowledge([(math.nan, 1.)]), (0., 0.))
    with pytest.raises(ValueError, match="radius"):
        build_cover(k, (0., 0.), radius=20.)
    with pytest.raises(ValueError, match="mode"):
        build_cover(k, (0., 0.), mode="truth")


def test_original_ineligible_for_non_observation_hull_and_small_radius():
    k = knowledge([(0., 40.), (40., 40.)], Observation((0., 0.), "direction", 0.))
    points, cert = build_cover(k, (0., 0.), radius=10.)
    assert cert["baseline_cost_s"] is None
    verify_partition_certificate(k.hull, points, cert)
