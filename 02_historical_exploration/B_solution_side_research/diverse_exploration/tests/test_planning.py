"""Semantic tests for fixed hidden-state filtering and genuine two-step trees."""
from __future__ import annotations

import copy
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

from bsolver.knowledge import ChannelKnowledge
from diverse.belief import (Particle, build_belief, condition_clear,
                            condition_measurement, predict_response, response_bin)
from diverse.planning import (DEFAULT_CONFIG, _Tree, action_cost, choose_action,
                              physical_information_gain)


def knowledge(problem=4):
    result = ChannelKnowledge(1, problem)
    result.observe((0, 0), {"measure_result": "direction", "svd_deg": 0.})
    return result


class BeliefSemanticsTests(unittest.TestCase):
    def test_every_particle_explains_entire_history_and_failed_clear(self):
        ledger = knowledge()
        ledger.observe((900, 0), {"measure_result": "no_signal"})
        ledger.record_clear((600, 0), {"clear_result": "no_target_in_range"})
        particles, summary = build_belief(ledger, DEFAULT_CONFIG)
        self.assertTrue(particles, summary)
        self.assertTrue(all(ledger.compatible_hidden_state(p.position, p.radius, p.direction_deg) for p in particles))
        # This negative lies within 1000 m of every position in the narrow
        # forward hull: only a compatible directional backside can explain it.
        self.assertTrue(all(p.direction_deg is not None for p in particles))

    def test_q4_keeps_omni_and_directional_negative_explanations(self):
        ledger = knowledge()
        ledger.observe((1700, 0), {"measure_result": "no_signal"})
        particles, summary = build_belief(ledger, DEFAULT_CONFIG)
        self.assertTrue(particles, summary)
        self.assertTrue(any(p.direction_deg is None for p in particles))
        self.assertTrue(any(p.direction_deg is not None for p in particles))
        self.assertTrue(all(ledger.compatible_hidden_state(p.position, p.radius, p.direction_deg) for p in particles))

    def test_q3_has_no_directional_component(self):
        particles, summary = build_belief(knowledge(3), DEFAULT_CONFIG)
        self.assertTrue(particles, summary)
        self.assertTrue(all(p.direction_deg is None for p in particles))

    def test_impossible_fixed_radius_does_not_reset_per_observation(self):
        ledger = knowledge(3)
        ledger.hull = [(600., 0.)]
        ledger.observe((1000, 0), {"measure_result": "no_signal"})
        particles, summary = build_belief(ledger, DEFAULT_CONFIG)
        self.assertEqual(particles, [])
        self.assertEqual(summary["reason"], "no_compatible_particles")

    def test_error_is_coordinate_fixed_and_historical_response_is_exact(self):
        p = Particle((600., 0.), 1200., None, 1., 123,
                     observed_angles=(((0., 0.), .7),))
        self.assertEqual(predict_response(p, (0, 0)), ("direction", .7))
        point = (100., 100.)
        initial = predict_response(p, point)
        for _ in range(5):
            predict_response(p, (200., 150.))
            self.assertEqual(predict_response(p, point), initial)
        conditioned = condition_measurement([p], point, response_bin(initial))
        self.assertEqual(predict_response(conditioned[0], point), initial)

    def test_duplicate_history_is_not_an_independent_sample(self):
        ledger = knowledge()
        before, _ = build_belief(ledger, DEFAULT_CONFIG)
        # Hold the geometric proposal fixed: vendor.observe re-clips its
        # already padded polygon, which is a separate floating-point effect.
        ledger.observations.append(copy.copy(ledger.observations[0]))
        ledger.positive_positions.append((0., 0.))
        after, summary = build_belief(ledger, DEFAULT_CONFIG)
        self.assertEqual(summary["unique_observations"], 1)
        self.assertEqual(before, after)

    def test_clear_failure_excludes_closed_twenty_metre_disk(self):
        particles = [Particle((x, 0), 1200, None, 1/3) for x in (0, 20, 20.001)]
        failed = condition_clear(particles, (0, 0), False)
        self.assertEqual([p.position for p in failed], [(20.001, 0)])
        self.assertEqual(failed[0].weight, 1)
        self.assertEqual(len(condition_clear(particles, (0, 0), True)), 2)

    def test_too_few_feasible_samples_falls_back_without_certificate(self):
        ledger = knowledge()
        cfg = {**DEFAULT_CONFIG, "max_position_proposals": 1}
        particles, summary = build_belief(ledger, cfg)
        self.assertEqual(particles, [])
        self.assertEqual(summary["reason"], "too_few_effective_compatible_particles")
        self.assertFalse(summary["finite_sample_is_certificate"])


