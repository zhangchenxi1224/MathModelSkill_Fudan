"""Behavioral safety and integration tests; these are not performance claims."""
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

from bsolver.protocol import RobotClient
from bsolver.simulator import LocalSimulator, Source
from bsolver.strategy import Solver, SolverConfig
from bsolver.knowledge import InconsistentKnowledge
from diverse.policies import make_solver, ring_certificate, ORIGINAL_RING, ROUTES


def fixture(route="probe", *, sources=None, problem=3, local_limit=2, options=None,
            max_virtual_s=360000., drop_indices=()):
    env = LocalSimulator(sources or [], error_mode="zero", response_drop_indices=drop_indices)
    client = RobotClient(transport=env, max_retries=0, retry_delay=0.)
    solver = make_solver(client, SolverConfig(problem=problem, local_measure_limit=local_limit,
                                               max_virtual_s=max_virtual_s), route, options=options)
    return solver, client, env


class PolicyTests(unittest.TestCase):
    def test_robust_is_exact_frozen_solver_and_all_default_covers_match(self):
        robust, _, _ = fixture("robust")
        self.assertIs(type(robust), Solver)
        for route in ROUTES:
            other, _, _ = fixture(route)
            self.assertEqual(other.points, robust.points)
            self.assertEqual(other.config, robust.config)

    def test_robust_matches_direct_frozen_run_actions(self):
        adapter, _, environment = fixture("robust", local_limit=0)
        second_env = LocalSimulator([], error_mode="zero")
        direct = Solver(RobotClient(transport=second_env), adapter.config)
        a, b = adapter.run(), direct.run()
        for key in ("status", "measures", "clear_attempts", "switches", "stop_evidence",
                    "total_virtual_time_s", "independently_accounted_time_s"):
            self.assertEqual(a[key], b[key])
        def actions(env):
            return [(x["path"], x["request"].get("position"), x["request"].get("channel"))
                    for x in env.public_trace()]
        self.assertEqual(actions(environment), actions(second_env))

    def test_clear_failure_success_cost_and_no_clear_channel_switch(self):
        solver, client, env = fixture(sources=[Source(2, (100., 0.))])
        client.enter()
        self.assertFalse(solver._clear(2, (0., 0.), {"type": "bounded_experimental_probe"}))
        self.assertEqual(client.virtual_time, 3.)
        self.assertEqual(client.current_channel, 1)
        self.assertTrue(solver._clear(2, (100., 0.), {"type": "bounded_experimental_probe"}))
        self.assertEqual(client.virtual_time, 28.)  # 3 + 100/5 + 5
        self.assertEqual(client.current_channel, 1)
        self.assertEqual(solver.stats["clear_attempts"], 2)
        self.assertEqual(solver.stats["clear_successes"], 1)
        self.assertEqual(solver.stats["certified_clears"], 0)
        self.assertEqual(solver.channels[2].failed_clear_positions, [(0., 0.)])
        self.assertEqual(env.summary()["stats"]["switches"], 0)

    def test_probe_budget_per_target_failure_ledger_and_finite_fallback(self):
        solver, client, _ = fixture(sources=[Source(1, (600., 0.))], local_limit=2)
        client.enter()
        solver._measure(1, (0., 0.), "test_initial_direction")
        with patch("diverse.planning.choose_action", return_value=({"kind": "clear", "point": (0., 0.)}, {})), \
             patch("diverse.policies.choose_measurement", return_value=(None, {"reason": "test_no_candidate"})):
            solver._localize(1)
        probes = [d for d in solver.decisions if d["event"] == "clear"
                  and d["certificate"]["type"] == "bounded_experimental_probe"]
        self.assertEqual(len(probes), 1)
        self.assertEqual(probes[0]["response"]["clear_result"], "no_target_in_range")
        self.assertIn((0., 0.), solver.channels[1].failed_clear_positions)
        self.assertEqual(solver.stats["fallback_targets"], 1)
        self.assertEqual(solver.channels[1].status, "cleared")
        self.assertLessEqual(solver.stats["clear_attempts"], 111)
        self.assertLessEqual(solver._local_actions[1], solver.config.local_measure_limit)

    def test_zero_local_budget_never_calls_planner_and_terminates(self):
        solver, _, env = fixture("rollout", sources=[Source(1, (600., 0.))], local_limit=0)
        with patch("diverse.planning.choose_action", side_effect=AssertionError("must not plan")) as planner:
            result = solver.run()
        planner.assert_not_called()
        self.assertEqual(result["status"], "complete")
        self.assertTrue(env.summary()["all_cleared"])
        self.assertLessEqual(result["clear_attempts"], 110)
        self.assertLess(abs(result["timing_residual_s"]), .001)

    def test_planner_exception_falls_back_to_frozen_measurement(self):
        solver, client, _ = fixture("fisher", sources=[Source(1, (600., 0.))], local_limit=1)
        client.enter()
        solver._measure(1, (0., 0.), "test_initial_direction")
        with patch("diverse.planning.choose_action", side_effect=RuntimeError("test planner failure")), \
             patch("diverse.policies.choose_measurement", return_value=((600., 0.), {"mode": "test"})):
            solver._localize(1)
        self.assertEqual(solver.channels[1].status, "cleared")
        self.assertEqual(solver.stats["certified_clears"], 1)
        selected = next(d for d in solver.decisions if d["event"] == "choose_action")
        self.assertEqual(selected["selection"]["fallback"], "frozen_choose_measurement")

    def test_bad_or_repeated_proposals_are_replaced(self):
        for point in ((math.nan, 0.), (0., 0.)):
            solver, client, _ = fixture("infogain", sources=[Source(1, (600., 0.))], local_limit=1)
            client.enter()
            solver._measure(1, (0., 0.), "test_initial_direction")
            with patch("diverse.planning.choose_action", return_value=({"kind": "measure", "point": point}, {})), \
                 patch("diverse.policies.choose_measurement", return_value=((600., 0.), {})):
                solver._localize(1)
            self.assertEqual(solver.channels[1].status, "cleared")
            self.assertEqual(solver._local_actions[1], 1)

    def test_virtual_budget_exits_incomplete_without_claiming_coverage(self):
        solver, client, _ = fixture("probe", max_virtual_s=10.)
        result = solver.run()
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("BudgetExhausted", result["error"])
        self.assertIsNone(result["stop_evidence"])
        self.assertTrue(client.exited)

    def test_pending_ambiguous_action_blocks_exit(self):
        solver, client, env = fixture("joint_route", drop_indices=(2,))
        result = solver.run()
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNotNone(client.pending_request)
        self.assertFalse(client.exited)
        self.assertNotIn("/exit", [x["path"] for x in env.public_trace()])

    def test_joint_reorders_only_unobserved_suffix_and_rejects_bad_indices(self):
        solver, client, _ = fixture("joint_route")
        before = list(solver.points)
        for knowledge in solver.channels.values():
            knowledge.coverage_indices = {0, 1}
        client.position = solver.points[-1]
        solver._reorder_suffix(2)
        self.assertEqual(solver.points[:2], before[:2])
        self.assertEqual(set(solver.points), set(before))
        self.assertEqual(solver.points[2], client.position)
        self.assertTrue(all(k.coverage_indices == {0, 1} for k in solver.channels.values()))
        solver.channels[1].coverage_indices.add(3)
        with self.assertRaises(InconsistentKnowledge):
            solver._reorder_suffix(2)

    def test_joint_shared_measurements_count_toward_budget_and_do_not_repeat(self):
        solver, client, _ = fixture("joint_route", sources=[Source(1, (500., 0.))], local_limit=1)
        client.enter()
        solver._measure(1, (0., 0.), "test_initial_direction")
        solver._measure(20, (450., 400.), "test_move")
        with patch("diverse.policies.direction_outcome_bound", return_value=10.):
            solver._share_at_current()
            solver._share_at_current()
        shared = [d for d in solver.decisions if d["event"] == "measure"
                  and d["reason"] == "shared_detected_station"]
        self.assertEqual(len(shared), 1)
        self.assertEqual(solver._local_actions[1], 1)
        self.assertEqual(solver._remaining_local_actions(1), 0)
        self.assertEqual(solver.channels[1].coverage_indices, set())
        self.assertEqual(solver.channels[20].status, "unknown")

    def test_joint_empty_scene_completes_with_every_coverage_index(self):
        solver, _, _ = fixture("joint_route")
        result = solver.run()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["stop_evidence"]["type"], "per_channel_coverage")
        self.assertTrue(all(indices == list(range(len(solver.points)))
                            for indices in result["stop_evidence"]["checked_indices"].values()))

    def test_ring_certificate_bound_is_tight_at_voronoi_or_boundary_witness(self):
        for ring in (1125., 1300., ORIGINAL_RING, 1700.):
            proof = ring_certificate(ring)
            solver, _, _ = fixture("robust", options={"ring_radius": ring})
            witness_distances = []
            for radius in (ring / math.sqrt(3.), 1800.):
                point = (radius * math.cos(math.pi / 6.), radius * math.sin(math.pi / 6.))
                witness_distances.append(min(math.dist(point, q) for q in solver.points))
            self.assertAlmostEqual(max(witness_distances), proof["covering_radius_upper_m"] - 1e-6, places=7)
            self.assertGreater(proof["distance_margin_m"], 0.)

    def test_ring_is_separate_ablation_with_evidence_and_rejected_for_q4(self):
        solver, _, _ = fixture("robust", options={"ring_radius": 1300.})
        result = solver.run()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["stop_evidence"]["analytic_certificate"]["ring_radius_m"], 1300.)
        for route in ROUTES:
            with self.assertRaises(ValueError):
                fixture(route, problem=4, options={"ring_radius": 1300.})
        for bad in (0., 1000., 1800., float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                ring_certificate(bad)


if __name__ == "__main__":
    unittest.main()
