"""Geometry tests: degeneracies, conservative bounds, and independent oracles."""
from __future__ import annotations

import itertools
import math
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bsolver.geometry import (  # noqa: E402
    bearing_deg, bearing_halfplanes, certify_clear, classify_halfplanes,
    clip_bearing, clip_disk_outer, clip_halfplane, contains, distance,
    distance_to_polygon, disk_outer_polygon, intersect_bearings, max_distance,
    minimum_enclosing_circle, nearest_safe_clear, polygon_centroid,
    polygon_diameter, unit_from_deg, wrap_angle_deg,
)


def brute_circle(points):
    """Independent small-set oracle: enumerate all 1/2/3 point supports."""
    candidates = [(p, 0.0) for p in points]
    for a, b in itertools.combinations(points, 2):
        c = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        candidates.append((c, distance(c, a)))
    for a, b, c in itertools.combinations(points, 3):
        # Different, untranslated determinant formula from implementation.
        d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
        if abs(d) < 1e-12:
            continue
        aa, bb, cc = sum(v * v for v in a), sum(v * v for v in b), sum(v * v for v in c)
        center = ((aa * (b[1] - c[1]) + bb * (c[1] - a[1]) + cc * (a[1] - b[1])) / d,
                  (aa * (c[0] - b[0]) + bb * (a[0] - c[0]) + cc * (b[0] - a[0])) / d)
        candidates.append((center, distance(center, a)))
    valid = [(c, r) for c, r in candidates if all(distance(c, p) <= r + 1e-8 for p in points)]
    return min(valid, key=lambda v: v[1])


class AngleAndPolygonTests(unittest.TestCase):
    def test_wrap_and_bearing_conventions(self):
        self.assertEqual(wrap_angle_deg(180), -180)
        self.assertEqual(wrap_angle_deg(-181), 179)
        self.assertEqual(wrap_angle_deg(721), 1)
        self.assertEqual(bearing_deg((0, 0), (0, 1)), 90)
        self.assertEqual(bearing_deg((0, 0), (0, -1)), 270)
        with self.assertRaises(ValueError):
            bearing_deg((1, 2), (1, 2))

    def test_circumscribed_polygon_contains_true_disk(self):
        for n in (3, 4, 17, 128):
            poly = disk_outer_polygon((370, -800), 1800, n)
            for j in range(2000):
                a = j * 2 * math.pi / 2000
                p = (370 + 1800 * math.cos(a), -800 + 1800 * math.sin(a))
                self.assertTrue(contains(poly, p, tol=1e-8), (n, p))
            self.assertGreater(polygon_diameter(poly), 3600 - 1e-7)

    def test_zero_radius_and_invalid_inputs(self):
        self.assertEqual(disk_outer_polygon((1, 2), 0), [(1.0, 2.0)])
        for radius in (-1, math.inf, math.nan):
            with self.assertRaises(ValueError):
                disk_outer_polygon((0, 0), radius)
        with self.assertRaises(ValueError):
            disk_outer_polygon((0, 0), 1, 2)

    def test_clip_square_and_normal_rescaling(self):
        square = [(-2, -2), (2, -2), (2, 2), (-2, 2)]
        a = clip_halfplane(square, (1, 0), 0)
        b = clip_halfplane(square, (100, 0), 0)
        self.assertEqual(a, b)
        self.assertTrue(contains(a, (0, 1)))
        self.assertFalse(contains(a, (1, 0)))
        self.assertTrue(all(p[0] <= 2e-9 for p in a))
        self.assertEqual(clip_halfplane(square, (0, 0), -1), [])
        self.assertEqual(clip_halfplane(square, (0, 0), 0), square)

    def test_clip_degenerate_point_segment(self):
        self.assertEqual(clip_halfplane([(0, 0)], (1, 0), -1), [])
        self.assertEqual(clip_halfplane([(0, 0)], (1, 0), 0), [(0.0, 0.0)])
        line = clip_halfplane([(-2, 0), (2, 0)], (1, 0), 0)
        self.assertTrue(contains(line, (-1, 0)))
        self.assertFalse(contains(line, (1, 0)))

    def test_bearing_is_forward_wedge_and_wraps_zero(self):
        square = [(-100, -100), (100, -100), (100, 100), (-100, 100)]
        p = clip_bearing(square, (0, 0), 359.5, epsilon_deg=1.0)
        self.assertTrue(contains(p, (50, 0)))
        self.assertFalse(contains(p, (-50, 0)))
        self.assertFalse(contains(p, (50, 5)))
        with self.assertRaises(ValueError):
            bearing_halfplanes((0, 0), 0, 90)

    def test_true_source_retained_at_extreme_bearing_errors(self):
        rng = random.Random(811)
        for _ in range(100):
            r, angle = 1800 * math.sqrt(rng.random()), rng.uniform(-math.pi, math.pi)
            source = (r * math.cos(angle), r * math.sin(angle))
            poly = disk_outer_polygon((0, 0), 1800)
            for j in range(8):
                station = (rng.uniform(-2500, 2500), rng.uniform(-2500, 2500))
                reading = (bearing_deg(station, source) + (-1 if j % 2 else 1)) % 360
                # Attachment 2 returns two decimals. Test conservatively as
                # physical +/-1 error followed by a separate rounding step.
                reading = round(reading, 2) % 360
                poly = clip_bearing(poly, station, reading)
                self.assertTrue(poly)
                self.assertTrue(contains(poly, source, tol=1e-6))

    def test_disk_clipping_retains_actual_circular_lens(self):
        poly = clip_disk_outer(disk_outer_polygon((0, 0), 1800), (500, 100), 1500, n=64)
        rng = random.Random(161)
        for _ in range(1000):
            p = (rng.uniform(-1800, 1800), rng.uniform(-1800, 1800))
            if distance(p, (0, 0)) <= 1800 and distance(p, (500, 100)) <= 1500:
                self.assertTrue(contains(poly, p))

    def test_diameter_centroid_and_distance(self):
        rectangle = [(-2, -1), (2, -1), (2, 1), (-2, 1)]
        self.assertAlmostEqual(polygon_diameter(rectangle), math.sqrt(20))
        self.assertEqual(polygon_centroid(rectangle), (0, 0))
        self.assertEqual(polygon_centroid(list(reversed(rectangle))), (0, 0))
        self.assertEqual(distance_to_polygon(rectangle, (0, 0)), 0)
        self.assertAlmostEqual(distance_to_polygon(rectangle, (5, 5)), 5)
        self.assertFalse(contains([], (0, 0)))
        self.assertTrue(contains([(0, 0), (2, 0), (3, 0)], (1, 0)))
        self.assertFalse(contains([(0, 0), (2, 0), (3, 0)], (1, 0.1)))