class PlanningSemanticsTests(unittest.TestCase):
    def test_information_does_not_reward_distinguishing_sensor_error_seeds(self):
        particles = [Particle((100., 0.), 1200., None, .25, seed) for seed in range(4)]
        gain, information = physical_information_gain(particles, (0., 0.), .25)
        self.assertAlmostEqual(gain, 0., places=12)
        self.assertGreater(information["response_entropy_nats"], 1.)
        self.assertEqual(information["physical_state_count"], 1)

    def test_information_of_two_fully_distinguished_physical_states(self):
        particles = [Particle((-100., 0.), 1200., None, .5, 1),
                     Particle((100., 0.), 1200., None, .5, 2)]
        gain, information = physical_information_gain(particles, (0., -100.), .25)
        self.assertAlmostEqual(gain, math.log(2), places=12)
        self.assertGreater(information["conditional_sensor_entropy_nats"], 0.)

    def test_action_cost_matches_speed_switch_and_success_charge(self):
        particles = [Particle((10., 0.), 1200., None, .25), Particle((100., 0.), 1200., None, .75)]
        clear = {"kind": "clear", "point": (10., 0.)}
        measure = {"kind": "measure", "point": (10., 0.)}
        cost, terms = action_cost(particles, (0, 0), 7, 1, clear)
        self.assertAlmostEqual(cost, 2+3+2*.25)
        self.assertEqual(terms["channel_switch"], 0)
        cost, terms = action_cost(particles, (0, 0), 7, 1, measure)
        self.assertEqual(cost, 2+5+1)
        self.assertEqual(terms["channel_switch"], 1)

    def test_second_action_depends_on_first_feedback(self):
        particles = [Particle((-100., 0.), 1500., None, .5, 1),
                     Particle((100., 0.), 1500., None, .5, 2)]
        ledger = ChannelKnowledge(1, 3)
        tree = _Tree(particles, ledger, DEFAULT_CONFIG, None)
        action = {"kind": "measure", "point": (0., -100.)}
        value, info = tree.two_step(particles, action["point"], 1, action, [action], "rollout")
        self.assertEqual(len(info["branches"]), 2)
        seconds = {tuple(branch["second_action"]["point"]) for branch in info["branches"]}
        self.assertEqual(seconds, {(-100., 0.), (100., 0.)})
        self.assertTrue(all(branch["second_action"]["kind"] == "clear" for branch in info["branches"]))
        # Each branch's second clear succeeds, so there is no residual tail.
        self.assertAlmostEqual(value, 5+math.sqrt(20000)/5+5)

    def test_root_clear_failure_conditions_then_measures_without_extra_clear(self):
        particles = [Particle((0., 0.), 1500., None, .5, 1),
                     Particle((100., 0.), 1500., None, .5, 2)]
        ledger = ChannelKnowledge(1, 3)
        tree = _Tree(particles, ledger, DEFAULT_CONFIG, None)
        clear = {"kind": "clear", "point": (0., 0.)}
        measure = {"kind": "measure", "point": (0., 100.)}
        _, info = tree.two_step(particles, (0, 0), 7, clear, [measure], "rollout")
        failure = next(branch for branch in info["branches"] if branch["outcome"][0] == "no_target_in_range")
        success = next(branch for branch in info["branches"] if branch["outcome"][0] == "success")
        self.assertEqual(failure["posterior_particle_count"], 1)
        self.assertEqual(failure["second_action"]["kind"], "measure")
        self.assertEqual(success["continuation_score_s"], 0)
        self.assertEqual(info["immediate_cost_s"], 4)
        # A root clear keeps channel 7, so second measurement pays +1.
        _, same_channel = tree.two_step(particles, (0, 0), 1, clear, [measure], "rollout")
        self.assertAlmostEqual(info["expected_continuation_s"]-same_channel["expected_continuation_s"], .5)

    def test_modes_are_deterministic_and_do_not_mutate_knowledge(self):
        ledger = knowledge()
        original = copy.deepcopy(ledger)
        for mode in ("fisher", "infogain", "rollout", "probe"):
            first, info = choose_action(ledger, (0, 0), 1, mode, {"planning_budget_s": 2.})
            second, repeated = choose_action(ledger, (0, 0), 1, mode, {"planning_budget_s": 2.})
            self.assertIsNotNone(first, info)
            self.assertEqual(first, second)
            self.assertEqual(info["evaluations"], repeated["evaluations"])
            self.assertFalse(info["posterior_is_certificate"])
            if mode in ("rollout", "probe"):
                self.assertGreater(info["second_stage_action_evaluations"], 0)
        self.assertEqual(ledger, original)

    def test_timeout_discards_choice_and_reports_reason(self):
        action, info = choose_action(knowledge(), (0, 0), 1, "rollout", {"planning_budget_s": 1e-12})
        self.assertIsNone(action)
        self.assertEqual(info["fallback_reason"], "planning_wall_clock_budget_exceeded")

    def test_disabling_clear_removes_it_from_both_planning_stages(self):
        for mode in ("rollout", "probe"):
            action, info = choose_action(knowledge(), (0, 0), 1, mode,
                                        {"allow_clear": False, "planning_budget_s": 2.})
            self.assertIsNotNone(action, info)
            self.assertEqual(action["kind"], "measure")
            for evaluation in info["evaluations"]:
                self.assertEqual(evaluation["action"]["kind"], "measure")
                for branch in evaluation["branches"]:
                    if branch["second_action"] is not None:
                        self.assertEqual(branch["second_action"]["kind"], "measure")

    def test_unknown_config_and_nonboolean_clear_option_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown planning configuration"):
            choose_action(knowledge(), (0, 0), 1, "rollout", {"partcle_count": 64})
        with self.assertRaisesRegex(ValueError, "allow_clear must be a bool"):
            choose_action(knowledge(), (0, 0), 1, "rollout", {"allow_clear": 1})


if __name__ == "__main__":
    unittest.main()
