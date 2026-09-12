"""Boundary, covariance, and independent continuum-witness geometry tests."""
from pathlib import Path
import math
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "diverse_exploration" / "vendor"))

from q2_geometry import (certify_joint_lens, generate_candidates,
                         lens_centers, radial_limit)


def rotate(point, angle):
    a = math.radians(angle)
    return (point[0] * math.cos(a) - point[1] * math.sin(a),
            point[0] * math.sin(a) + point[1] * math.cos(a))


class GeometryTests(unittest.TestCase):
    def test_lens_centers_are_radial_endpoints(self):
        centers = lens_centers((20., -30.), 359., 1.)
        for point, radius, bearing in zip(centers, (5., 5., 1000., 1000.), (358., 360., 358., 360.)):
            expected = rotate((radius, 0.), bearing)
            self.assertAlmostEqual(point[0] - 20., expected[0], places=10)
            self.assertAlmostEqual(point[1] + 30., expected[1], places=10)

    def test_known_radial_limits_and_two_different_active_constraints(self):
        self.assertAlmostEqual(radial_limit(0., 0.), 1005., places=10)
        self.assertAlmostEqual(radial_limit(60., 0.), 1000., places=9)
        self.assertAlmostEqual(radial_limit(80., 0.), 2000. * math.cos(math.radians(80.)), places=10)
        self.assertEqual(radial_limit(90., 0.), 0.)
        self.assertEqual(radial_limit(180., 1.), 0.)
        self.assertEqual(radial_limit(89., 1.), 0.)
        self.assertAlmostEqual(radial_limit(0., 0., inner=10., reception=800.), 810.)

    def test_radial_roots_inside_and_outside_each_closed_lens(self):
        for eps in (0., 1.0051, 20., 65.):
            for phi in (-80., -45., -10., 0., 10., 45., 80.):
                limit = radial_limit(phi, eps)
                if limit < 1.:
                    continue
                inside = rotate((limit - .02, 0.), phi)
                outside = rotate((limit + .02, 0.), phi)
                self.assertTrue(certify_joint_lens(inside, (0., 0.), 0., eps)["certified"])
                self.assertFalse(certify_joint_lens(outside, (0., 0.), 0., eps)["certified"])

    def test_safe_for_inner_reception_and_far_radial_witnesses(self):
        # R is one shared hidden radius, and a positive first direction implies
        # R>=r. The r=1500 witness must therefore use R=1500, not R=1000.
        for eps in (1.0051, 25., 60.):
            points = generate_candidates((0., 0.), 0., eps, angle_step_deg=10.)
            self.assertTrue(points)
            for q in points:
                for r in (5., math.nextafter(5., math.inf), 50., 500., 1000., 1250., 1500.):
                    for offset in (-eps, -.75 * eps, -.25 * eps, 0., .25 * eps, .75 * eps, eps):
                        target = rotate((r, 0.), offset)
                        self.assertLessEqual(math.dist(q, target), max(1000., r) + 1e-8)

    def test_angular_interior_extrema_do_not_invalidate_four_disk_proof(self):
        # For a forward point, the target straight ahead is an interior
        # angular distance MINIMUM. Endpoints give the maximum on the arc.
        q, eps = (700., 0.), 30.
        self.assertTrue(certify_joint_lens(q, (0., 0.), 0., eps)["certified"])
        for radius in (5., 1000., 1500.):
            interior = math.dist(q, (radius, 0.))
            endpoints = max(math.dist(q, rotate((radius, 0.), edge)) for edge in (-eps, eps))
            self.assertLess(interior, endpoints)
        # Behind the station, an interior angular maximum can occur. Such a
        # point is rejected by the r=1000 disks, so the proof cannot assume
        # endpoint maxima for arbitrary, uncertified points.
        behind = (-200., 0.)
        self.assertGreater(math.dist(behind, (1000., 0.)),
                           math.dist(behind, rotate((1000., 0.), eps)))
        self.assertFalse(certify_joint_lens(behind, (0., 0.), 0., eps)["certified"])

    def test_joint_safe_point_can_lie_outside_constant_1000_safe_set(self):
        q = (200., 300.)
        self.assertTrue(certify_joint_lens(q, (0., 0.), 0., 1.0051)["certified"])
        far_target = (1500., 0.)
        self.assertGreater(math.dist(q, far_target), 1000.)
        self.assertLess(math.dist(q, far_target), 1500.)

    def test_translation_rotation_and_wrap_covariance(self):
        eps, alpha = 1.0051, 359.75
        original = generate_candidates((0., 0.), alpha, eps)
        shift, turn = (1200., -713.), 83.125
        transformed = generate_candidates(shift, alpha + turn, eps)
        self.assertEqual(len(original), len(transformed))
        for old, new in zip(original, transformed):
            rotated = rotate(old, turn)
            self.assertAlmostEqual(new[0], rotated[0] + shift[0], places=8)
            self.assertAlmostEqual(new[1], rotated[1] + shift[1], places=8)
            a = certify_joint_lens(old, (0., 0.), alpha, eps)
            b = certify_joint_lens(new, shift, alpha + turn, eps)
            self.assertEqual(a["certified"], b["certified"])
            self.assertAlmostEqual(a["max_distance_m"], b["max_distance_m"], places=7)
        at_zero = generate_candidates((0., 0.), 0., eps)
        at_360 = generate_candidates((0., 0.), 360., eps)
        self.assertEqual(at_zero, at_360)
        self.assertAlmostEqual(radial_limit(-25., eps), radial_limit(335., eps), places=9)

    def test_candidates_are_safe_unique_spaced_and_mirror_preserving(self):
        station, eps = (35., 61.), 1.0051
        candidates = generate_candidates(station, 0., eps, radial_fractions=(.4, .7, 1., 1.))
        rounded = {(round(p[0] - station[0], 7), round(p[1] - station[1], 7)) for p in candidates}
        self.assertEqual(len(rounded), len(candidates))
        self.assertTrue(candidates)
        for q in candidates:
            self.assertGreaterEqual(math.dist(q, station), 1.)
            self.assertTrue(certify_joint_lens(q, station, 0., eps)["certified"])
            local = q[0] - station[0], q[1] - station[1]
            self.assertIn((round(local[0], 7), round(-local[1], 7)), rounded)
            phi = math.degrees(math.atan2(local[1], local[0]))
            self.assertLessEqual(abs(phi), 90. - eps + 1e-9)

    def test_margin_and_tangent_station_are_not_silently_certified(self):
        boundary = (radial_limit(0., 1.), 0.)
        self.assertFalse(certify_joint_lens(boundary, (0., 0.), 0., 1.)["certified"])
        self.assertFalse(certify_joint_lens((0., 0.), (0., 0.), 0., 1.)["certified"])
        q = (300., 0.)
        certificate = certify_joint_lens(q, (0., 0.), 0., 1.)
        self.assertTrue(certificate["certified"])
        self.assertAlmostEqual(certificate["margin_m"] + certificate["max_distance_m"], 1000.)
        self.assertFalse(certify_joint_lens(q, (0., 0.), 0., 1.,
                                          margin=certificate["margin_m"] + 1.)["certified"])

    def test_parameter_validation_and_empty_candidate_policy(self):
        for epsilon in (-1., 90., math.nan, math.inf, True):
            with self.assertRaises(ValueError):
                lens_centers((0., 0.), 0., epsilon)
        for point in ((math.nan, 0.), (0.,), (0., 1., 2.)):
            with self.assertRaises(ValueError):
                certify_joint_lens(point, (0., 0.), 0., 1.)
        for inner, reception in ((0., 1000.), (1001., 1000.), (5., -1.)):
            with self.assertRaises(ValueError):
                radial_limit(0., 1., inner=inner, reception=reception)
        for kwargs in ({"angle_step_deg": 0.}, {"margin_m": -1.},
                       {"radial_fractions": (0.,)}, {"radial_fractions": (1.1,)}):
            with self.assertRaises(ValueError):
                generate_candidates((0., 0.), 0., 1., **kwargs)
        self.assertEqual(generate_candidates((0., 0.), 0., 1., radial_fractions=()), [])
        self.assertEqual(generate_candidates((0., 0.), 0., 1., margin_m=2000.), [])


if __name__ == "__main__":
    unittest.main()
