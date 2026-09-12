"""Small-grid ranking tests; all safety checks remain independent of samples."""
from __future__ import annotations

import ast
import copy
import math
from pathlib import Path
import unittest

from bsolver.geometry import contains, distance
from bsolver.knowledge import ChannelKnowledge
from bsolver.nosignal_sensing import (Hypothesis, NoSignalConfig, NoSignalSolver,
    branch_probabilities, choose_measurement_nosignal, compatible_hypotheses,
    optical_fallback_cost_bound, positive_convex_visibility)
from bsolver.protocol import RobotClient
from bsolver.sensing import candidate_points, choose_measurement, optical_fallback_points
from bsolver.simulator import LocalSimulator, Source, generate_sources
from bsolver.strategy import SolverConfig


def first_bearing(problem=4, angle=0.):
    knowledge = ChannelKnowledge(1, problem)
    knowledge.observe((0., 0.), {"measure_result": "direction", "svd_deg": angle})
    return knowledge


class NoSignalTests(unittest.TestCase):
    def test_same_observations_produce_same_point_probabilities_and_score(self):
        knowledge = first_bearing()
        before = copy.deepcopy(knowledge)
        first = choose_measurement_nosignal(knowledge, (0., 0.))
        second = choose_measurement_nosignal(knowledge, (0., 0.))
        self.assertEqual(first, second)
        self.assertEqual(knowledge, before, "selection must not mutate the evidence")
        self.assertLessEqual(first[1]["hypothesis_summary"]["evaluated_states"], 7*3*25)

    def test_candidates_and_direction_certificates_are_exactly_baseline(self):
        knowledge = first_bearing(angle=30.)
        q, info = choose_measurement_nosignal(knowledge, (0., 0.))
        original_q, original_info = choose_measurement(knowledge, (0., 0.))
        self.assertEqual([e["point"] for e in info["evaluations"]],
                         [e["point"] for e in original_info["evaluations"]])
        self.assertEqual([e["direction_posterior_radius_bound_m"] for e in info["evaluations"]],
                         [e["direction_posterior_radius_bound_m"] for e in original_info["evaluations"]])
        self.assertEqual(info["baseline_selected_point"], original_q)
        self.assertIn(q, [point for point, _ in candidate_points(knowledge, (0., 0.))])
        self.assertTrue(info["selection_changed_from_baseline"], "fixture should exercise an actual ranking change")

    def test_filter_uses_one_fixed_radius_and_orientation_for_all_observations(self):
        knowledge = ChannelKnowledge(1, 4)
        knowledge.observe((1200., 0.), {"measure_result": "direction", "svd_deg": 180.})
        knowledge.observe((-1200., 0.), {"measure_result": "direction", "svd_deg": 0.})
        knowledge.observe((0., 1300.), {"measure_result": "no_signal"})
        knowledge.hull = [(0., 0.)]  # A synthetic, exactly known position for this grid test.
        hypotheses, summary = compatible_hypotheses(knowledge)
        self.assertTrue(hypotheses)
        states = {(h.radius, h.direction_deg) for h in hypotheses}
        self.assertEqual(states, {(1250., None), (1250., 90.), (1250., 270.), (1500., 270.)})
        for h in hypotheses:
            self.assertTrue(knowledge.compatible_hidden_state(h.position, h.radius, h.direction_deg))
        self.assertNotIn((1500., 90.), states, "cannot rotate the same source to explain the negative")
        self.assertNotIn((1000., None), states, "cannot grow R independently for each positive")

    def test_orientation_grid_does_not_overweight_directional_component(self):
        knowledge = ChannelKnowledge(1, 4)
        knowledge.observe((0., 0.), {"measure_result": "near"})
        knowledge.hull = [(0., 0.)]
        for count in (4, 12, 24):
            samples, summary = compatible_hypotheses(knowledge,
                NoSignalConfig(directional_prior=.3, orientation_sample_count=count))
            self.assertAlmostEqual(sum(h.weight for h in samples if h.direction_deg is not None), .3)
            self.assertAlmostEqual(sum(h.weight for h in samples if h.direction_deg is None), .7)
            self.assertEqual(summary["compatible_directional_states"], 3*count)

    def test_branch_probabilities_sum_to_one_and_score_has_seconds_units(self):
        samples = [Hypothesis((0., 0.), 1000., None, .2),
                   Hypothesis((100., 0.), 1000., None, .3),
                   Hypothesis((10., 0.), 1000., 0., .5)]
        self.assertEqual(branch_probabilities(samples, (0., 0.)),
                         {"direction": .3, "near": .2, "no_signal": .5})
        _, info = choose_measurement_nosignal(first_bearing(), (0., 0.))
        for evaluation in info["evaluations"]:
            p, costs = evaluation["branch_probabilities"], evaluation["cost_terms_s"]
            self.assertAlmostEqual(sum(p.values()), 1.)
            self.assertTrue(all(0 <= value <= 1 for value in p.values()))
            self.assertAlmostEqual(evaluation["score_s"], costs["movement"]+costs["measurement"]
                +p["direction"]*costs["direction_branch_proxy"]
                +p["near"]*costs["near_branch_clear"]
                +p["no_signal"]*costs["no_signal_immediate_fallback"])

    def test_positive_convex_membership_is_not_outward_padded(self):
        triangle = [(0., 0.), (10., 0.), (0., 10.)]
        self.assertTrue(positive_convex_visibility(triangle, (2., 2.)))
        self.assertTrue(positive_convex_visibility(triangle, (5., 5.)))
        self.assertFalse(positive_convex_visibility(triangle, (5., 5.+1e-12)))
        self.assertTrue(positive_convex_visibility([(0., 0.), (1., 0.)], (.5, 0.)))
        self.assertFalse(positive_convex_visibility([(0., 0.), (1., 0.)], (.5, 1e-100)))
        self.assertFalse(positive_convex_visibility([], (0., 0.)))

    def test_zero_grid_states_with_a_feasible_real_state_returns_baseline(self):
        knowledge = ChannelKnowledge(1, 3)
        knowledge.observe((1050., 0.), {"measure_result": "direction", "svd_deg": 180.})
        knowledge.observe((1120., 0.), {"measure_result": "no_signal"})
        knowledge.hull = [(0., 0.)]
        self.assertTrue(knowledge.compatible_hidden_state((0., 0.), 1100., None))
        samples, _ = compatible_hypotheses(knowledge)
        self.assertEqual(samples, [], "valid R=1100 falls between the preregistered radius grid")
        q, info = choose_measurement_nosignal(knowledge, (1120., 0.))
        original_q, _ = choose_measurement(knowledge, (1120., 0.))
        self.assertEqual(q, original_q)
        self.assertEqual(info["baseline_fallback_reason"], "no_compatible_grid_states")

    def test_optical_cost_bounds_an_actual_successful_fallback_walk(self):
        knowledge = first_bearing()
        actual_g = (700., 5.)  # Held only by this evaluator.
        current = (100., 250.)
        bound = optical_fallback_cost_bound(knowledge, current)
        self.assertIsNotNone(bound)
        points = optical_fallback_points(knowledge.first_direction)
        if distance(current, points[-1]) < distance(current, points[0]):
            points.reverse()
        from bsolver.geometry import distance_to_polygon
        points = [p for p in points if distance_to_polygon(knowledge.hull, p) <= 20.+1e-5]
        walk, attempts, last = 0., 0, current
        for p in points:
            walk += distance(last, p)
            attempts += 1
            last = p
            if distance(p, actual_g) <= 20:
                break
        else:
            self.fail("fixture should be covered by the existing finite fallback")
        self.assertLessEqual(walk/5+3*attempts+2, bound["cost_s"]+1e-8)
        self.assertLessEqual(bound["optical_attempt_upper_bound"], 110)

    def test_q3_samples_and_certified_candidates_have_no_dark_branch(self):
        knowledge = first_bearing(problem=3)
        samples, _ = compatible_hypotheses(knowledge)
        self.assertTrue(samples)
        self.assertTrue(all(h.direction_deg is None for h in samples))
        _, info = choose_measurement_nosignal(knowledge, (0., 0.))
        self.assertTrue(all(e["branch_probabilities"]["no_signal"] == 0. for e in info["evaluations"]))

    def test_fixed_and_estimated_modes_preserve_original_action(self):
        knowledge = first_bearing()
        for mode in ("fixed", "estimated"):
            q, info = choose_measurement_nosignal(knowledge, (0., 0.), mode)
            original, _ = choose_measurement(knowledge, (0., 0.), mode)
            self.assertEqual(q, original)
            self.assertEqual(info["baseline_fallback_reason"], "variant_only_changes_active_mode")

    def test_end_to_end_safety_with_omni_and_boundary_directional_sources(self):
        cases = [(3, generate_sources(631103, count=10, directional_fraction=0.))]
        boundary = [Source(i+1, (1800.*math.cos(i*.6283+.051), 1800.*math.sin(i*.6283+.051)),
                           1000., math.degrees(i*.6283+.051)) for i in range(10)]
        cases.append((4, boundary))
        for problem, sources in cases:
            env = LocalSimulator(sources, seed=1917, error_mode="extreme", enforce_case_size=True)
            client = RobotClient(transport=lambda path, body: env(path, body), retry_delay=0)
            solver = NoSignalSolver(client, SolverConfig(problem=problem, local_measure_limit=2))
            result = solver.run()
            self.assertEqual(result["status"], "complete", result["error"])
            self.assertTrue(env.summary()["all_cleared"])
            self.assertEqual(result["sensing_variant"], "sampled_nosignal_v1")
            self.assertIsNone(result["source_total"])
            self.assertLess(abs(result["timing_residual_s"]), .002)
            truth = {source.channel: source for source in sources}
            for event in solver.decisions:
                state = event.get("knowledge")
                if state and state["channel"] in truth:
                    self.assertTrue(contains(state["hull"], truth[state["channel"]].position, tol=1e-5))
                if event["event"] == "clear" and event["certificate"]["type"] in ("near", "outer_hull"):
                    self.assertLessEqual(distance(event["position"], truth[state["channel"]].position), 20.)
            self.assertIsNotNone(result["stop_evidence"])

    def test_module_imports_no_source_generator_or_simulator(self):
        source = Path(__file__).resolve().parents[1]/"src"/"bsolver"/"nosignal_sensing.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertFalse(any(name and ("simulator" in name or "experiments" in name) for name in imports))
        self.assertEqual(NoSignalConfig().directional_prior, .5)
        with self.assertRaises(ValueError):
            NoSignalConfig(position_sample_count=8)
        with self.assertRaises(ValueError):
            NoSignalConfig(radius_samples=(1000., 1500.))


if __name__ == "__main__":
    unittest.main()
