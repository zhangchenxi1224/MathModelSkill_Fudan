"""Serial, retry-safe client for the four published CUMCM 2026 B endpoints.

No request is made at import or construction.  A transport is a callable accepting
``(path, JSON-compatible dict)`` and returning the JSON response dict.  HTTP and
the local simulator use that same boundary; policies never need environment truth.
"""

from __future__ import annotations

import copy
import http.client
import json
import math
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class ProtocolError(RuntimeError):
    """Base class for a failure at the public protocol boundary."""


class TransportError(ProtocolError):
    """No complete response: the action may already have executed."""


class ConnectionClosedError(TransportError):
    """The endpoint is not open, or the session has ended."""


class HTTPStatusError(ProtocolError):
    def __init__(self, status_code: int, response: dict | None = None,
                 message: str | None = None):
        self.status_code = status_code
        self.response = response
        super().__init__(message or f"HTTP {status_code}: action not confirmed")


class ConflictError(HTTPStatusError):
    def __init__(self, response: dict | None = None, message: str | None = None):
        super().__init__(409, response, message or "HTTP 409: ID conflict or concurrent action")


class RequestRejected(ProtocolError):
    def __init__(self, response: dict):
        self.response = response
        super().__init__("Simulator returned accepted=false; local state was not changed")


class InvalidResponseError(ProtocolError):
    """A response cannot safely be used to advance the local state."""


class DeadlineExceeded(ProtocolError):
    """The conservative local deadline or known virtual limit has been reached."""


class PendingActionError(ProtocolError):
    """An unresolved action must be retried with its original ID before a new action."""


class ConcurrentRequestError(ProtocolError):
    """This client already has an action in progress; no second action was sent."""


class ClientStateError(ProtocolError):
    """The requested action is incompatible with the confirmed local state."""


class JournalError(ProtocolError):
    """Local audit log failed; never retry an already confirmed action because of this."""


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def normalize_position(position: Sequence[float] | Mapping[str, float]) -> tuple[float, float]:
    if isinstance(position, Mapping):
        if set(position) != {"x", "y"}:
            raise ValueError("position must contain exactly x and y")
        values = (position["x"], position["y"])
    else:
        if isinstance(position, (str, bytes)) or len(position) != 2:
            raise ValueError("position must contain two coordinates")
        values = (position[0], position[1])
    if not all(_finite_number(v) and abs(v) <= 2_000_000 for v in values):
        raise ValueError("coordinates must be finite numbers with |coordinate| <= 2000000")
    return tuple(0.0 if v == 0 else float(v) for v in values)


def normalize_channel(channel: Any) -> int:
    if not _finite_number(channel) or int(channel) != channel or not 1 <= channel <= 20:
        raise ValueError("channel must be an integer from 1 through 20")
    return int(channel)


