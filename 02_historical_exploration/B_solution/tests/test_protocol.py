"""Tests use synthetic direct transports or an ephemeral loopback HTTP server only."""

import copy
import json
import math
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bsolver.protocol import (ClientStateError, ConcurrentRequestError, ConflictError,
                              DeadlineExceeded, HTTPStatusError, HTTPTransport,
                              InvalidResponseError, JournalError, PendingActionError,
                              RequestRejected, RobotClient, TransportError)
from bsolver.simulator import LocalSimulator, Source


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def wall(self):
        return 1_700_000_000.0 + self.now


class ProtocolTests(unittest.TestCase):
    def fixture(self, sources=(), **env_options):
        clock = Clock()
        env = LocalSimulator(sources, clock=clock, wall_clock=clock.wall, **env_options)
        client = RobotClient(transport=env, clock=clock, wall_clock=clock.wall,
                             sleeper=clock.sleep, retry_delay=0, request_prefix="test")
        return clock, env, client

    def test_published_timing_example(self):
        _, env, client = self.fixture()
        self.assertEqual(client.enter()["virtual_time_s"], 0)
        self.assertEqual(client.measure(1, (300, 400))["virtual_time_s"], 105)
        self.assertEqual(client.measure(2, (300, 400))["virtual_time_s"], 111)
        self.assertEqual(client.clear(3, (300, 0))["virtual_time_s"], 194)
        self.assertEqual(client.current_channel, 2)
        self.assertEqual(client.position, (300.0, 0.0))
        self.assertEqual(client.measure(2, (300, 0))["virtual_time_s"], 199)
        self.assertEqual(client.exit()["virtual_time_s"], 199)
        self.assertEqual(client.stats["walk_distance"], 900)
        self.assertEqual(client.stats["switches"], 1)
        self.assertEqual(client.stats["measures"], 3)
        self.assertEqual(client.stats["clear_attempts"], 1)
        self.assertEqual(env.summary()["virtual_time_s"], client.virtual_time)

    def test_response_lost_after_measure_reuses_id_and_counts_once(self):
        _, env, client = self.fixture([Source(1, (100, 0))])
        client.enter()
        env.drop_next_response()
        response = client.measure(1, (50, 0))
        self.assertEqual(response["measure_result"], "direction")
        attempts = [h for h in client.history if h["path"] == "/measure"]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["request"], attempts[1]["request"])
        self.assertEqual(client.virtual_time, 15)
        self.assertEqual(client.stats["measures"], 1)
        self.assertEqual(env.summary()["stats"]["measures"], 1)
        self.assertEqual(client.stats["walk_distance"], 50)

    def test_response_lost_after_clear_does_not_become_false_failure(self):
        _, env, client = self.fixture([Source(2, (100, 0))])
        client.enter()
        env.drop_next_response()
        response = client.clear(2, (100, 0))
        self.assertEqual(response["clear_result"], "success")
        self.assertEqual(client.cleared_count, 1)
        self.assertEqual(client.stats["successes"], 1)
        self.assertEqual(client.stats["clear_attempts"], 1)
        self.assertEqual(client.current_channel, 1)
        self.assertEqual(client.virtual_time, 25)
        self.assertEqual(env.summary()["cleared_count"], 1)

    def test_first_enter_start_controls_deadline_even_after_cached_retry(self):
        clock = Clock()
        env = LocalSimulator([], clock=clock, wall_clock=clock.wall)
        first = True
        def transport(path, body):
            nonlocal first
            result = env(path, body)
            clock.sleep(7)
            if first:
                first = False
                raise TimeoutError("Lost accepted /enter")
            return result
        client = RobotClient(transport=transport, clock=clock, wall_clock=clock.wall,
                             sleeper=clock.sleep, retry_delay=0)
        client.enter()
        self.assertEqual(clock.now, 14)
        self.assertEqual(client.deadline, 1200)
        self.assertEqual(client.remaining_real_time(), 1186)
        self.assertEqual(env.summary()["accepted_actions"], 1)

    def test_retries_exhausted_block_new_actions_until_original_retried(self):
        clock = Clock()
        env = LocalSimulator([], clock=clock, wall_clock=clock.wall)
        lose = False
        calls = []
        def transport(path, body):
            calls.append((path, copy.deepcopy(body)))
            result = env(path, body)
            if lose:
                raise ConnectionResetError("dropped")
            return result
        client = RobotClient(transport=transport, clock=clock, wall_clock=clock.wall,
                             max_retries=0, retry_delay=0)
        client.enter()
        lose = True
        with self.assertRaises(TransportError):
            client.measure(1, (300, 400))
        self.assertEqual(client.position, (0.0, 0.0))
        self.assertEqual(client.virtual_time, 0)
        pending = client.pending_request
        with self.assertRaises(PendingActionError):
            client.clear(1, (300, 400))
        pending["body"]["position"]["x"] = -999
        self.assertEqual(client.pending_request["body"]["position"]["x"], 300)
        lose = False
        client.retry_pending()
        self.assertEqual(calls[-1], calls[-2])
        self.assertEqual(client.virtual_time, 105)
        self.assertEqual(env.summary()["stats"]["measures"], 1)
        client.measure(2, (300, 400))
        self.assertNotEqual(calls[-1][1]["request_id"], calls[-2][1]["request_id"])

    def test_rejected_response_keeps_state_and_does_not_reset_clock(self):
        clock = Clock()
        env = LocalSimulator([], clock=clock, wall_clock=clock.wall)
        reject = False
        def transport(path, body):
            if reject:
                return {"accepted": False, "real_timestamp_ms": 1, "virtual_time_s": 0}
            return env(path, body)
        client = RobotClient(transport=transport, clock=clock, wall_clock=clock.wall)
        client.enter()
        client.measure(2, (300, 400))
        before = client.state_snapshot()
        reject = True
        with self.assertRaises(RequestRejected):
            client.clear(3, (100, 100))
        self.assertEqual(client.state_snapshot(), before)
        self.assertIsNone(client.pending_request)
        self.assertEqual(client.stats["clear_attempts"], 0)

    def test_http_409_fails_fast_and_keeps_uncertain_action(self):
        _, env, client = self.fixture()
        client.enter()
        def conflict(path, body):
            raise ConflictError({"accepted": False, "real_timestamp_ms": 1, "virtual_time_s": 0})
        client._RobotClient__transport = conflict
        with self.assertRaises(ConflictError):
            client.measure(1, (1, 1))
        self.assertEqual(client.virtual_time, 0)
        self.assertIsNotNone(client.pending_request)
        self.assertEqual(client.pending_request["attempts"], 1)

    def test_real_deadline_and_remaining_zero_do_not_send_more_actions(self):
        clock, env, client = self.fixture(window_duration_s=100)
        clock.sleep(80)
        client.enter()
        self.assertEqual(client.deadline, 100)
        clock.sleep(20)
        with self.assertRaises(DeadlineExceeded):
            client.measure(1, (0, 0))
        self.assertEqual(env.summary()["accepted_actions"], 1)
        _, env2, client2 = self.fixture(window_duration_s=0.5)
        self.assertEqual(client2.enter()["remaining_real_duration_s"], 0)
        with self.assertRaises(DeadlineExceeded):
            client2.measure(1, (0, 0))

    def test_virtual_limit_completes_registered_action_then_blocks(self):
        _, env, client = self.fixture(max_virtual_duration_s=4)
        client.enter()
        client.measure(1, (0, 0))
        self.assertEqual(client.virtual_time, 5)
        self.assertTrue(env.summary()["closed"])
        with self.assertRaises(DeadlineExceeded):
            client.exit()

    def test_incomplete_direction_response_is_not_committed(self):
        _, env, client = self.fixture()
        client.enter()
        client._RobotClient__transport = lambda path, body: {
            "accepted": True, "real_timestamp_ms": 1, "virtual_time_s": 5,
            "measure_result": "direction"}
        with self.assertRaises(InvalidResponseError):
            client.measure(1, (0, 0))
        self.assertEqual(client.virtual_time, 0)
        self.assertIsNotNone(client.pending_request)

    def test_argument_validation_prevents_invalid_network_requests(self):
        _, env, client = self.fixture()
        client.enter()
        for position in ((float("nan"), 0), (float("inf"), 0), (2_000_001, 0), (True, 0)):
            with self.subTest(position=position), self.assertRaises(ValueError):
                client.measure(1, position)
        for channel in (True, 0, 21, 1.5, "1"):
            with self.subTest(channel=channel), self.assertRaises(ValueError):
                client.measure(channel, (0, 0))
        self.assertEqual(env.summary()["accepted_actions"], 1)

    def test_concurrent_client_calls_do_not_send_concurrent_requests(self):
        clock = Clock()
        env = LocalSimulator([], clock=clock, wall_clock=clock.wall)
        arrived, release = threading.Event(), threading.Event()
        def transport(path, body):
            if path == "/measure":
                arrived.set()
                if not release.wait(3):
                    raise TimeoutError("test failed to release transport")
            return env(path, body)
        client = RobotClient(transport=transport, clock=clock, wall_clock=clock.wall)
        client.enter()
        errors = []
        def measure():
            try:
                client.measure(1, (0, 0))
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=measure)
        worker.start()
        try:
            self.assertTrue(arrived.wait(2))
            with self.assertRaises(ConcurrentRequestError):
                client.clear(2, (0, 0))
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(errors)
        self.assertEqual(env.summary()["stats"]["clear_attempts"], 0)

    def test_jsonl_contains_every_attempt_and_confirmed_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "client.jsonl"
            env = LocalSimulator([Source(1, (100, 0))])
            client = RobotClient(transport=env, log_path=path, retry_delay=0)
            client.enter()
            env.drop_next_response()
            client.clear(1, (100, 0))
            client.exit()
            client.close_log()
            lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(lines), 4)
            self.assertEqual(lines[1]["outcome"], "transport_error")
            self.assertEqual(lines[2]["state_after"]["cleared_count"], 1)
            self.assertEqual(lines[-1]["state_after"]["exited"], True)
            self.assertEqual(lines[1]["request"], lines[2]["request"])

    def test_journal_failure_never_reexecutes_confirmed_action(self):
        _, env, client = self.fixture()
        client.enter()
        class BrokenLog:
            def write(self, text):
                raise OSError("disk full")
        client._journal = BrokenLog()
        with self.assertRaises(JournalError):
            client.measure(1, (0, 0))
        self.assertEqual(env.summary()["stats"]["measures"], 1)
        self.assertEqual(client.stats["measures"], 1)
        self.assertEqual(client.virtual_time, 5)
        self.assertIsNone(client.pending_request)

    def test_exit_has_no_implicit_retry_as_new_action_or_context_side_effect(self):
        _, env, client = self.fixture()
        with client:
            client.enter()
        self.assertFalse(env.summary()["closed"])
        client.exit()
        with self.assertRaises(ClientStateError):
            client.exit()
        self.assertEqual(env.summary()["accepted_actions"], 2)


class HTTPTransportTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.reply = (200, {"accepted": True, "real_timestamp_ms": 1, "virtual_time_s": 0})
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                outer.requests.append((self.path, self.headers["Content-Type"], raw))
                status, body = outer.reply
                data = json.dumps(body).encode("utf-8") if not isinstance(body, bytes) else body
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, format, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.worker.start()
        self.transport = HTTPTransport(f"http://127.0.0.1:{self.server.server_port}")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(2)

    def test_http_sends_exact_json_protocol(self):
        payload = {"arena_id": "default", "robot_id": "队号", "request_id": "r1"}
        self.assertTrue(self.transport("/enter", payload)["accepted"])
        path, content_type, raw = self.requests[0]
        self.assertEqual(path, "/enter")
        self.assertEqual(content_type, "application/json; charset=utf-8")
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(json.loads(raw.decode("utf-8")), payload)

    def test_http_conflict_and_bad_response_are_distinct(self):
        self.reply = (409, {"accepted": False, "real_timestamp_ms": 1, "virtual_time_s": 0})
        with self.assertRaises(ConflictError) as caught:
            self.transport("/enter", {})
        self.assertEqual(caught.exception.status_code, 409)
        self.reply = (400, {"accepted": False, "real_timestamp_ms": 1, "virtual_time_s": 0})
        with self.assertRaises(HTTPStatusError) as caught:
            self.transport("/enter", {})
        self.assertEqual(caught.exception.status_code, 400)
        self.reply = (200, b"not json")
        with self.assertRaises(InvalidResponseError):
            self.transport("/enter", {})


if __name__ == "__main__":
    unittest.main()
