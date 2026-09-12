from collections import Counter
from pathlib import Path
import math
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT))

from bsolver.geometry import contains
from methods.cover_order import reorder_cover, working_samples


@pytest.mark.parametrize("hull", [
    [(10., -3.)], [(0., 0.), (300., 70.)],
    [(0., 0.), (90., 0.), (90., 40.), (0., 40.)],
    [(0., 0.), (90., 40.), (40., 80.), (-20., 10.)],
    [(0., 0.), (300., 0.), (300., .01)],
    [(0., 40.), (90., 40.), (90., 0.), (0., 0.)],
])
def test_fixed_samples_inside_convex_hull_and_deterministic(hull):
    samples = working_samples(hull)
    assert len(samples) == 96
    assert samples == working_samples(hull)
    assert all(contains(hull, point, tol=1e-8) for point in samples)


def test_uniform_triangle_area_mean_is_reasonable():
    samples = working_samples([(0., 0.), (120., 0.), (0., 90.)])
    assert sum(p[0] for p in samples) / 96 == pytest.approx(40., abs=2.)
    assert sum(p[1] for p in samples) / 96 == pytest.approx(30., abs=2.)


@pytest.mark.parametrize("mode", ["mass", "nearest"])
def test_zero_sample_points_and_duplicates_are_preserved(mode):
    hull = [(0., 0.), (100., 0.), (100., 10.), (0., 10.)]
    points = [(0., 0.), (500., 500.), (20., 5.), (40., 5.), (60., 5.), (80., 5.), (100., 5.), (20., 5.)]
    immutable = list(points)
    ordered, info = reorder_cover(points, hull, (0., -10.), mode)
    assert Counter(ordered) == Counter(points)
    assert points == immutable
    assert info["same_point_multiset"]
    assert info["point_count"] == 8
    assert info["unique_point_count"] == 7
    assert info["uncovered_sample_count"] == 0
    assert "not a calibrated posterior" in info["mass_interpretation"]


def test_mass_selection_never_worsens_its_own_discrete_score():
    hull = [(0., 0.), (160., 0.), (160., 24.), (0., 24.)]
    points = [(x, 12.) for x in (150., 10., 130., 30., 110., 50., 90., 70.)]
    ordered, info = reorder_cover(points, hull, (0., 0.))
    assert info["predicted_latency_score_s"] <= info["original_predicted_latency_score_s"]
    assert info["predicted_latency_score_s"] < info["original_predicted_latency_score_s"] - 10
    assert Counter(ordered) == Counter(points)
    # Reconstruct actual first-success cost at every deterministic work sample.
    times = []
    for sample in working_samples(hull):
        current, elapsed = (0., 0.), 0.
        for point in ordered:
            elapsed += math.dist(current, point) / 5 + 3
            current = point
            if math.dist(sample, point) <= 20:
                times.append(elapsed + 2)
                break
    assert len(times) == 96
    assert info["predicted_latency_score_s"] == pytest.approx(sum(times) / len(times))


def test_nearest_ablation_and_full_path_cost():
    points = [(50., 0.), (10., 0.), (30., 0.)]
    ordered, info = reorder_cover(points, [(0., 0.), (60., 0.)], (0., 0.), "nearest")
    assert ordered == [(10., 0.), (30., 0.), (50., 0.)]
    assert info["selected_ordering"] == "nearest"
    assert info["route_distance_m"] == 50.
    assert info["full_traversal_cost_s"] == 50 / 5 + 3 * 3 + 2


def test_uncovered_samples_are_reported_and_no_point_is_added():
    points = [(0., 0.)]
    ordered, info = reorder_cover(points, [(500., 500.)], (0., 0.))
    assert ordered == points
    assert info["uncovered_sample_count"] == 96
    assert info["predicted_latency_score_s"] == 5.
    assert "censored" in info["score_interpretation"]


def test_empty_points_preserved_and_bad_inputs_rejected():
    ordered, info = reorder_cover([], [(0., 0.)], (0., 0.))
    assert ordered == [] and info["uncovered_sample_count"] == 96
    with pytest.raises(ValueError, match="empty hull"):
        reorder_cover([(0., 0.)], [], (0., 0.))
    with pytest.raises(ValueError, match="finite"):
        reorder_cover([(math.nan, 0.)], [(0., 0.)], (0., 0.))
    with pytest.raises(ValueError, match="mode"):
        reorder_cover([(0., 0.)], [(0., 0.)], (0., 0.), "truth")
