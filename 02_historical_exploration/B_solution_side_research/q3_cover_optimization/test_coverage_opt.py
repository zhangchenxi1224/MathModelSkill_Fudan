import math
import unittest

from coverage_opt import (ORIGINAL_RING, MINIMUM_RING, covering_radius,
                          certificate, ContractedCoverageSolver)
from bsolver.coverage import omnidirectional_points, order_route, route_length
from bsolver.protocol import RobotClient
from bsolver.simulator import LocalSimulator, Source
from bsolver.strategy import SolverConfig


class CoverTests(unittest.TestCase):
    def test_original_exact_radius(self):
        self.assertAlmostEqual(covering_radius(ORIGINAL_RING), 900, places=8)

    def test_boundary_midpoints_attain_contracted_bound(self):
        r = 1125.
        points = [(0., 0.)] + [(r * math.cos(k * math.pi / 3),
                               r * math.sin(k * math.pi / 3)) for k in range(6)]
        for k in range(6):
            a = (k + .5) * math.pi / 3
            g = (1800 * math.cos(a), 1800 * math.sin(a))
            self.assertAlmostEqual(min(math.dist(g, p) for p in points),
                                   covering_radius(r), places=8)
        self.assertLess(certificate(r)["covering_radius_upper_m"], 1000)

    def test_below_threshold_has_constructive_hole(self):
        r = MINIMUM_RING - .1
        a = math.pi / 6
        g = (1800 * math.cos(a), 1800 * math.sin(a))
        points = [(0., 0.)] + [(r * math.cos(k * math.pi / 3),
                               r * math.sin(k * math.pi / 3)) for k in range(6)]
        self.assertGreater(min(math.dist(g, p) for p in points), 1000)
        with self.assertRaises(ValueError):
            certificate(r)

    def test_interior_voronoi_vertex(self):
        r = 1700.
        a = math.pi / 6
        g = (r / math.sqrt(3) * math.cos(a), r / math.sqrt(3) * math.sin(a))
        p = (r, 0.)
        self.assertAlmostEqual(math.dist(g, p), r / math.sqrt(3))
        self.assertAlmostEqual(math.hypot(*g), r / math.sqrt(3))
        self.assertAlmostEqual(covering_radius(r), r / math.sqrt(3))

    def test_route_scaling_and_lower_bound(self):
        original = order_route(omnidirectional_points())
        scaled = [(x * 1125 / ORIGINAL_RING, y * 1125 / ORIGINAL_RING)
                  for x, y in original]
        self.assertAlmostEqual(route_length(original), 6 * ORIGINAL_RING)
        self.assertAlmostEqual(route_length(scaled), 6750)

    def test_invalid_inputs(self):
        for r in [-1, float("nan"), float("inf"), 4000]:
            with self.assertRaises(ValueError):
                covering_radius(r)

    def test_five_boundary_arcs_insufficient(self):
        self.assertLess(5 * 2 * math.asin(1000 / 1800), 2 * math.pi)

    def test_unresolved_channel_requires_all_stations(self):
        env = LocalSimulator([Source(1, (0., 0.))], robot_id="side-local")
        solver = ContractedCoverageSolver(RobotClient(robot_id="side-local", transport=env),
                                           SolverConfig(problem=3))
        for k in solver.channels.values():
            k.coverage_indices.update(range(6))
        self.assertIsNone(solver._complete_evidence())
        for k in solver.channels.values():
            k.coverage_indices.add(6)
        evidence = solver._complete_evidence()
        self.assertEqual(evidence["required_point_count"], 7)
        self.assertEqual(evidence["analytic_certificate"]["ring_radius_m"], 1125)


if __name__ == "__main__":
    unittest.main(verbosity=2)
