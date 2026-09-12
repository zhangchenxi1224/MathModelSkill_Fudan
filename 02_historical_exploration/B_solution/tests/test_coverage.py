"""Analytic-construction regression tests; finite probes are not the proof."""

import json
import math
import random
import unittest

from bsolver.coverage import (
    coverage_certificate, directional_points, omnidirectional_points,
    order_route, route_length, _triangle_distance_sq,
)


def angular_max_gap(source, points, radius=1000.0):
    bearings = sorted(math.atan2(y - source[1], x - source[0]) % math.tau
                      for x, y in points
                      if 1e-8 < math.dist(source, (x, y)) <= radius + 1e-8)
    if not bearings:
        return math.tau
    return max(b - a for a, b in zip(bearings, bearings[1:] + [bearings[0] + math.tau]))


def nearest_route(points, start=(0.0, 0.0)):
    todo, route = set(points), []
    while todo:
        point = min(todo, key=lambda p: (math.dist(start, p), p))
        todo.remove(point)
        route.append(point)
        start = point
    return route


class CoverageTests(unittest.TestCase):
    def test_omni_continuous_formula_and_boundary_probes(self):
        points = omnidirectional_points()
        self.assertEqual(len(set(points)), 7)
        # Exact radial extrema of r^2 - 2700r + 2430000 on [900,1800].
        self.assertEqual(max(r*r - 2700*r + 2430000 for r in (900, 1800)), 900**2)
        for r in (0, 5, 899.999, 900, 1000, 1799.999, 1800):
            for degree in range(0, 360, 2):
                a = math.radians(degree)
                s = r * math.cos(a), r * math.sin(a)
                self.assertLessEqual(min(math.dist(s, p) for p in points), 900 + 1e-8)

    def test_directional_orientation_certificate_at_adversarial_points(self):
        rng = random.Random(20260911)
        random_sources = []
        for _ in range(200):
            r, a = 1800 * math.sqrt(rng.random()), math.tau * rng.random()
            random_sources.append((r * math.cos(a), r * math.sin(a)))
        boundary_sources = [(1800 * math.cos(a * math.pi / 180),
                             1800 * math.sin(a * math.pi / 180)) for a in range(360)]
        for kind, spacing in (("square", 700), ("triangular", 950), ("triangular", 990)):
            points = directional_points(kind, spacing)
            # Also test exact lattice vertices and edge midpoints, where a
            # single containing cell would be insufficient for strict visibility.
            lattice_sources = [p for p in points if math.hypot(*p) <= 1800]
            midpoints = [((a[0]+b[0])/2, (a[1]+b[1])/2)
                         for i, a in enumerate(lattice_sources)
                         for b in lattice_sources[i+1:]
                         if abs(math.dist(a, b)-spacing) < 1e-7]
            for source in random_sources + boundary_sources + lattice_sources + midpoints:
                self.assertLess(angular_max_gap(source, points), math.pi - 1e-10,
                                (kind, spacing, source))

    def test_closed_tangent_cells_and_nonzero_vertex_visibility(self):
        # (1800,0) is a lattice vertex on the source boundary. Cells wholly
        # outside except that point must contribute neighbors on its right.
        square = set(directional_points("square", 600))
        self.assertIn((2400.0, 0.0), square)
        self.assertIn((2400.0, 600.0), square)
        self.assertIn((2400.0, -600.0), square)
        triangular = set(directional_points("triangular", 900))
        self.assertIn((2700.0, 0.0), triangular)
        self.assertLess(angular_max_gap((1800, 0), triangular), math.pi)

    def test_triangle_disk_intersection_distance_is_not_vertex_only(self):
        triangle = ((-2000.0, 1700.0), (2000.0, 1700.0), (0.0, 4000.0))
        self.assertTrue(all(math.hypot(*p) > 1800 for p in triangle))
        self.assertAlmostEqual(_triangle_distance_sq(triangle), 1700**2)
        self.assertEqual(_triangle_distance_sq(((-1, -1), (1, -1), (0, 1))), 0)

    def test_route_preserves_cover_and_improves_nearest_neighbor(self):
        for points in (omnidirectional_points(), directional_points(), directional_points("triangular", 990)):
            original = points[:]
            ordered = order_route(points)
            self.assertEqual(points, original)
            self.assertEqual(set(ordered), set(points))
            self.assertEqual(len(ordered), len(set(points)))
            self.assertLessEqual(route_length(ordered), route_length(nearest_route(points)) + 1e-7)
            self.assertEqual(ordered, order_route(reversed(points)))

    def test_open_route_and_input_validation(self):
        self.assertEqual(order_route([]), [])
        self.assertEqual(order_route([(1, 0), (1, 0)], (5, 0)), [(1.0, 0.0)])
        self.assertEqual(route_length([(3, 4)]), 5)
        for kind, value in (("square", 708), ("triangular", 1001), ("square", 0), ("square", math.nan)):
            with self.assertRaises(ValueError):
                directional_points(kind, value)
        with self.assertRaises(ValueError):
            directional_points("hexagon")
        with self.assertRaises(ValueError):
            order_route([(math.inf, 0)])

    def test_certificates_and_local_fallback_time_bound(self):
        for kind, spacing in (("omnidirectional", None), ("square", 700), ("triangular", 950), ("triangular", 990)):
            cert = coverage_certificate(kind, spacing)
            self.assertEqual(json.loads(json.dumps(cert)), cert)
            points = omnidirectional_points() if kind == "omnidirectional" else directional_points(kind, spacing)
            self.assertEqual(cert["point_count"], len(points))
            # Actual 110-point strip implementation; clear success adds 2 s.
            bound = route_length(order_route(points))/5 + len(points)*20*6 + 16*(3080/5 + 110*3 + 2)
            self.assertLess(bound, 100*3600)
        self.assertLess(1500 * math.sin(math.radians(1.005)), 26.4)
        self.assertLess(math.hypot(15, 13.2), 20)


if __name__ == "__main__":
    unittest.main()