def validate_identifier(value: Any, max_bytes: int, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise ValueError(f"{name} is not valid UTF-8") from exc
    if not 1 <= size <= max_bytes or any(unicodedata.category(c) in {"Cc", "Cf"} for c in value):
        raise ValueError(f"{name} must have 1..{max_bytes} UTF-8 bytes and no control/format characters")
    return value


class HTTPTransport:
    """Small urllib transport; no third-party package or persistent browser required."""

    def __init__(self, base_url: str = "http://127.0.0.1:2026", timeout: float = 5.0):
        parts = urlsplit(base_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("base_url must be an HTTP(S) URL")
        if parts.query or parts.fragment or parts.path not in {"", "/"}:
            raise ValueError("base_url must not contain a path, query or fragment")
        if not _finite_number(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)

    def __call__(self, path: str, payload: dict) -> dict:
        return self.request(path, payload)

    def request(self, path: str, payload: dict, timeout: float | None = None) -> dict:
        if path not in {"/enter", "/measure", "/clear", "/exit"}:
            raise ValueError("unknown protocol path")
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
        request = Request(self.base_url + path, data=data,
                          headers={"Content-Type": "application/json; charset=utf-8"},
                          method="POST")
        try:
            with urlopen(request, timeout=self.timeout if timeout is None else timeout) as reply:
                status = reply.status
                raw = reply.read()
        except HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
            except (ValueError, UnicodeError, OSError):
                body = None
            if not isinstance(body, dict):
                body = None
            if exc.code == 409:
                raise ConflictError(body) from exc
            raise HTTPStatusError(exc.code, body) from exc
        except (URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            raise TransportError(f"No complete HTTP response: {type(exc).__name__}: {exc}") from exc
        if status != 200:
            raise HTTPStatusError(status)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise InvalidResponseError("HTTP 200 did not contain a complete UTF-8 JSON response") from exc
        if not isinstance(body, dict):
            raise InvalidResponseError("Response JSON must be an object")
        return body


class RobotClient:
    """Confirmed robot state plus serial idempotent execution and JSONL auditing.

    Methods return the actual successful protocol dictionaries.  A definitive
    accepted=false raises RequestRejected and permits a later new action.  When
    all retries lose their responses, pending_request is retained and new actions
    are blocked; retry_pending() uses exactly the original path/body/request_id.

    ``deadline`` is monotonic time, conservatively measured from the FIRST /enter
    transmission start, including time lost before its cached response arrives.
    """

    def __init__(self, robot_id: str = "local-team", transport: Callable[[str, dict], dict] | None = None,
                 *, base_url: str = "http://127.0.0.1:2026", timeout: float = 5.0,
                 log_path: str | Path | None = None, max_retries: int = 3,
                 retry_delay: float = 0.1, clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], float] = time.time,
                 sleeper: Callable[[float], None] = time.sleep,
                 request_prefix: str | None = None, keep_history: bool = True):
        self.robot_id = validate_identifier(robot_id, 64, "robot_id")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be a nonnegative integer")
        if not _finite_number(retry_delay) or retry_delay < 0:
            raise ValueError("retry_delay must be nonnegative")
        self.__transport = transport if transport is not None else HTTPTransport(base_url, timeout)
        self._clock, self._wall_clock, self._sleeper = clock, wall_clock, sleeper
        self._max_retries, self._retry_delay = max_retries, float(retry_delay)
        self._prefix = validate_identifier(request_prefix or uuid.uuid4().hex[:16], 90, "request_prefix")
        self._sequence = 0
        self._lock = threading.Lock()
        self._pending: dict | None = None
        self.position = (0.0, 0.0)
        self.current_channel = 1
        self.virtual_time = 0.0
        self.cleared_channels: set[int] = set()
        self.entered = False
        self.exited = False
        self.deadline: float | None = None
        self.max_virtual_duration_s = 360000.0
        self.max_real_duration_s = 1200.0
        self.stats = {"walk_distance": 0.0, "switches": 0, "measures": 0,
                      "clear_attempts": 0, "successes": 0, "failures": 0,
                      "accepted_actions": 0, "transport_attempts": 0,
                      "movement_time": 0.0, "measure_time": 0.0,
                      "switch_time": 0.0, "clear_time": 0.0}
        self.history: list[dict] = []
        self._keep_history = keep_history
        self._journal = None
        self.log_path = Path(log_path) if log_path is not None else None
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._journal = self.log_path.open("a", encoding="utf-8", newline="\n")

    @property
    def cleared_count(self) -> int:
        return len(self.cleared_channels)

    @property
    def pending_request(self) -> dict | None:
        return copy.deepcopy(self._pending)

    def remaining_real_time(self) -> float:
        return math.inf if self.deadline is None else max(0.0, self.deadline - self._clock())

    def state_snapshot(self) -> dict:
        return {"position": {"x": self.position[0], "y": self.position[1]},
                "current_channel": self.current_channel, "virtual_time_s": self.virtual_time,
                "cleared_count": self.cleared_count, "cleared_channels": sorted(self.cleared_channels),
                "entered": self.entered, "exited": self.exited, "deadline": self.deadline}

    def close_log(self) -> None:
        """Close local file only.  Deliberately does not call official /exit."""
        if self._journal is not None:
            self._journal.close()
            self._journal = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close_log()

    def enter(self) -> dict:
        return self._new_action("/enter", {})

    def measure(self, channel: int, position: Sequence[float] | Mapping[str, float]) -> dict:
        return self._new_action("/measure", self._action_fields(channel, position))

    def clear(self, channel: int, position: Sequence[float] | Mapping[str, float]) -> dict:
        return self._new_action("/clear", self._action_fields(channel, position))

    def exit(self) -> dict:
        return self._new_action("/exit", {})

    @staticmethod
    def _action_fields(channel, position) -> dict:
        x, y = normalize_position(position)
        return {"channel": normalize_channel(channel), "position": {"x": x, "y": y}}

    def _new_action(self, path: str, fields: dict) -> dict:
        if not self._lock.acquire(blocking=False):
            raise ConcurrentRequestError("A previous action is still awaiting its response")
        try:
            if self._pending is not None:
                raise PendingActionError("Call retry_pending(); a new action could corrupt state")
            if self.exited:
                raise ClientStateError("Session already exited; no further request was sent")
            if path == "/enter" and self.entered:
                raise ClientStateError("Already entered; no duplicate /enter was sent")
            if path != "/enter" and not self.entered:
                raise ClientStateError("Call enter() first")
            self._check_deadline()
            self._sequence += 1
            body = {"arena_id": "default", "robot_id": self.robot_id,
                    "request_id": f"{self._prefix}-{self._sequence}", **fields}
            self._pending = {"path": path, "body": body,
                             "started_monotonic": self._clock(), "attempts": 0}
            return self._execute_pending()
        finally:
            self._lock.release()

    def retry_pending(self) -> dict:
        if not self._lock.acquire(blocking=False):
            raise ConcurrentRequestError("An action is already in progress")
        try:
            if self._pending is None:
                raise ClientStateError("There is no unresolved action")
            return self._execute_pending()
        finally:
            self._lock.release()

    def _check_deadline(self) -> None:
        if self.deadline is not None and self._clock() >= self.deadline:
            raise DeadlineExceeded("Conservative real-time deadline reached; no request was sent")
        if self.entered and self.virtual_time >= self.max_virtual_duration_s:
            raise DeadlineExceeded("Confirmed virtual-time limit reached; endpoint may be closed")

    def _execute_pending(self) -> dict:
        assert self._pending is not None
        pending = self._pending
        path, body = pending["path"], pending["body"]
        for local_attempt in range(self._max_retries + 1):
            self._check_deadline()
            pending["attempts"] += 1
            self.stats["transport_attempts"] += 1
            record = {"event": "request", "path": path, "request": copy.deepcopy(body),
                      "attempt": pending["attempts"], "wall_timestamp_s": self._wall_clock(),
                      "monotonic_s": self._clock(), "state_before": self.state_snapshot()}
            try:
                if isinstance(self.__transport, HTTPTransport):
                    limit = min(self.__transport.timeout, self.remaining_real_time())
                    response = self.__transport.request(path, copy.deepcopy(body), timeout=limit)
                else:
                    response = self.__transport(path, copy.deepcopy(body))
                record["response"] = copy.deepcopy(response)
                self._validate_response(path, response)
                if response["accepted"] is not True:
                    self._pending = None
                    record["outcome"] = "rejected"
                    self._log(record)
                    raise RequestRejected(response)
                self._apply_response(path, body, response, pending["started_monotonic"])
                self._pending = None
                record["outcome"] = "accepted"
                self._log(record)
                return response
            except RequestRejected:
                raise
            except HTTPStatusError as exc:
                record.update(outcome="http_error", status_code=exc.status_code,
                              error=str(exc), response=exc.response)
                self._log(record)
                if exc.status_code not in {429, 500, 502, 503, 504}:
                    # 409 is fail-closed: it can indicate another actor or ID collision.
                    if exc.status_code != 409:
                        self._pending = None
                    raise
                if local_attempt == self._max_retries:
                    raise
            except (TransportError, TimeoutError, ConnectionError, OSError) as exc:
                record.update(outcome="transport_error", error=f"{type(exc).__name__}: {exc}")
                self._log(record)
                if local_attempt == self._max_retries:
                    if isinstance(exc, TransportError):
                        raise
                    raise TransportError("Retries exhausted; original action remains pending") from exc
            except InvalidResponseError as exc:
                record.update(outcome="invalid_response", error=str(exc))
                self._log(record)
                # A malformed response can follow an executed action.  Do not allow new IDs.
                raise
            delay = self._retry_delay * (2 ** local_attempt)
            remaining = self.remaining_real_time()
            if remaining <= delay:
                raise DeadlineExceeded("No time remains for another retry; action remains pending")
            if delay:
                self._sleeper(delay)
        raise AssertionError("unreachable")

    def _validate_response(self, path: str, response: Any) -> None:
        if not isinstance(response, dict) or not isinstance(response.get("accepted"), bool):
            raise InvalidResponseError("Missing Boolean accepted field")
        for key in ("real_timestamp_ms", "virtual_time_s"):
            if not _finite_number(response.get(key)) or response[key] < 0:
                raise InvalidResponseError(f"Missing or invalid {key}")
        if not response["accepted"]:
            return
        if response["virtual_time_s"] + 1e-6 < self.virtual_time:
            raise InvalidResponseError("Accepted response moved virtual time backwards")
        if path == "/enter":
            for key in ("remaining_real_duration_s", "max_real_duration_s", "max_virtual_duration_s"):
                if not _finite_number(response.get(key)) or response[key] < 0:
                    raise InvalidResponseError(f"Missing or invalid enter field {key}")
            remaining = response["remaining_real_duration_s"]
            if int(remaining) != remaining or remaining > response["max_real_duration_s"]:
                raise InvalidResponseError("remaining_real_duration_s is not an integer in its permitted range")
        elif path == "/measure":
            result = response.get("measure_result")
            if result not in {"direction", "near", "no_signal"}:
                raise InvalidResponseError("Unknown measure_result")
            if result == "direction":
                value = response.get("svd_deg")
                if not _finite_number(value) or not 0 <= value < 360:
                    raise InvalidResponseError("direction requires svd_deg in [0, 360)")
            elif "svd_deg" in response:
                raise InvalidResponseError("near/no_signal must not carry svd_deg")
        elif path == "/clear" and response.get("clear_result") not in {"success", "no_target_in_range"}:
            raise InvalidResponseError("Unknown clear_result")
        elif path == "/exit" and response.get("exit_reason") != "user_exit":
            raise InvalidResponseError("Accepted /exit must return exit_reason=user_exit")

    def _apply_response(self, path: str, body: dict, response: dict, started: float) -> None:
        if path == "/enter":
            self.entered = True
            self.deadline = started + float(response["remaining_real_duration_s"])
            self.max_real_duration_s = float(response["max_real_duration_s"])
            self.max_virtual_duration_s = float(response["max_virtual_duration_s"])
        elif path in {"/measure", "/clear"}:
            position = normalize_position(body["position"])
            distance = math.dist(self.position, position)
            self.stats["walk_distance"] += distance
            self.stats["movement_time"] += distance / 5.0
            self.position = position
            channel = body["channel"]
            if path == "/measure":
                switched = int(channel != self.current_channel)
                self.stats["switches"] += switched
                self.stats["switch_time"] += switched
                self.stats["measures"] += 1
                self.stats["measure_time"] += 5.0
                self.current_channel = channel
            else:
                self.stats["clear_attempts"] += 1
                success = response["clear_result"] == "success"
                self.stats["clear_time"] += 5.0 if success else 3.0
                if success:
                    self.cleared_channels.add(channel)
                    self.stats["successes"] += 1
                else:
                    self.stats["failures"] += 1
        elif path == "/exit":
            self.exited = True
        self.virtual_time = float(response["virtual_time_s"])
        self.stats["accepted_actions"] += 1

    def _log(self, record: dict) -> None:
        record["state_after"] = self.state_snapshot()
        if self._keep_history:
            self.history.append(copy.deepcopy(record))
        if self._journal is not None:
            try:
                self._journal.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                self._journal.flush()
            except (OSError, ValueError) as exc:
                raise JournalError("Local JSONL audit failed; confirmed robot state remains committed") from exc


# Descriptive alias for callers that prefer to spell out the transport boundary.
ProtocolClient = RobotClient