class ClassificationTests(unittest.TestCase):
    def test_plane_halfplane_wedge_strip(self):
        cases = [[], [((1, 0), 1)], [((-1, 0), 0), ((0, -1), 0)],
                 [((1, 0), 2), ((-1, 0), -1)]]
        for halfplanes in cases:
            r = classify_halfplanes(halfplanes)
            self.assertEqual(r.kind, "unbounded")
            self.assertFalse(r.bounded)
            self.assertEqual(r.dimension, 2)
            self.assertTrue(r.recession_directions)

    def test_empty_parallel_and_zero_constraint(self):
        self.assertEqual(classify_halfplanes([((1, 0), 0), ((-1, 0), -1)]).kind, "empty")
        self.assertEqual(classify_halfplanes([((0, 0), -1)]).kind, "empty")
        self.assertEqual(classify_halfplanes([((0, 0), 0)]).kind, "unbounded")

    def test_tiny_inconsistency_is_not_hidden_by_tolerance(self):
        r = classify_halfplanes([((1, 0), 0), ((-1, 0), -1e-15)], tol=1e-3)
        self.assertEqual(r.kind, "empty")

    def test_bounded_polygon(self):
        r = classify_halfplanes([((1, 0), 2), ((-1, 0), 0), ((0, 1), 3), ((0, -1), 0)])
        self.assertEqual(r.kind, "polygon")
        self.assertEqual(r.dimension, 2)
        self.assertTrue(r.bounded)
        self.assertEqual(set(r.vertices), {(0, 0), (2, 0), (2, 3), (0, 3)})
        self.assertAlmostEqual(polygon_diameter(r.vertices), math.sqrt(13))

    def test_point_segment_line_and_ray(self):
        xeq1 = [((1, 0), 1), ((-1, 0), -1)]
        point = classify_halfplanes(xeq1 + [((0, 1), 2), ((0, -1), -2)])
        self.assertEqual((point.kind, point.dimension, point.vertices), ("point", 0, [(1, 2)]))
        segment = classify_halfplanes(xeq1 + [((0, 1), 2), ((0, -1), 0)])
        self.assertEqual((segment.kind, segment.dimension), ("segment", 1))
        self.assertEqual(set(segment.vertices), {(1, 0), (1, 2)})
        line = classify_halfplanes(xeq1)
        self.assertEqual((line.kind, line.dimension, line.vertices), ("unbounded", 1, []))
        ray = classify_halfplanes(xeq1 + [((0, -1), -2)])
        self.assertEqual((ray.kind, ray.dimension, ray.vertices), ("unbounded", 1, [(1, 2)]))

    def test_extremely_thin_real_polygon_is_not_collapsed(self):
        r = classify_halfplanes([((1, 0), 1e-14), ((-1, 0), 0), ((0, 1), 1), ((0, -1), 0)])
        self.assertEqual((r.kind, r.dimension, len(r.vertices)), ("polygon", 2, 4))

    def test_near_parallel_halfplanes_without_artificial_box(self):
        r = classify_halfplanes([((-1, 0), 0), ((0, -1), 0), ((1e-12, 1), 1)])
        self.assertEqual(r.kind, "polygon")
        self.assertTrue(any(p[0] >= 0.999999e12 for p in r.vertices))

    def test_q1_wedges_bounded_and_unbounded(self):
        self.assertEqual(intersect_bearings([((0, 0), 0)]).kind, "unbounded")
        bounded = intersect_bearings([((0, 0), 45), ((100, 0), 135)])
        self.assertEqual(bounded.kind, "polygon")
        self.assertTrue(contains(bounded.vertices, (50, 50)))


