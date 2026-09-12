"""Independent local test environment implementing the published B action rules.

This is NOT the official simulator or a reconstruction of its hidden generator.
Position/channel error fields are fixed during a case.  Truth is kept here; a
strategy receives only RobotClient, whose transport returns public response fields.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .protocol import (ConnectionClosedError, ConflictError, HTTPStatusError,
                       _finite_number, normalize_channel, normalize_position,
                       validate_identifier)


@dataclass(frozen=True)
class Source:
    channel: int
    position: tuple[float, float]
    radius: float = 1000.0
    direction_deg: float | None = None

    def __post_init__(self):
        object.__setattr__(self, "channel", normalize_channel(self.channel))
        position = normalize_position(self.position)
        if math.hypot(*position) > 1800.0 + 1e-9:
            raise ValueError("Source must lie in the closed target disk of radius 1800")
        object.__setattr__(self, "position", position)
        if not _finite_number(self.radius) or not 1000 <= self.radius <= 1500:
            raise ValueError("Source radius must lie in [1000, 1500]")
        object.__setattr__(self, "radius", float(self.radius))
        if self.direction_deg is not None:
            if not _finite_number(self.direction_deg):
                raise ValueError("direction_deg must be finite or None")
            object.__setattr__(self, "direction_deg", float(self.direction_deg) % 360.0)


class FixedErrorField:
    """Bounded synthetic fields; none claims to match official error statistics.

    deterministic: hashed exact location/channel, independently varying nearby.
    correlated: smooth field on the configured spatial scale.
    extreme: region-wise constant -1/+1 degree with abrupt sign boundaries.
    zero, positive, negative: exact or constant worst-sign fixtures.
    """

    MODES = {"deterministic", "correlated", "extreme", "zero", "positive", "negative"}

    def __init__(self, seed: int = 0, mode: str = "deterministic", correlation_length: float = 150.0):
        if mode not in self.MODES:
            raise ValueError(f"Unknown error_mode {mode!r}")
        if not _finite_number(correlation_length) or correlation_length <= 0:
            raise ValueError("correlation_length must be positive")
        self.seed, self.mode, self.scale = seed, mode, float(correlation_length)

    def _unit(self, token: str) -> float:
        digest = hashlib.blake2b(f"{self.seed}|{token}".encode("ascii"), digest_size=8).digest()
        return int.from_bytes(digest, "big") / (2**64 - 1)

    def __call__(self, channel: int, position: tuple[float, float]) -> float:
        x, y = (0.0 if a == 0 else float(a) for a in position)
        if self.mode == "zero":
            return 0.0
        if self.mode == "positive":
            return 1.0
        if self.mode == "negative":
            return -1.0
        if self.mode == "deterministic":
            return 2.0 * self._unit(f"{channel}|{x.hex()}|{y.hex()}") - 1.0
        phase = 2 * math.pi * self._unit(f"phase|{channel}")
        if self.mode == "correlated":
            value = (0.45 * math.sin(x / self.scale + phase)
                     + 0.35 * math.cos(y / self.scale - 0.7 * phase)
                     + 0.20 * math.sin((x + y) / (2 * self.scale) + 1.3 * phase))
            return min(1.0, max(-1.0, value))
        cell_x, cell_y = math.floor(x / self.scale), math.floor(y / self.scale)
        return 1.0 if self._unit(f"cell|{channel}|{cell_x}|{cell_y}") >= 0.5 else -1.0


def generate_sources(seed: int, count: int | None = None, directional_fraction: float = 0.0,
                     *, minimum_radius: bool = False) -> list[Source]:
    """Reproducible synthetic cases, explicitly not the official case distribution."""
    if not _finite_number(directional_fraction) or not 0 <= directional_fraction <= 1:
        raise ValueError("directional_fraction must lie in [0, 1]")
    rng = random.Random(seed)
    count = rng.randint(10, 16) if count is None else count
    if isinstance(count, bool) or not isinstance(count, int) or not 10 <= count <= 16:
        raise ValueError("Generated contest-sized cases must contain 10..16 sources")
    channels = rng.sample(range(1, 21), count)
    result = []
    for channel in channels:
        radius_from_origin = 1800.0 * math.sqrt(rng.random())
        angle = rng.uniform(0, 2 * math.pi)
        position = (radius_from_origin * math.cos(angle), radius_from_origin * math.sin(angle))
        receive_radius = 1000.0 if minimum_radius else rng.uniform(1000.0, 1500.0)
        direction = rng.uniform(0.0, 360.0) if rng.random() < directional_fraction else None
        result.append(Source(channel, position, receive_radius, direction))
    return result


class LocalSimulator:
    """Direct transport with geometry, microsecond accounting, deduplication and faults.

    ``sources`` may contain 0..20 sources for unit tests.  Set enforce_case_size=True
    for a contest-sized 10..16 case.  The evaluation harness may call summary();
    no summary or source metadata is returned by the four protocol endpoints.

    By default a closed endpoint cannot replay cached responses, matching the
    actual possibility of connection failure after exit/timeout.  Set
    replay_cached_after_close=True only to test the alternative where a cached
    completed /exit response remains reachable.  Clients must handle both.
    """

    def __init__(self, sources: Iterable[Source], *, robot_id: str = "local-team",
                 seed: int = 0, error_mode: str = "deterministic",
                 correlation_length: float = 150.0,
                 error_field: Callable[[int, tuple[float, float]], float] | None = None,
                 max_real_duration_s: float = 1200.0, window_duration_s: float = 1500.0,
                 max_virtual_duration_s: float = 360000.0,
                 clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], float] = time.time,
                 enforce_case_size: bool = False, replay_cached_after_close: bool = False,
                 max_idempotency_records: int = 100000,
                 response_drop_ids: Iterable[str] = (),
                 response_drop_indices: Iterable[int] = (),
                 drop_response_after_execute: Callable[[str, dict, dict], bool] | None = None):
        self.robot_id = validate_identifier(robot_id, 64, "robot_id")
        items = list(sources)
        if not all(isinstance(source, Source) for source in items):
            raise ValueError("sources must be Source objects")
        if len(items) > 20 or (enforce_case_size and not 10 <= len(items) <= 16):
            raise ValueError("Invalid number of sources")
        if len({s.channel for s in items}) != len(items):
            raise ValueError("Each source must have a distinct channel")
        for label, value in (("max_real_duration_s", max_real_duration_s),
                             ("window_duration_s", window_duration_s),
                             ("max_virtual_duration_s", max_virtual_duration_s)):
            if not _finite_number(value) or value < 0:
                raise ValueError(f"{label} must be nonnegative and finite")
        if isinstance(max_idempotency_records, bool) or not isinstance(max_idempotency_records, int) or max_idempotency_records < 1:
            raise ValueError("max_idempotency_records must be a positive integer")
        self.__sources = {s.channel: s for s in items}
        self.__cleared: set[int] = set()
        self.__field = error_field if error_field is not None else FixedErrorField(seed, error_mode, correlation_length)
        self._clock, self._wall_clock = clock, wall_clock
        self._max_real = float(max_real_duration_s)
        self._max_virtual_us = int(round(max_virtual_duration_s * 1_000_000))
        self._window_deadline = clock() + float(window_duration_s)
        self._real_deadline: float | None = None
        self._position = (0.0, 0.0)
        self._channel = 1
        self._virtual_us = 0
        self._entered = False
        self._closed = False
        self._reason: str | None = None
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[str, dict]] = {}
        self._max_records = max_idempotency_records
        self._replay_after_close = replay_cached_after_close
        self._drop_ids = set(response_drop_ids)
        self._drop_indices = set(response_drop_indices)
        self._drop_callback = drop_response_after_execute
        self._drop_next = 0
        self._accepted_count = 0
        self._trace: list[dict] = []
        self._stats = {"walk_distance": 0.0, "movement_time": 0.0,
                       "switches": 0, "measures": 0, "clear_attempts": 0,
                       "successes": 0, "failures": 0,
                       "switch_time": 0.0, "measure_time": 0.0, "clear_time": 0.0}

    def drop_next_response(self, count: int = 1) -> None:
        """Drop responses AFTER new accepted actions, not before execution or on retries."""
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("count must be a nonnegative integer")
        self._drop_next += count

    def close(self, reason: str = "manual_abort") -> None:
        """Evaluation-harness hook; no public endpoint can manually reset a case."""
        self._closed, self._reason = True, reason

    def __call__(self, path: str, payload: dict) -> dict:
        if not self._lock.acquire(blocking=False):
            raise ConflictError(self._rejection(), "Concurrent new actions are forbidden")
        try:
            self._update_expiration()
            if self._closed and not self._replay_after_close:
                raise ConnectionClosedError(f"Local endpoint is closed ({self._reason})")
            if path not in {"/enter", "/measure", "/clear", "/exit"}:
                raise HTTPStatusError(404, self._rejection())
            validation = self._validate_payload(path, payload)
            if validation is False:
                return self._rejection()
            identity = payload["request_id"]
            fingerprint = path + "\n" + json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                                     allow_nan=False, separators=(",", ":"))
            self._update_expiration()
            if self._closed and not self._replay_after_close:
                raise ConnectionClosedError(f"Local endpoint is closed ({self._reason})")
            if identity in self._cache:
                old_fingerprint, old_response = self._cache[identity]
                if old_fingerprint != fingerprint:
                    raise ConflictError(self._rejection())
                return copy.deepcopy(old_response)
            if self._closed:
                raise ConnectionClosedError(f"Local endpoint is closed ({self._reason})")
            if len(self._cache) >= self._max_records:
                raise HTTPStatusError(429, self._rejection(), "Local idempotency record limit reached")
            if path == "/enter":
                if self._entered:
                    return self._rejection()
                now = self._clock()
                self._entered = True
                self._real_deadline = min(self._window_deadline, now + self._max_real)
                remaining = max(0, math.floor(self._real_deadline - now))
                response = self._accepted(max_virtual_duration_s=self._max_virtual_us / 1_000_000,
                                          max_real_duration_s=self._max_real,
                                          remaining_real_duration_s=remaining)
            elif not self._entered:
                return self._rejection()
            elif path == "/exit":
                response = self._accepted(exit_reason="user_exit")
                self._closed, self._reason = True, "user_exit"
            else:
                response = self._execute_action(path, payload)
            self._cache[identity] = (fingerprint, copy.deepcopy(response))
            self._accepted_count += 1
            self._trace.append({"path": path, "request": copy.deepcopy(payload),
                                "response": copy.deepcopy(response)})
            # An action registered before a cutoff is allowed to complete.
            self._update_expiration()
            drop = identity in self._drop_ids or self._accepted_count in self._drop_indices
            if self._drop_next:
                self._drop_next -= 1
                drop = True
            if self._drop_callback is not None:
                drop = bool(self._drop_callback(path, copy.deepcopy(payload), copy.deepcopy(response))) or drop
            if drop:
                self._drop_ids.discard(identity)
                self._drop_indices.discard(self._accepted_count)
                raise TimeoutError("Injected response loss AFTER action execution and caching")
            return copy.deepcopy(response)
        finally:
            self._lock.release()

    def _rejection(self) -> dict:
        return {"accepted": False, "real_timestamp_ms": int(self._wall_clock() * 1000),
                "virtual_time_s": 0}

    def _accepted(self, **extra) -> dict:
        return {"accepted": True, "real_timestamp_ms": int(self._wall_clock() * 1000),
                "virtual_time_s": self._virtual_us / 1_000_000, **extra}

    def _update_expiration(self) -> None:
        if self._closed:
            return
        if self._clock() >= self._window_deadline:
            self._closed, self._reason = True, "window_timeout"
        elif self._real_deadline is not None and self._clock() >= self._real_deadline:
            self._closed, self._reason = True, "real_timeout"
        elif self._entered and self._virtual_us >= self._max_virtual_us:
            self._closed, self._reason = True, "virtual_timeout"

    def _validate_payload(self, path: str, payload: Any) -> bool:
        required = {"arena_id", "robot_id", "request_id"}
        if path in {"/measure", "/clear"}:
            required |= {"position", "channel"}
        if not isinstance(payload, dict) or not required <= set(payload):
            raise HTTPStatusError(400, self._rejection(), "Missing field or non-object request")
        if not isinstance(payload["arena_id"], str):
            raise HTTPStatusError(400, self._rejection(), "arena_id must be a string")
        try:
            validate_identifier(payload["robot_id"], 64, "robot_id")
            validate_identifier(payload["request_id"], 128, "request_id")
            if path in {"/measure", "/clear"}:
                position = payload["position"]
                if not isinstance(position, dict) or not {"x", "y"} <= set(position):
                    raise ValueError("Missing position fields")
                normalize_position({"x": position["x"], "y": position["y"]})
                normalize_channel(payload["channel"])
        except (ValueError, TypeError, OverflowError) as exc:
            raise HTTPStatusError(400, self._rejection(), str(exc)) from exc
        if set(payload) != required:
            return False
        if path in {"/measure", "/clear"} and set(payload["position"]) != {"x", "y"}:
            return False
        if payload["arena_id"] != "default" or payload["robot_id"] != self.robot_id:
            return False
        return True

    def _execute_action(self, path: str, payload: dict) -> dict:
        position = normalize_position(payload["position"])
        channel = normalize_channel(payload["channel"])
        distance = math.dist(self._position, position)
        move_us = int(round(distance / 5.0 * 1_000_000))
        # Compute detection and validate a custom error field BEFORE committing movement.
        if path == "/measure":
            result = self._measurement(channel, position)
            switched = int(channel != self._channel)
            increment_us = move_us + (5 + switched) * 1_000_000
        else:
            source = self.__sources.get(channel)
            success = (source is not None and channel not in self.__cleared
                       and math.dist(position, source.position) <= 20.0)
            increment_us = move_us + (5 if success else 3) * 1_000_000
            result = {"clear_result": "success" if success else "no_target_in_range"}
        self._position = position
        self._virtual_us += increment_us
        self._stats["walk_distance"] += distance
        self._stats["movement_time"] += move_us / 1_000_000
        if path == "/measure":
            self._channel = channel
            self._stats["switches"] += switched
            self._stats["switch_time"] += switched
            self._stats["measures"] += 1
            self._stats["measure_time"] += 5.0
        else:
            self._stats["clear_attempts"] += 1
            self._stats["clear_time"] += 5.0 if success else 3.0
            if success:
                self.__cleared.add(channel)
                self._stats["successes"] += 1
            else:
                self._stats["failures"] += 1
        return self._accepted(**result)

    def _measurement(self, channel: int, position: tuple[float, float]) -> dict:
        source = self.__sources.get(channel)
        if source is None or channel in self.__cleared:
            return {"measure_result": "no_signal"}
        dx, dy = position[0] - source.position[0], position[1] - source.position[1]
        distance = math.hypot(dx, dy)
        if distance > source.radius:
            return {"measure_result": "no_signal"}
        if source.direction_deg is not None and distance > 0:
            angle = math.radians(source.direction_deg)
            projection = math.cos(angle) * dx + math.sin(angle) * dy
            # Only a floating-point boundary tolerance, less than 2e-12 m at 1500 m.
            tolerance = 8 * math.ulp(max(1.0, abs(dx), abs(dy)))
            if projection < -tolerance:
                return {"measure_result": "no_signal"}
        if distance <= 5.0:
            # At exact coincidence the source is in its own closed half-plane;
            # this explicit local convention is not a claim about hidden code.
            return {"measure_result": "near"}
        error = self.__field(channel, position)
        if not _finite_number(error) or not -1.0 <= error <= 1.0:
            raise ValueError("Local error_field must return a finite value in [-1, 1]")
        bearing = math.degrees(math.atan2(-dy, -dx)) % 360.0
        svd = round((bearing + error) % 360.0, 2) % 360.0
        return {"measure_result": "direction", "svd_deg": svd}

    def handle_json(self, path: str, raw: bytes | str, *, method: str = "POST",
                    content_type: str = "application/json", content_encoding: str = "identity") -> dict:
        """Optional wire-format test entry; raises HTTPStatusError for malformed JSON.

        Direct dict calls cannot express duplicate JSON keys.  This entry checks
        bytes, duplicate keys, nesting, size, media type and method as documented.
        It starts no listening server and performs no network action.
        """
        if path not in {"/enter", "/measure", "/clear", "/exit"}:
            raise HTTPStatusError(404, self._rejection())
        if method != "POST":
            raise HTTPStatusError(405, self._rejection())
        pieces = [p.strip().lower() for p in content_type.split(";")]
        if (pieces[0] != "application/json" or len(pieces) > 2
                or (len(pieces) == 2 and pieces[1] != "charset=utf-8")
                or content_encoding.lower() != "identity"):
            raise HTTPStatusError(415, self._rejection())
        try:
            encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
            if not isinstance(encoded, bytes):
                raise ValueError("Request must be bytes or string")
            if len(encoded) > 65536:
                raise HTTPStatusError(413, self._rejection())
            if encoded.startswith(b"\xef\xbb\xbf"):
                raise ValueError("UTF-8 BOM is forbidden")
            def pairs_hook(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("Duplicate JSON key")
                    result[key] = value
                return result
            def reject_constant(value):
                raise ValueError(f"Non-finite JSON value {value}")
            body = json.loads(encoded.decode("utf-8"), object_pairs_hook=pairs_hook,
                              parse_constant=reject_constant)
            def depth(value):
                if isinstance(value, dict):
                    return 1 + max((depth(v) for v in value.values()), default=0)
                if isinstance(value, list):
                    return 1 + max((depth(v) for v in value), default=0)
                return 0
            if depth(body) > 16:
                raise ValueError("JSON nesting exceeds 16")
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise HTTPStatusError(400, self._rejection(), str(exc)) from exc
        return self(path, body)

    def summary(self) -> dict:
        """Evaluation-only scores.  Never give this method or output to the policy."""
        self._update_expiration()
        total, cleared = len(self.__sources), len(self.__cleared)
        return {"source_count": total, "cleared_count": cleared,
                "cleared_channels": sorted(self.__cleared),
                "clearance_ratio": cleared / total if total else 1.0,
                "all_cleared": cleared == total,
                "virtual_time_s": self._virtual_us / 1_000_000,
                "average_clear_time_s": self._virtual_us / 1_000_000 / cleared if cleared else None,
                "position": self._position, "current_channel": self._channel,
                "accepted_actions": self._accepted_count, "closed": self._closed,
                "exit_reason": self._reason, "stats": copy.deepcopy(self._stats)}

    def public_trace(self) -> list[dict]:
        """Copies only submitted actions and public responses; no latent parameters."""
        return copy.deepcopy(self._trace)


Simulator = LocalSimulator
