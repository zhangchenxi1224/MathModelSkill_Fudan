"""Safety and outcome-partition tests using only public knowledge inputs."""
import math
import unittest

from bsolver.geometry import (bearing_deg, clip_bearing, distance, max_distance,
    minimum_enclosing_circle, contains)
from bsolver.knowledge import ChannelKnowledge, Observation
from bsolver.sensing import (candidate_points, choose_measurement,
    direction_outcome_bound, optical_fallback_points, guaranteed_1000)


def ray_safe_endpoint(hull, start, q, radius=999.99999):
    """Independent proof that q lies between start and a radius-safe point."""
    v = q[0]-start[0], q[1]-start[1]
    vv = v[0]*v[0]+v[1]*v[1]
    if vv == 0:
        return start if guaranteed_1000(hull, start) else None
    lo, hi = 1.0, math.inf
    for p in hull:
        w = p[0]-start[0], p[1]-start[1]
        projection = (w[0]*v[0]+w[1]*v[1])/vv
        perpendicular2 = max(0.0, w[0]*w[0]+w[1]*w[1]-projection*projection*vv)
        if perpendicular2 > radius*radius:
            return None
        delta = math.sqrt((radius*radius-perpendicular2)/vv)
        lo, hi = max(lo, projection-delta), min(hi, projection+delta)
        if lo > hi:
            return None
    t = (lo+hi)/2
    endpoint = start[0]+t*v[0], start[1]+t*v[1]
    return endpoint if max_distance(hull, endpoint) <= radius+1e-7 else None


class SensingTests(unittest.TestCase):
    def test_all_q3_candidates_have_independent_distance_or_convex_certificate(self):
        for angle in (0, .005, 90, 179.995, 359.995):
            k = ChannelKnowledge(1, 3)
            k.observe((0, 0), {"measure_result": "direction", "svd_deg": angle})
            for mode in ("fixed", "estimated", "active"):
                candidates = candidate_points(k, (0, 0), mode)
                self.assertTrue(candidates)
                for entry in candidates:
                    q = entry if mode == "fixed" else entry[0]
                    endpoint = ray_safe_endpoint(k.hull, (0, 0), q)
                    self.assertTrue(guaranteed_1000(k.hull, q) or endpoint is not None,
                                    (angle, mode, q))

    def test_direction_bin_bound_covers_continuous_interval_representatives(self):
        cases = [([(-12, -3), (15, -3), (15, 8), (-12, 8)], (40, .1)),
                 ([(80, -5), (110, -5), (110, 5), (80, 5)], (0, 0)),
                 ([(-5, -5), (5, -5), (5, 5), (-5, 5)], (0, 0)),
                 ([(2, 0)], (0, 0))]
        for hull, q in cases:
            bound = direction_outcome_bound(hull, q, bin_width_deg=4)
            for source in hull + [((hull[0][0]+p[0])/2, (hull[0][1]+p[1])/2) for p in hull]:
                if distance(q, source) <= 5:
                    self.assertGreaterEqual(bound, 5)
                    continue
                for error in (-1.0051, -.37, 0, .61, 1.0051):
                    theta = (bearing_deg(q, source)+error) % 360
                    posterior = clip_bearing(hull, q, theta, 1.0051)
                    self.assertTrue(contains(posterior, source))
                    self.assertLessEqual(minimum_enclosing_circle(posterior)[1], bound+1e-6)

    def test_outcome_bound_wraparound_and_empty(self):
        hull = [(100, -3), (120, -3), (120, 3), (100, 3)]
        bound = direction_outcome_bound(hull, (0, 0))
        for angle in (0, .001, 359.999, 1, 359):
            post = clip_bearing(hull, (0, 0), angle)
            if post:
                self.assertLessEqual(minimum_enclosing_circle(post)[1], bound+1e-6)
        self.assertEqual(direction_outcome_bound([], (0, 0)), math.inf)

    def test_q4_can_lose_signal_despite_distance_safety(self):
        # A source at (500,0) emitting left sees the first point. A candidate
        # on its right may satisfy all distance bounds but cannot see it.
        k = ChannelKnowledge(1, 4)
        k.observe((0, 0), {"measure_result": "direction", "svd_deg": 0})
        self.assertTrue(guaranteed_1000(k.hull, (750, 450)))
        self.assertGreater((750-500)*math.cos(0), 0)
        self.assertLess((750-500)*math.cos(math.pi), 0)
        k.observe((750, 450), {"measure_result": "no_signal"})
        self.assertTrue(k.compatible_hidden_state((500, 0), 1000, 180))

    def test_near_only_requires_immediate_clear_and_no_direction_candidate(self):
        k = ChannelKnowledge(1, 3)
        k.observe((100, 100), {"measure_result": "near"})
        self.assertEqual(candidate_points(k, (100, 100)), [])
        q, info = choose_measurement(k, (100, 100))
        self.assertIsNone(q)
        self.assertEqual(info["reason"], "no_candidate")

    def test_optical_fallback_covers_worst_angle_strip_at_wrap(self):
        for angle in (0, 90, 359.995):
            obs = Observation((17, -23), "direction", angle)
            points = optical_fallback_points(obs)
            self.assertEqual(len(points), 110)
            a = math.radians(angle)
            for along in (0, 14, 28, 742, 1498, 1500):
                for side in (-1500*math.sin(math.radians(1.0051)), 0,
                              1500*math.sin(math.radians(1.0051))):
                    source = (17+along*math.cos(a)-side*math.sin(a),
                              -23+along*math.sin(a)+side*math.cos(a))
                    self.assertLess(min(distance(source, p) for p in points), 20)


if __name__ == "__main__":
    unittest.main()
