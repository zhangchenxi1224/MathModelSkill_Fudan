import copy
import json
import math
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bsolver.protocol import ConnectionClosedError, ConflictError, HTTPStatusError, RobotClient, TransportError
from bsolver.simulator import FixedErrorField, LocalSimulator, Source, generate_sources


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def wall(self):
        return 1_700_000_000 + self.now


def body(identity, channel=None, position=None, **extra):
    result = {"arena_id": "default", "robot_id": "local-team", "request_id": identity}
    if channel is not None:
        result["channel"] = channel
    if position is not None:
        result["position"] = {"x": position[0], "y": position[1]}
    return {**result, **extra}


class SimulatorTests(unittest.TestCase):
    def test_example_timing_is_an_independent_direct_transport_oracle(self):
        env = LocalSimulator([])
        requests = [("/enter", body("e"), 0),
                    ("/measure", body("m1", 1, (300, 400)), 105),
                    ("/measure", body("m2", 2, (300, 400)), 111),
                    ("/clear", body("c1", 3, (300, 0)), 194),
                    ("/measure", body("m3", 2, (300, 0)), 199),
                    ("/exit", body("x"), 199)]
        for path, payload, expected in requests:
            self.assertEqual(env(path, payload)["virtual_time_s"], expected)
        result = env.summary()
        self.assertEqual(result["current_channel"], 2)
        self.assertEqual(result["stats"]["walk_distance"], 900)
        self.assertEqual(result["stats"]["failures"], 1)

    def test_rejected_fields_do_not_move_time_or_consume_id(self):
        env = LocalSimulator([])
        env("/enter", body("e"))
        rejected = env("/measure", body("m", 2, (300, 400), extra=1))
        self.assertEqual(rejected, {"accepted": False, "real_timestamp_ms": rejected["real_timestamp_ms"],
                                    "virtual_time_s": 0})
        self.assertEqual(env.summary()["position"], (0, 0))
        self.assertEqual(env("/measure", body("m", 2, (300, 400)))["virtual_time_s"], 106)
        bad = body("m2", 3, (0, 0))
        bad["position"]["z"] = 1
        self.assertFalse(env("/measure", bad)["accepted"])
        self.assertEqual(env.summary()["virtual_time_s"], 106)
        del bad["position"]["z"]
        self.assertTrue(env("/measure", bad)["accepted"])

    def test_bad_identifiers_and_wrong_identity(self):
        env = LocalSimulator([])
        wrong = body("e", robot_id="other-team")
        self.assertFalse(env("/enter", wrong)["accepted"])
        self.assertTrue(env("/enter", body("e"))["accepted"])
        self.assertFalse(env("/enter", body("other"))["accepted"])
        for identity in ("", "x" * 129, "x\u200by", "x\ny", 42):
            with self.subTest(identity=identity), self.assertRaises(HTTPStatusError) as caught:
                env("/measure", body(identity, 1, (0, 0)))
            self.assertEqual(caught.exception.status_code, 400)

    def test_range_and_near_boundaries_are_inclusive(self):
        env = LocalSimulator([Source(1, (0, 0), 1000)], error_mode="zero")
        env("/enter", body("e"))
        points = [(1000, "direction"), (1000.000001, "no_signal"),
                  (5, "near"), (5.000001, "direction"), (0, "near")]
        for index, (x, expected) in enumerate(points):
            response = env("/measure", body(f"m{index}", 1, (x, 0)))
            self.assertEqual(response["measure_result"], expected)
            self.assertEqual("svd_deg" in response, expected == "direction")

    def test_direction_halfplane_boundary_and_backside_clear(self):
        env = LocalSimulator([Source(1, (0, 0), 1000, 0)], error_mode="zero")
        env("/enter", body("e"))
        for i, (point, expected) in enumerate([((100, 0), "direction"),
                                               ((0, 100), "direction"),
                                               ((0, -100), "direction"),
                                               ((-0.000001, 100), "no_signal"),
                                               ((-5, 0), "no_signal"),
                                               ((5, 0), "near")]):
            self.assertEqual(env("/measure", body(f"m{i}", 1, point))["measure_result"], expected)
        response = env("/clear", body("c", 1, (-20, 0)))
        self.assertEqual(response["clear_result"], "success")
        self.assertEqual(env("/clear", body("c2", 1, (0, 0)))["clear_result"], "no_target_in_range")
        self.assertEqual(env("/measure", body("m9", 1, (0, 0)))["measure_result"], "no_signal")

    def test_zero_distance_directional_convention_is_explicit(self):
        env = LocalSimulator([Source(1, (0, 0), 1000, 137)])
        env("/enter", body("e"))
        self.assertEqual(env("/measure", body("m", 1, (0, 0)))["measure_result"], "near")
        self.assertEqual(env("/clear", body("c", 1, (0, 0)))["clear_result"], "success")

    def test_clear_radius_strict_outside_and_channel_does_not_switch(self):
        env = LocalSimulator([Source(2, (0, 0))])
        env("/enter", body("e"))
        self.assertEqual(env("/clear", body("c1", 2, (20.000001, 0)))["clear_result"], "no_target_in_range")
        self.assertEqual(env("/clear", body("c2", 2, (20, 0)))["clear_result"], "success")
        self.assertEqual(env.summary()["current_channel"], 1)
        previous = env.summary()["virtual_time_s"]
        self.assertEqual(env("/measure", body("m", 1, (20, 0)))["virtual_time_s"], previous + 5)

    def test_robot_can_leave_disk_and_walk_is_microsecond_accounted(self):
        env = LocalSimulator([])
        env("/enter", body("e"))
        response = env("/measure", body("m", 1, (2500, 0)))
        self.assertEqual(response["virtual_time_s"], 505)
        response = env("/measure", body("m2", 1, (2501, 1)))
        expected = 505 + 5 + round(math.sqrt(2) / 5 * 1_000_000) / 1_000_000
        self.assertAlmostEqual(response["virtual_time_s"], expected, places=6)
        response = env("/measure", body("m3", 1, (2500, 0)))
        expected += 5 + round(math.sqrt(2) / 5 * 1_000_000) / 1_000_000
        self.assertAlmostEqual(response["virtual_time_s"], expected, places=6)

    def test_fixed_error_at_location_and_rounding_wrap(self):
        for mode in FixedErrorField.MODES:
            env = LocalSimulator([Source(1, (1000, 0), 1500)], seed=17, error_mode=mode)
            env("/enter", body("e"))
            first = env("/measure", body("m1", 1, (0, 0)))["svd_deg"]
            env("/measure", body("m2", 1, (20, 20)))
            last = env("/measure", body("m3", 1, (0, 0)))["svd_deg"]
            self.assertEqual(first, last)
            wrapped_error = (first + 180) % 360 - 180
            self.assertLessEqual(abs(wrapped_error), 1.005000001)
        env = LocalSimulator([Source(1, (1000, -0.03), 1500)], error_mode="zero")
        env("/enter", body("e"))
        self.assertEqual(env("/measure", body("m", 1, (0, 0)))["svd_deg"], 0.0)

    def test_noise_modes_repeat_are_bounded_and_correlated(self):
        for mode in FixedErrorField.MODES:
            field = FixedErrorField(4, mode)
            clone = FixedErrorField(4, mode)
            for i in range(100):
                position = (i * 31.0 - 1500, i * -17.0 + 1000)
                result = field(3, position)
                self.assertGreaterEqual(result, -1)
                self.assertLessEqual(result, 1)
                self.assertEqual(result, clone(3, position))
            self.assertEqual(field(1, (-0.0, 0)), field(1, (0, -0.0)))
        field = FixedErrorField(4, "correlated", 150)
        self.assertLess(abs(field(1, (100, 100)) - field(1, (100.001, 100))), 0.00001)
        self.assertEqual({FixedErrorField(4, "extreme")(1, (i * 150, 0)) for i in range(40)}, {-1, 1})

    def test_same_id_same_content_is_immutable_replay_changed_content_conflicts(self):
        env = LocalSimulator([])
        env("/enter", body("e"))
        request = body("m", 1, (300, 400))
        first = env("/measure", request)
        cached = copy.deepcopy(first)
        first["virtual_time_s"] = -999
        self.assertEqual(env("/measure", copy.deepcopy(request)), cached)
        self.assertEqual(env.summary()["virtual_time_s"], 105)
        with self.assertRaises(ConflictError):
            env("/clear", request)
        changed = copy.deepcopy(request)
        changed["position"]["x"] = 301
        with self.assertRaises(ConflictError):
            env("/measure", changed)
        self.assertEqual(env.summary()["accepted_actions"], 2)

    def test_execution_then_response_loss_has_cached_success(self):
        env = LocalSimulator([Source(1, (100, 0))], response_drop_indices={2})
        env("/enter", body("e"))
        request = body("c", 1, (100, 0))
        with self.assertRaises(TimeoutError):
            env("/clear", request)
        replay = env("/clear", request)
        self.assertEqual(replay["clear_result"], "success")
        self.assertEqual(replay["virtual_time_s"], 25)
        self.assertEqual(env.summary()["cleared_count"], 1)

    def test_closed_session_can_make_exit_recovery_impossible(self):
        env = LocalSimulator([], response_drop_indices={2})
        client = RobotClient(transport=env, max_retries=1, retry_delay=0)
        client.enter()
        with self.assertRaises(TransportError):
            client.exit()
        self.assertFalse(client.exited)
        self.assertTrue(env.summary()["closed"])
        self.assertIsNotNone(client.pending_request)
        with self.assertRaises(ConnectionClosedError):
            env("/enter", body("another"))

    def test_optional_post_exit_cached_replay_keeps_same_response(self):
        env = LocalSimulator([], replay_cached_after_close=True, response_drop_indices={2})
        client = RobotClient(transport=env, retry_delay=0)
        client.enter()
        client.exit()
        self.assertTrue(client.exited)
        self.assertEqual(env.summary()["accepted_actions"], 2)

    def test_timeout_limits_close_without_posthoc_exit_reason_endpoint(self):
        clock = Clock()
        env = LocalSimulator([], clock=clock, wall_clock=clock.wall, window_duration_s=100)
        clock.now = 80
        self.assertEqual(env("/enter", body("e"))["remaining_real_duration_s"], 20)
        clock.now = 100
        with self.assertRaises(ConnectionClosedError):
            env("/exit", body("x"))
        self.assertEqual(env.summary()["exit_reason"], "window_timeout")
        env = LocalSimulator([], max_virtual_duration_s=4)
        env("/enter", body("e"))
        self.assertEqual(env("/measure", body("m", 1, (0, 0)))["virtual_time_s"], 5)
        with self.assertRaises(ConnectionClosedError):
            env("/exit", body("x"))

    def test_pending_before_enter_is_rejected_not_an_implicit_enter(self):
        env = LocalSimulator([])
        self.assertFalse(env("/measure", body("m", 1, (0, 0)))["accepted"])
        self.assertTrue(env("/enter", body("m"))["accepted"])

    def test_concurrent_simulator_actions_are_409(self):
        arrived, release = threading.Event(), threading.Event()
        def hook(path, payload, response):
            if path == "/measure":
                arrived.set()
                if not release.wait(3):
                    raise RuntimeError("test release timed out")
            return False
        env = LocalSimulator([], drop_response_after_execute=hook)
        env("/enter", body("e"))
        errors = []
        def execute():
            try:
                env("/measure", body("m", 1, (0, 0)))
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=execute)
        worker.start()
        try:
            self.assertTrue(arrived.wait(2))
            with self.assertRaises(ConflictError):
                env("/clear", body("c", 1, (0, 0)))
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(errors)

    def test_malformed_wire_requests_do_not_execute(self):
        env = LocalSimulator([])
        duplicate = '{"arena_id":"default","robot_id":"local-team","request_id":"e","request_id":"x"}'
        cases = [("/enter", duplicate, {}, 400),
                 ("/enter", b"\xef\xbb\xbf{}", {}, 400),
                 ("/enter", b"{", {}, 400),
                 ("/enter", b"{}", {"method": "GET"}, 405),
                 ("/enter/", b"{}", {}, 404),
                 ("/enter", b"{}", {"content_type": "text/plain"}, 415),
                 ("/enter", b"{}", {"content_encoding": "gzip"}, 415),
                 ("/enter", b" " * 65537, {}, 413),
                 ("/enter", json.dumps({**body("e"), "x": float("nan")}), {}, 400)]
        for path, data, options, status in cases:
            with self.subTest(status=status, path=path), self.assertRaises(HTTPStatusError) as caught:
                env.handle_json(path, data, **options)
            self.assertEqual(caught.exception.status_code, status)
        self.assertEqual(env.summary()["accepted_actions"], 0)
        self.assertTrue(env.handle_json("/enter", json.dumps(body("e")))["accepted"])

    def test_coordinate_channel_types_and_record_limit(self):
        env = LocalSimulator([], max_idempotency_records=2)
        env("/enter", body("e"))
        for point in ((float("nan"), 0), (float("inf"), 0), (2_000_001, 0), (True, 0)):
            with self.subTest(point=point), self.assertRaises(HTTPStatusError) as caught:
                env("/measure", body("bad", 1, point))
            self.assertEqual(caught.exception.status_code, 400)
        for channel in (True, 0, 21, 1.5, "1"):
            with self.subTest(channel=channel), self.assertRaises(HTTPStatusError) as caught:
                env("/measure", body("bad", channel, (0, 0)))
            self.assertEqual(caught.exception.status_code, 400)
        self.assertTrue(env("/measure", body("good", 1.0, (0, 0)))["accepted"])
        with self.assertRaises(HTTPStatusError) as caught:
            env("/measure", body("next", 1, (0, 0)))
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(env("/measure", body("good", 1.0, (0, 0)))["virtual_time_s"], 5)

    def test_synthetic_case_generation_has_valid_unique_channels(self):
        first = generate_sources(5, directional_fraction=1.0)
        self.assertEqual(first, generate_sources(5, directional_fraction=1.0))
        self.assertTrue(10 <= len(first) <= 16)
        self.assertEqual(len(first), len({source.channel for source in first}))
        self.assertTrue(all(math.hypot(*s.position) <= 1800 and 1000 <= s.radius <= 1500
                            and s.direction_deg is not None for s in first))
        with self.assertRaises(ValueError):
            LocalSimulator([Source(1, (0, 0)), Source(1, (1, 1))])
        with self.assertRaises(ValueError):
            LocalSimulator([Source(1, (0, 0))], enforce_case_size=True)

    def test_protocol_responses_and_public_trace_do_not_leak_case_truth(self):
        env = LocalSimulator([Source(7, (123, 456), 1111, 70)])
        client = RobotClient(transport=env)
        client.enter()
        client.measure(7, (0, 0))
        serialized = json.dumps(env.public_trace())
        for forbidden in ("source_count", "radius", "direction_deg", "sources", "source_position"):
            self.assertNotIn(forbidden, serialized)
        self.assertFalse(hasattr(client, "sources"))
        trace = env.public_trace()
        trace[0]["response"]["virtual_time_s"] = 99
        self.assertEqual(env.public_trace()[0]["response"]["virtual_time_s"], 0)


if __name__ == "__main__":
    unittest.main()
