"""Constructive standard-survey tests; simulator truth stays in the evaluator."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from bsolver.geometry import contains, distance
from bsolver.protocol import RobotClient
from bsolver.simulator import LocalSimulator, Source, generate_sources
from bsolver.strategy import SolverConfig
from bsolver.survey import SURVEY_PROTOCOL_VERSION, SurveySolver, run_survey


class Clock:
    def __init__(self):
        self.now = 0.

    def __call__(self):
        return self.now

    def wall(self):
        return 1_700_000_000.+self.now

    def sleep(self, seconds):
        self.now += seconds


class SurveyTests(unittest.TestCase):
    def fixture(self, sources, *, problem=3, survey_seed=7103, sample_count=2,
                env_kwargs=None, config_kwargs=None, client_kwargs=None,
                transport_wrapper=None, decision_log=None):
        clock = Clock()
        env = LocalSimulator(sources, seed=413, error_mode="extreme",
                             clock=clock, wall_clock=clock.wall, **(env_kwargs or {}))
        transport = (transport_wrapper(env, clock) if transport_wrapper else
                     lambda path, body: env(path, body))
        client = RobotClient(transport=transport, clock=clock, wall_clock=clock.wall,
                             sleeper=clock.sleep, retry_delay=0, request_prefix="survey-test",
                             **(client_kwargs or {}))
        config = SolverConfig(problem=problem, sensing="fixed", local_measure_limit=0,
                              **(config_kwargs or {}))
        solver = SurveySolver(client, config, survey_seed=survey_seed,
                              sample_count=sample_count, decision_log=decision_log)
        return clock, env, client, solver

    def audit_complete(self, sources, env, client, solver, result):
        self.assertEqual(result["status"], "complete", result.get("error"))
        self.assertTrue(result["survey_sampling_complete"])
        self.assertTrue(result["cleanup_complete"])
        self.assertTrue(env.summary()["all_cleared"])
        self.assertTrue(client.exited)
        self.assertEqual(result["clear_successes"], len(sources))
        self.assertIsNone(result["source_total"])
        self.assertIsNone(result["clear_fraction"])
        self.assertLess(abs(result["timing_residual_s"]), .002)
        truth = {source.channel: source for source in sources}
        measures = [e for e in solver.decisions if e["event"] == "measure"]
        coverage = [e for e in measures if e["phase"] == "coverage"]
        self.assertEqual(len(coverage), 20*len(solver.points))
        self.assertEqual(result["phase_stats"]["coverage"]["measures"], len(coverage))
        for i, point in enumerate(solver.points):
            station = coverage[20*i:20*(i+1)]
            self.assertEqual([e["target_channel"] for e in station], list(range(1, 21)))
            self.assertTrue(all(e["position"] == point for e in station))
            self.assertTrue(all(e["station_id"] == f"coverage:{i:03d}" for e in station))
        extras = [e for e in measures if e["phase"] == "survey"]
        expected = (16 if solver.config.problem == 3 else 19)*min(2, len(sources))
        if solver.sample_count == 2:
            self.assertEqual(len(extras), expected)
        survey_end = max(e["sequence"] for e in coverage+extras)
        clears = [e for e in solver.decisions if e["event"] == "clear"]
        self.assertTrue(all(e["sequence"] > survey_end and e["phase"] == "cleanup" for e in clears))
        for event in solver.decisions:
            snapshot = event.get("knowledge")
            if snapshot and snapshot["channel"] in truth:
                source = truth[snapshot["channel"]]
                self.assertTrue(contains(snapshot["hull"], source.position, tol=1e-5))
            if event["event"] == "clear" and event["certificate"]["type"] in ("near", "outer_hull"):
                self.assertLessEqual(distance(event["position"],
                    truth[event["knowledge"]["channel"]].position), 20)
        for source in sources:
            self.assertTrue(solver.channels[source.channel].compatible_hidden_state(
                source.position, source.radius, source.direction_deg))
        for event in measures:
            self.assertIn("station_id", event)
            self.assertIn("virtual_time_s", event["response"])
            self.assertIsNotNone(event["request_id"])
        self.assertIsNotNone(result["stop_evidence"])

    def test_full_scan_and_probes_even_after_sixteen_near_detections(self):
        sources = [Source(channel, (0, 0)) for channel in range(1, 17)]
        _, env, client, solver = self.fixture(sources)
        result = solver.run()
        self.audit_complete(sources, env, client, solver, result)
        self.assertEqual(result["deferred_near_channels"], list(range(1, 17)))
        self.assertEqual(result["stop_evidence"]["type"], "known_upper_bound")
        self.assertEqual(len([k for k in solver.channels.values()
                              if k.status == "cleared" and k.first_direction is None]), 14)

    def test_q3_ten_source_local_smoke(self):
        sources = generate_sources(8103, count=10, directional_fraction=0.)
        _, env, client, solver = self.fixture(sources)
        self.audit_complete(sources, env, client, solver, solver.run())

    def test_q4_boundary_outward_local_smoke(self):
        sources = []
        for j in range(10):
            theta = 2*math.pi*j/10+.021
            sources.append(Source(j+1, (1800*math.cos(theta), 1800*math.sin(theta)),
                                  1000, math.degrees(theta)))
        _, env, client, solver = self.fixture(sources, problem=4)
        result = solver.run()
        self.audit_complete(sources, env, client, solver, result)
        self.assertEqual(len(solver.points), 31)
        self.assertEqual(result["phase_stats"]["coverage"]["measures"], 620)
        self.assertEqual(result["phase_stats"]["survey"]["measures"], 38)

    def test_plan_is_preregistered_seeded_and_frozen_before_all_extra_probes(self):
        sources = generate_sources(8349, count=10)
        _, env, client, solver = self.fixture(sources, survey_seed=115907)
        result = solver.run()
        self.audit_complete(sources, env, client, solver, result)
        self.assertEqual(result["sampled_channels"], random.Random(115907).sample(
            sorted(source.channel for source in sources), 2))
        event = next(e for e in solver.decisions if e["event"] == "survey_plan")
        plan = event["plan"]
        self.assertEqual(plan, solver.survey_plan)
        self.assertTrue(all(event["sequence"] < e["sequence"] for e in solver.decisions
                            if e["event"] == "measure" and e["phase"] == "survey"))
        for target in plan:
            self.assertFalse(target["reference"]["estimate_is_ground_truth"])
            self.assertEqual(len(target["reference"]["observations"]), len(solver.points))
            channel = target["channel"]
            actual = [e for e in solver.decisions if e["event"] == "measure"
                      and e["phase"] == "survey" and e["target_channel"] == channel]
            self.assertEqual([e["position"] for e in actual],
                             [p["position"] for p in target["probes"]])
            source = next(source for source in sources if source.channel == channel)
            for probe in target["probes"]:
                lower, upper = probe["source_distance_interval_m"]
                self.assertLessEqual(lower-1e-5, distance(source.position, probe["position"]))
                self.assertGreaterEqual(upper+1e-5, distance(source.position, probe["position"]))
        _, _, _, repeated = self.fixture(sources, survey_seed=115907)
        self.assertEqual(repeated.run()["plan_sha256"], result["plan_sha256"])

    def test_repeat_matches_positive_anchor_and_neighbors_are_one_meter(self):
        sources = [Source(7, (640, 410), 1000, 157)]
        _, env, client, solver = self.fixture(sources, problem=4)
        result = solver.run()
        self.audit_complete(sources, env, client, solver, result)
        target = solver.survey_plan[0]
        reference = target["reference"]
        old_obs = reference["observations"][reference["anchor_observation_index"]]
        self.assertIn(old_obs["result"], ("direction", "near"))
        repeats = [event for event in solver.decisions if event["event"] == "measure"
                   and event["phase"] == "survey"
                   and event["measurement_design"]["kind"] == "same_point_repeat"]
        self.assertEqual(len(repeats), 1)
        self.assertEqual(repeats[0]["position"], old_obs["position"])
        self.assertEqual(repeats[0]["response"]["measure_result"], old_obs["result"])
        if old_obs["result"] == "direction":
            self.assertEqual(repeats[0]["response"]["svd_deg"], old_obs["angle"])
        neighbors = [p for p in target["probes"] if p["kind"] == "neighbor"]
        self.assertEqual(len(neighbors), 2)
        for point in neighbors:
            self.assertAlmostEqual(distance(point["position"], old_obs["position"]), 1.)

    def test_lost_measure_response_is_not_a_second_observation(self):
        dropped = []

        def drop(path, body, response):
            if path == "/measure" and not dropped:
                dropped.append(body["request_id"])
                return True
            return False

        sources = [Source(3, (0, 0))]
        _, env, client, solver = self.fixture(sources,
            env_kwargs={"drop_response_after_execute": drop})
        result = solver.run()
        self.audit_complete(sources, env, client, solver, result)
        self.assertEqual(result["measures"], env.summary()["stats"]["measures"])
        self.assertEqual(len(dropped), 1)
        self.assertEqual(len([event for event in solver.decisions
                              if event.get("request_id") == dropped[0]]), 1)

    def test_coverage_wall_budget_exhaustion_stays_incomplete(self):
        _, env, client, solver = self.fixture([Source(1, (0, 0))],
                                              env_kwargs={"max_real_duration_s": 4})
        result = solver.run()
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["survey_sampling_complete"])
        self.assertFalse(result["coverage_completed"])
        self.assertEqual(result["clear_attempts"], 0)
        self.assertIsNone(result["stop_evidence"])

    def test_exhaustion_mid_extra_survey_is_not_complete_and_does_not_clear(self):
        sources = [Source(1, (0, 0))]
        _, _, _, estimate = self.fixture(sources, sample_count=0)
        baseline = estimate.run()
        coverage_end = baseline["phase_stats"]["coverage"]["end_virtual_time_s"]
        plan = estimate._make_target_plan(1)
        q = plan["probes"][0]["position"]
        first_cost = distance(estimate.points[-1], q)/5+6
        budget = coverage_end+first_cost+10
        _, env, client, solver = self.fixture(sources, config_kwargs={"max_virtual_s": budget})
        result = solver.run()
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(result["coverage_completed"])
        self.assertFalse(result["extra_survey_completed"])
        self.assertGreater(result["phase_stats"]["survey"]["measures"], 0)
        self.assertEqual(result["clear_attempts"], 0)
        self.assertFalse(env.summary()["all_cleared"])
        self.assertIsNone(result["stop_evidence"])
        self.assertLess(result["total_virtual_time_s"], budget)

    def test_smaller_server_virtual_limit_is_respected(self):
        _, env, client, solver = self.fixture([Source(1, (0, 0))],
            env_kwargs={"max_virtual_duration_s": 8})
        result = solver.run()
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("server_virtual_time_reserve", result["error"])
        self.assertLess(result["total_virtual_time_s"], 8)
        self.assertTrue(client.exited)

    def test_pending_measure_does_not_send_clear_or_exit(self):
        paths = []

        def wrapper(env, clock):
            def transport(path, body):
                paths.append(path)
                response = env(path, body)
                if path == "/measure":
                    raise TimeoutError("lost response")
                return response
            return transport

        _, env, client, solver = self.fixture([Source(1, (0, 0))],
                                              transport_wrapper=wrapper)
        result = solver.run()
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNotNone(client.pending_request)
        self.assertNotIn("/clear", paths)
        self.assertNotIn("/exit", paths)
        self.assertEqual(env.summary()["stats"]["measures"], 1)

    def test_json_log_matches_confirmed_requests_and_function_interface(self):
        sources = [Source(4, (150, 180))]
        _, env, client, solver = self.fixture(sources)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"decisions.jsonl"
            result = run_survey(client, solver.config, 7126, path)
            items = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(result["status"], "complete", result["error"])
            requests = {entry["request"]["request_id"]: entry for entry in client.history
                        if entry["outcome"] == "accepted"}
            for event in items:
                self.assertEqual(event["survey_protocol_version"], SURVEY_PROTOCOL_VERSION)
                if event["event"] == "measure":
                    request = requests[event["request_id"]]
                    self.assertEqual(event["response"], request["response"])
                    self.assertEqual(event["target_channel"], request["request"]["channel"])
            self.assertTrue(env.summary()["all_cleared"])

    def test_no_history_keeps_auditable_response_join_key(self):
        _, _, _, solver = self.fixture([Source(1, (0, 0))], sample_count=0,
                                       client_kwargs={"keep_history": False})
        result = solver.run()
        self.assertEqual(result["status"], "complete", result["error"])
        for event in solver.decisions:
            if event["event"] == "measure":
                self.assertIsNone(event["request_id"])
                self.assertIn("virtual_time_s", event["response"])
                self.assertIn("target_channel", event)
                self.assertIn("position", event)

    def test_input_validation_and_clear_guard(self):
        _, _, client, solver = self.fixture([Source(1, (0, 0))])
        with self.assertRaisesRegex(RuntimeError, "prohibits clearing"):
            solver._clear(1, (0, 0), {"type": "near"})
        for seed in (True, 1.2, "71"):
            with self.assertRaises(ValueError):
                SurveySolver(client, survey_seed=seed)
        for count in (True, -1, 21, 1.2):
            with self.assertRaises(ValueError):
                SurveySolver(client, survey_seed=7, sample_count=count)
        result = solver.run()
        self.assertEqual(result["status"], "complete", result["error"])
        with self.assertRaisesRegex(RuntimeError, "only once"):
            solver.run()

    def test_exactly_coincident_estimate_and_repeat_anchor_has_no_bearing(self):
        _, _, _, solver = self.fixture([Source(1, (0, 0))])
        solver.channels[1].observe((0., 0.), {"measure_result": "near"})
        with patch("bsolver.survey.minimum_enclosing_circle", return_value=((0., 0.), 5.01)):
            plan = solver._make_target_plan(1)
        repeats = [probe for probe in plan["probes"] if probe["kind"] == "same_point_repeat"]
        self.assertEqual(len(repeats), 2)
        for probe in repeats:
            self.assertEqual(probe["position"], (0., 0.))
            self.assertEqual(probe["nominal_distance_m"], 0.)
            self.assertIsNone(probe["nominal_bearing_deg"])


if __name__ == "__main__":
    unittest.main()
