"""Observation ledger tests, including information shared across observations."""
import math
import unittest

from bsolver.geometry import bearing_deg
from bsolver.knowledge import ChannelKnowledge, InconsistentKnowledge


class KnowledgeTests(unittest.TestCase):
    def test_true_state_retained_at_angle_and_distance_extremes(self):
        for d in (5.001, 100, 1000, 1500):
            for angle in (0, .0049, 90, 179.995, 359.995):
                a = math.radians(angle)
                g = d*math.cos(a)*(1-1e-15), d*math.sin(a)*(1-1e-15)
                for error in (-1, 0, 1):
                    observed = round((bearing_deg((0, 0), g)+error) % 360, 2) % 360
                    k = ChannelKnowledge(1, 3)
                    k.observe((0, 0), {"measure_result": "direction", "svd_deg": observed})
                    self.assertTrue(k.position_in_outer_hull(g))
                    self.assertTrue(k.compatible_hidden_state(g, max(1000, d)))

    def test_shared_radius_disallows_resampling_between_observations(self):
        k = ChannelKnowledge(1, 3)
        k.observe((-1200, 0), {"measure_result": "direction", "svd_deg": 0})
        k.observe((1100, 0), {"measure_result": "no_signal"})
        interval = k.radius_interval_for_position((0, 0))
        self.assertEqual(interval, {"lower": 1200, "upper": 1100, "upper_open": True})
        self.assertFalse(any(k.compatible_hidden_state((0, 0), r) for r in range(1000, 1501)))
        # A negative observation remains a ledger condition, not a hole carved
        # incorrectly into the convex conservative hull.
        self.assertTrue(k.position_in_outer_hull((0, 0)))

    def test_directional_negative_preserves_fixed_orientation_disjunction(self):
        k = ChannelKnowledge(1, 4)
        k.observe((0, 0), {"measure_result": "direction", "svd_deg": 0})
        k.observe((700, 0), {"measure_result": "no_signal"})
        self.assertTrue(k.compatible_hidden_state((500, 0), 1000, 180))
        self.assertFalse(k.compatible_hidden_state((500, 0), 1000, None))
        self.assertFalse(k.compatible_hidden_state((500, 0), 1000, 0))

    def test_failed_clear_is_strict_position_exclusion(self):
        k = ChannelKnowledge(1, 3)
        k.record_clear((0, 0), {"clear_result": "no_target_in_range"})
        self.assertFalse(k.compatible_hidden_state((20, 0), 1000))
        self.assertTrue(k.compatible_hidden_state((20.001, 0), 1000))

    def test_near_and_status(self):
        k = ChannelKnowledge(1, 3)
        k.observe((0, 0), {"measure_result": "near"}, coverage_index=2)
        self.assertTrue(k.compatible_hidden_state((5, 0), 1000))
        self.assertFalse(k.compatible_hidden_state((5.1, 0), 1000))
        self.assertIn(2, k.coverage_indices)
        k.record_clear((0, 0), {"clear_result": "success"})
        self.assertEqual(k.status, "cleared")
        with self.assertRaises(InconsistentKnowledge):
            k.observe((0, 0), {"measure_result": "near"})

    def test_invalid_result_and_absent_state(self):
        k = ChannelKnowledge(1, 3)
        with self.assertRaises(ValueError):
            k.observe((0, 0), {"measure_result": "bogus"})
        absent = ChannelKnowledge(2, 3, status="absent")
        self.assertFalse(absent.compatible_hidden_state((0, 0), 1000))

    def test_successful_clear_constrains_historical_source_position(self):
        k = ChannelKnowledge(1, 3)
        k.record_clear((100, 0), {"clear_result": "success"})
        self.assertTrue(k.compatible_hidden_state((100, 0), 1000))
        self.assertFalse(k.compatible_hidden_state((121, 0), 1000))

    def test_same_place_readout_is_fixed_before_removal(self):
        k = ChannelKnowledge(1, 3)
        k.observe((0, 0), {"measure_result": "direction", "svd_deg": 0})
        k.observe((0, 0), {"measure_result": "direction", "svd_deg": 0})
        with self.assertRaises(InconsistentKnowledge):
            k.observe((0, 0), {"measure_result": "direction", "svd_deg": .01})


if __name__ == "__main__":
    unittest.main()
