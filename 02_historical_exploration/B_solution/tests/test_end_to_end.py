"""Independent synthetic end-to-end safety checks, never official test runs.

Truth remains in the fixture/evaluator. The production Solver receives only a
RobotClient whose transport exposes the four published endpoint responses.
"""
from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bsolver.geometry import contains, distance  # noqa: E402
from bsolver.protocol import RobotClient  # noqa: E402
from bsolver.simulator import LocalSimulator, Source  # noqa: E402
from bsolver.strategy import Solver, SolverConfig  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def wall(self):
        return 1_700_000_000.0 + self.now


def ring_sources(count, *, directional=False, boundary=False):
    result = []
    for j in range(count):
        theta = 2 * math.pi * j / count + 0.073
        r = 1800.0 if boundary else 420.0 + 70.0 * (j % 7)
        result.append(Source(j + 1, (r * math.cos(theta), r * math.sin(theta)),
                             1000.0, math.degrees(theta) if directional else None))
    return result


class EndToEndTests(unittest.TestCase):
    def fixture(self, sources, *, error_mode="extreme", env_kwargs=None,
                transport_wrapper=None, **config):
        clock = Clock()
        env = LocalSimulator(sources, clock=clock, wall_clock=clock.wall,
                             seed=1841, error_mode=error_mode,
                             enforce_case_size=True, **(env_kwargs or {}))
        transport = (transport_wrapper(env, clock) if transport_wrapper else
                     lambda path, body: env(path, body))
        client = RobotClient(transport=transport, clock=clock, wall_clock=clock.wall,
                             sleeper=clock.sleep, retry_delay=0, request_prefix="e2e")
        policy = Solver(client, SolverConfig(**config))
        return clock, env, client, policy

    def audit_complete(self, sources, env, client, policy, result):
        self.assertEqual(result["status"], "complete", result.get("error"))
        truth = {s.channel: s for s in sources}
        score = env.summary()
        self.assertTrue(score["all_cleared"])
        self.assertEqual(result["clear_successes"], len(sources))
        self.assertEqual(client.cleared_channels, set(truth))
        self.assertEqual(score["cleared_count"], len(sources))
        self.assertIsNone(result["source_total"], "policy must not obtain hidden source count")
        self.assertIsNone(result["clear_fraction"], "truth-based fraction belongs to evaluator only")
        self.assertLess(result["total_virtual_time_s"], 360000)
        # Each move is rounded to microseconds by the synthetic protocol.
        self.assertLess(abs(result["timing_residual_s"]), .002)
        for item in policy.decisions:
            snapshot = item.get("knowledge")
            if snapshot and snapshot["channel"] in truth:
                source = truth[snapshot["channel"]]
                self.assertTrue(contains(snapshot["hull"], source.position, tol=1e-5),
                                f"truth left outer hull at event {item['sequence']}")
            if item["event"] == "clear" and item["certificate"]["type"] in ("near", "outer_hull"):
                channel = item["knowledge"]["channel"]
                self.assertIn(channel, truth)
                self.assertLessEqual(distance(item["position"], truth[channel].position), 20)
                self.assertEqual(item["response"]["clear_result"], "success")
        for source in sources:
            self.assertTrue(policy.channels[source.channel].compatible_hidden_state(
                source.position, source.radius, source.direction_deg))
        evidence = result["stop_evidence"]
        self.assertIsNotNone(evidence)
        if len(sources) < 16:
            self.assertEqual(evidence["type"], "per_channel_coverage")
            self.assertEqual(set(evidence["absent_channels"]), set(range(1, 21)) - set(truth))
            for channel in evidence["absent_channels"]:
                self.assertEqual(set(evidence["checked_indices"][str(channel)]),
                                 set(range(len(policy.points))))

    def test_q3_ten_omnidirectional_sources(self):
        sources = ring_sources(10)
        _, env, client, policy = self.fixture(sources, problem=3, sensing="fixed",
                                              local_measure_limit=2, scheduling="joint")
        result = policy.run()
        self.audit_complete(sources, env, client, policy, result)

    def test_q3_sixteen_sources_uses_only_confirmed_upper_bound(self):
        sources = ring_sources(16)
        _, env, client, policy = self.fixture(sources, problem=3, sensing="fixed",
                                              local_measure_limit=1, scheduling="immediate")
        result = policy.run()
        self.audit_complete(sources, env, client, policy, result)
        self.assertEqual(result["stop_evidence"]["type"], "known_upper_bound")
        clears = [e for e in policy.decisions if e["event"] == "clear"
                  and e["response"]["clear_result"] == "success"]
        self.assertEqual(len(clears), 16)

    def test_q4_all_directional_outward_boundary_sources(self):
        sources = ring_sources(10, directional=True, boundary=True)
        _, env, client, policy = self.fixture(sources, problem=4, sensing="fixed",
                                              local_measure_limit=0, scheduling="joint")
        result = policy.run()
        self.audit_complete(sources, env, client, policy, result)
        self.assertGreater(result["fallback_targets"], 0)
        self.assertTrue(any(math.hypot(*e["position"]) > 1800 for e in policy.decisions
                            if e["event"] == "measure"))

    def test_q4_handles_all_omnidirectional_without_type_oracle(self):
        sources = ring_sources(10)
        _, env, client, policy = self.fixture(sources, problem=4, sensing="fixed",
                                              local_measure_limit=1, scheduling="scan_then_clear")
        self.audit_complete(sources, env, client, policy, policy.run())

    def test_no_early_exit_after_nine_easy_clears_with_hidden_outward_source(self):
        sources = [Source(j, (0, 0), 1000) for j in range(1, 10)]
        sources.append(Source(20, (1800, 0), 1000, 0))
        _, env, client, policy = self.fixture(sources, problem=4, sensing="fixed",
                                              local_measure_limit=0, scheduling="immediate")
        result = policy.run()
        self.audit_complete(sources, env, client, policy, result)
        clears = [e for e in policy.decisions if e["event"] == "clear"
                  and e["response"]["clear_result"] == "success"]
        self.assertEqual({e["knowledge"]["channel"] for e in clears[:9]}, set(range(1, 10)))
        self.assertEqual(clears[-1]["knowledge"]["channel"], 20)
        measures20 = [e for e in policy.decisions if e["event"] == "measure"
                      and e["knowledge"]["channel"] == 20]
        self.assertEqual(measures20[0]["response"]["measure_result"], "no_signal")
        self.assertTrue(any(e["position"][0] >= 1800 and
                            e["response"]["measure_result"] in ("direction", "near")
                            for e in measures20))
        self.assertGreater(clears[-1]["sequence"], clears[8]["sequence"])

    def test_near_clear_and_lost_success_response_are_accounted_once(self):
        sources = [Source(j, (0, 0), 1000) for j in range(1, 11)]
        dropped = []

        def drop_first_clear(path, body, response):
            if path == "/clear" and not dropped:
                dropped.append(body["request_id"])
                return True
            return False

        _, env, client, policy = self.fixture(sources, problem=3,
            env_kwargs={"drop_response_after_execute": drop_first_clear})
        result = policy.run()
        self.audit_complete(sources, env, client, policy, result)
        self.assertEqual(len(dropped), 1)
        self.assertEqual(result["clear_attempts"], 10)
        self.assertEqual(result["certified_clears"], 10)
        self.assertEqual(result["fallback_targets"], 0)
        near = [e for e in policy.decisions if e["event"] == "clear"
                and e["certificate"]["type"] == "near"]
        self.assertEqual(len(near), 10)

    def test_finite_fallback_failed_moves_and_clear_channel_state(self):
        sources = ring_sources(10)
        _, env, client, policy = self.fixture(sources, problem=3, error_mode="positive",
                                              local_measure_limit=0, scheduling="immediate")
        result = policy.run()
        self.audit_complete(sources, env, client, policy, result)
        self.assertGreater(result["clear_attempts"], result["clear_successes"])
        for channel in range(1, 11):
            attempts = [e for e in policy.decisions if e["event"] == "clear"
                        and e["knowledge"]["channel"] == channel]
            self.assertLessEqual(len(attempts), 110)
        previous = (0.0, 0.0)
        observed_failure_move = False
        current_channel = 1
        for event in policy.decisions:
            if event["event"] == "measure":
                current_channel = event["knowledge"]["channel"]
            if event["event"] in ("measure", "clear"):
                if event["event"] == "clear":
                    self.assertEqual(event["channel"], current_channel,
                                     "clear's target channel must not retune the receiver")
                    if event["response"]["clear_result"] == "no_target_in_range":
                        observed_failure_move |= distance(previous, event["position"]) > 1
                previous = event["position"]
        self.assertTrue(observed_failure_move)

    def test_active_candidate_policy_smoke_with_extreme_errors(self):
        sources = ring_sources(10)
        _, env, client, policy = self.fixture(sources, problem=3, sensing="active",
                                              local_measure_limit=1, scheduling="joint")
        result = policy.run()
        self.audit_complete(sources, env, client, policy, result)
        self.assertTrue(any(e["event"] == "choose_measurement" for e in policy.decisions))

    def test_insufficient_wall_clock_is_not_reported_as_success(self):
        sources = ring_sources(10)
        _, env, client, policy = self.fixture(sources, problem=3,
                                              env_kwargs={"max_real_duration_s": 4})
        result = policy.run()
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["stop_evidence"])
        self.assertFalse(env.summary()["all_cleared"])
        self.assertIn("real_time_reserve", result["error"])

    def test_insufficient_virtual_budget_is_not_reported_as_success(self):
        sources = ring_sources(10)
        _, env, client, policy = self.fixture(sources, problem=3, max_virtual_s=8)
        result = policy.run()
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["stop_evidence"])
        self.assertFalse(env.summary()["all_cleared"])
        self.assertIn("virtual_time_reserve", result["error"])

    def test_ambiguous_measure_does_not_trigger_new_exit(self):
        sources = ring_sources(10)
        called = []

        def wrapper(env, clock):
            def transport(path, body):
                called.append((path, body["request_id"]))
                result = env(path, body)
                if path == "/measure":
                    raise TimeoutError("all responses to an executed measure are lost")
                return result
            return transport

        _, env, client, policy = self.fixture(sources, problem=3, transport_wrapper=wrapper)
        result = policy.run()
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["stop_evidence"])
        self.assertIsNotNone(client.pending_request)
        self.assertFalse(any(path == "/exit" for path, _ in called))
        self.assertNotIn("exit:", result["error"], "strategy must skip, not attempt, a new exit")
        measure_ids = {identity for path, identity in called if path == "/measure"}
        self.assertEqual(len(measure_ids), 1)
        self.assertEqual(env.summary()["stats"]["measures"], 1)


if __name__ == "__main__":
    unittest.main()