class CircleAndSafetyTests(unittest.TestCase):
    def test_triangle_diameter_counterexample(self):
        points = [(0, 0), (39, 0), (19.5, 39 * math.sqrt(3) / 2)]
        self.assertAlmostEqual(polygon_diameter(points), 39)
        _, radius = minimum_enclosing_circle(points)
        self.assertAlmostEqual(radius, 39 / math.sqrt(3), places=7)
        self.assertIsNone(nearest_safe_clear(points, (0, 0), radius=20))

    def test_collinear_duplicate_and_singleton_circle(self):
        center, radius = minimum_enclosing_circle([(0, 0), (0, 0), (10, 0), (3, 0)])
        self.assertAlmostEqual(center[0], 5)
        self.assertAlmostEqual(center[1], 0)
        self.assertAlmostEqual(radius, 5)
        self.assertEqual(minimum_enclosing_circle([(1, 2)]), ((1.0, 2.0), 0))
        with self.assertRaises(ValueError):
            minimum_enclosing_circle([])

    def test_random_mec_against_independent_support_enumeration(self):
        rng = random.Random(107)
        for _ in range(200):
            points = [(rng.uniform(-50, 50), rng.uniform(-50, 50)) for _ in range(rng.randint(2, 9))]
            center, radius = minimum_enclosing_circle(points)
            _, expected = brute_circle(points)
            self.assertLessEqual(max_distance(points, center), radius)
            self.assertAlmostEqual(radius, expected, places=7)

    def test_circle_translated_near_coordinate_limit(self):
        points = [(1999990, -1999990), (1999992, -1999990), (1999991, -1999988)]
        center, radius = minimum_enclosing_circle(points)
        self.assertAlmostEqual(center[0], 1999991, places=6)
        self.assertAlmostEqual(center[1], -1999989.25, places=6)
        self.assertAlmostEqual(radius, 1.25, places=6)
        self.assertLessEqual(max_distance(points, center), radius)

    def test_nearest_two_disk_lens_beats_mec_center(self):
        points = [(-10, 0), (10, 0)]
        p = nearest_safe_clear(points, (0, 50), radius=20)
        self.assertIsNotNone(p)
        self.assertAlmostEqual(p[0], 0, places=7)
        self.assertAlmostEqual(p[1], math.sqrt(300), places=7)
        self.assertLessEqual(max_distance(points, p), 20)

    def test_current_feasible_and_single_disk_projection(self):
        self.assertEqual(nearest_safe_clear([(-1, 0), (1, 0)], (0, 0)), (0, 0))
        p = nearest_safe_clear([(0, 0)], (50, 0))
        self.assertAlmostEqual(p[0], 19.999)
        self.assertAlmostEqual(p[1], 0)
        self.assertTrue(certify_clear([(0, 0)], p))

    def test_tangent_and_disjoint_clear_disks(self):
        self.assertEqual(nearest_safe_clear([(-20, 0), (20, 0)], (0, 50), 20), (0, 0))
        self.assertIsNone(nearest_safe_clear([(-20.001, 0), (20.001, 0)], (0, 50), 20))

    def test_empty_never_certifies_and_margin_is_enforced(self):
        self.assertFalse(certify_clear([], (0, 0)))
        self.assertIsNone(nearest_safe_clear([], (0, 0)))
        self.assertFalse(certify_clear([(20, 0)], (0, 0)))
        self.assertTrue(certify_clear([(19.999, 0)], (0, 0)))

    def test_safe_clear_retains_true_region_not_just_centroid(self):
        source = (511.0, -732.0)
        poly = disk_outer_polygon((0, 0), 1800)
        for station in [(source[0] - 500, source[1]), (source[0], source[1] - 500)]:
            poly = clip_bearing(poly, station, bearing_deg(station, source))
        p = nearest_safe_clear(poly, (0, 0))
        self.assertIsNotNone(p)
        self.assertTrue(certify_clear(poly, p))
        self.assertLess(distance(source, p), 20)

    def test_clear_projection_against_dense_feasible_grid(self):
        # This independent oracle need not find the exact optimum; a correct
        # candidate may never be worse than any sampled feasible competitor.
        poly = [(-7, -3), (9, -2), (3, 10)]
        current = (41, 38)
        p = nearest_safe_clear(poly, current, radius=19.999)
        self.assertIsNotNone(p)
        best = distance(current, p)
        for i in range(-50, 51):
            for j in range(-50, 51):
                q = (i / 2, j / 2)
                if max_distance(poly, q) <= 19.999:
                    self.assertLessEqual(best, distance(current, q) + 1e-7)


if __name__ == "__main__":
    unittest.main()
