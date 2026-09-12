"""Single-robot coverage, active localization, and finite optical fallback.

Only a RobotClient and legal observations enter the policy. No simulator
configuration, true source count, locations, radii, or orientations are read.
"""
from dataclasses import dataclass, asdict
from pathlib import Path
import json
import math
import time
from .coverage import (omnidirectional_points, directional_points, order_route,
                       coverage_certificate)
from .geometry import (distance, minimum_enclosing_circle, nearest_safe_clear,
                       certify_clear, distance_to_polygon)
from .knowledge import ChannelKnowledge, InconsistentKnowledge
from .sensing import choose_measurement, optical_fallback_points


class BudgetExhausted(RuntimeError):
    pass


@dataclass
class SolverConfig:
    problem: int = 3
    sensing: str = "active"  # fixed, estimated, active
    scheduling: str = "joint"  # scan_then_clear, immediate, joint
    coverage: str = "triangular"
    spacing: float | None = None
    epsilon_deg: float = 1.0051
    clear_radius: float = 19.999
    local_measure_limit: int = 5
    joint_detour_m: float = 800.
    bin_width_deg: float = 4.
    radius_weight: float = 2.
    reserve_real_s: float = 5.
    max_virtual_s: float = 360000.
    nearest_safe: bool = True
    opportunistic_known_measurements: bool = False


class Solver:
    def __init__(self, client, config=None, decision_log=None):
        self.client = client
        self.config = config or SolverConfig()
        if self.config.problem not in (3, 4):
            raise ValueError("problem must be 3 or 4")
        if not (1.0051 <= self.config.epsilon_deg <= 1.06):
            raise ValueError("certified policy requires 1.0051 <= epsilon <= 1.06 degrees")
        if self.config.clear_radius > 19.999:
            raise ValueError("clear radius must leave numerical margin")
        self.channels = {f: ChannelKnowledge(f, self.config.problem, self.config.epsilon_deg)
                         for f in range(1, 21)}
        points = (omnidirectional_points() if self.config.problem == 3 else
                  directional_points(self.config.coverage, self.config.spacing))
        self.points = order_route(points, (0., 0.))
        self.decision_log = Path(decision_log) if decision_log else None
        if self.decision_log:
            self.decision_log.parent.mkdir(parents=True, exist_ok=True)
            self.decision_log.write_text("", encoding="utf-8")
        self.decisions = []
        self.stats = {"walk_distance_m": 0., "switches": 0, "measures": 0,
                      "clear_attempts": 0, "clear_successes": 0,
                      "fallback_targets": 0, "certified_clears": 0,
                      "initial_hull_invariant": True}
        self.stop_evidence = None
        self.event_counter = 0

    def _record(self, event, **data):
        item = {"sequence": self.event_counter, "event": event,
                "position": self.client.position, "channel": self.client.current_channel,
                "virtual_time_s": self.client.virtual_time, **data}
        self.event_counter += 1
        self.decisions.append(item)
        if self.decision_log:
            with self.decision_log.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(item, ensure_ascii=False, allow_nan=False)+"\n")

    def _check_budget(self, destination, maximum_action_cost):
        remaining = self.client.remaining_real_time()
        if remaining <= self.config.reserve_real_s + .2:
            raise BudgetExhausted("real_time_reserve")
        virtual_end = self.client.virtual_time + distance(self.client.position, destination)/5+maximum_action_cost
        if virtual_end >= self.config.max_virtual_s - 1:
            raise BudgetExhausted("virtual_time_reserve")

    def _measure(self, channel, point, reason, station=None):
        self._check_budget(point, 6)
        old_position, old_channel = self.client.position, self.client.current_channel
        response = self.client.measure(channel, point)
        if response.get("accepted") is not True:
            raise RuntimeError("measure was rejected")
        self.stats["walk_distance_m"] += distance(old_position, point)
        self.stats["switches"] += int(old_channel != channel)
        self.stats["measures"] += 1
        knowledge = self.channels[channel]
        knowledge.observe(point, response, station)
        self._record("measure", reason=reason, response=response, knowledge=knowledge.snapshot())
        if response["measure_result"] == "near":
            self._clear(channel, point, {"type": "near", "radius_m": 5.})
        return response

    def _clear(self, channel, point, certificate):
        self._check_budget(point, 5)
        old_position = self.client.position
        response = self.client.clear(channel, point)
        if response.get("accepted") is not True:
            raise RuntimeError("clear was rejected")
        self.stats["walk_distance_m"] += distance(old_position, point)
        self.stats["clear_attempts"] += 1
        success = response["clear_result"] == "success"
        self.stats["clear_successes"] += int(success)
        if success and certificate.get("type") in ("outer_hull", "near"):
            self.stats["certified_clears"] += 1
        self.channels[channel].record_clear(point, response)
        self._record("clear", certificate=certificate, response=response,
                     knowledge=self.channels[channel].snapshot())
        if not success and certificate.get("type") in ("outer_hull", "near"):
            raise InconsistentKnowledge("certified clear failed; stop rather than report success")
        return success

    def _try_certified_clear(self, knowledge):
        if knowledge.status != "detected":
            return knowledge.status == "cleared"
        center, rad = minimum_enclosing_circle(knowledge.hull)
        if rad > self.config.clear_radius:
            return False
        point = (nearest_safe_clear(knowledge.hull, self.client.position,
                                   radius=self.config.clear_radius)
                 if self.config.nearest_safe else center)
        if point is None or not certify_clear(knowledge.hull, point, radius=20., margin=1e-5):
            return False
        return self._clear(knowledge.channel, point,
                           {"type": "outer_hull", "mec_radius_m": rad,
                            "point_selection": "nearest_safe" if self.config.nearest_safe else "mec_center",
                            "hull_vertex_count": len(knowledge.hull)})

    def _localize(self, channel):
        knowledge = self.channels[channel]
        if knowledge.status == "cleared":
            return
        if self._try_certified_clear(knowledge):
            return
        for _ in range(self.config.local_measure_limit):
            q, info = choose_measurement(knowledge, self.client.position, self.config.sensing,
                                         self.config.bin_width_deg, self.config.radius_weight)
            if q is None:
                break
            self._record("choose_measurement", target_channel=channel, selection=info)
            self._measure(channel, q, "local_active_sensing")
            if knowledge.status == "cleared" or self._try_certified_clear(knowledge):
                return
        # Finite fallback is independent of visibility, angle noise, and whether
        # subsequent position hulls retain all negative-observation holes.
        if knowledge.first_direction is None:
            raise InconsistentKnowledge("detected source lacks direction and near was not cleared")
        self.stats["fallback_targets"] += 1
        points = optical_fallback_points(knowledge.first_direction)
        if distance(self.client.position, points[-1]) < distance(self.client.position, points[0]):
            points.reverse()
        attempted = 0
        for point in points:
            # Proven skip: if the entire clearance disk is disjoint from the
            # convex outer hull, it cannot contain the true source.
            if distance_to_polygon(knowledge.hull, point) > 20.+1e-5:
                continue
            attempted += 1
            if self._clear(channel, point, {"type": "optical_grid_attempt", "max_grid_points": 110}):
                return
        raise InconsistentKnowledge(f"finite optical cover exhausted after {attempted} attempts")

    def _detected(self):
        return [k for k in self.channels.values() if k.status == "detected"]

    def _complete_evidence(self):
        cleared = sorted(k.channel for k in self.channels.values() if k.status == "cleared")
        if len(cleared) >= 16:
            return {"type": "known_upper_bound", "cleared_channels": cleared,
                    "reason": "16 distinct successful clear confirmations; at most 16 sources"}
        if self._detected():
            return None
        unseen = [k for k in self.channels.values() if k.status not in ("cleared", "absent")]
        if all(len(k.coverage_indices) == len(self.points) for k in unseen):
            for k in unseen:
                k.status = "absent"
            return {"type": "per_channel_coverage", "cleared_channels": cleared,
                    "absent_channels": sorted(k.channel for k in self.channels.values() if k.status == "absent"),
                    "required_point_count": len(self.points), "points": self.points,
                    "checked_indices": {str(k.channel): sorted(k.coverage_indices)
                                        for k in self.channels.values() if k.status == "absent"},
                    "construction": "omnidirectional_seven" if self.config.problem == 3 else self.config.coverage}
        return None

    def _joint_service(self, next_point, force=False):
        while self._detected():
            candidates = []
            for k in self._detected():
                center, _ = minimum_enclosing_circle(k.hull)
                detour = distance(self.client.position, center)
                if next_point is not None:
                    detour += distance(center, next_point)-distance(self.client.position, next_point)
                candidates.append((detour, k.channel))
            detour, channel = min(candidates)
            if not force and detour > self.config.joint_detour_m:
                return
            self._localize(channel)
            if self._complete_evidence() is not None:
                return

    def run(self):
        start = time.monotonic()
        status, error = "incomplete", None
        try:
            if not self.client.entered:
                self.client.enter()
            self._record("start", config=asdict(self.config), coverage_points=self.points)
            for i, point in enumerate(self.points):
                next_point = self.points[i+1] if i+1 < len(self.points) else None
                channels = [k.channel for k in self.channels.values() if k.status == "unknown"]
                if self.config.opportunistic_known_measurements:
                    channels += [k.channel for k in self._detected()]
                if self.client.current_channel in channels:
                    channels.remove(self.client.current_channel)
                    channels.insert(0, self.client.current_channel)
                for channel in channels:
                    if self.channels[channel].status in ("cleared", "absent"):
                        continue
                    self._measure(channel, point, "global_coverage", station=i)
                    if self.config.scheduling == "immediate" and self.channels[channel].status == "detected":
                        self._localize(channel)
                    self.stop_evidence = self._complete_evidence()
                    if self.stop_evidence:
                        break
                if self.stop_evidence:
                    break
                if self.config.scheduling == "joint":
                    self._joint_service(next_point, force=next_point is None)
                self.stop_evidence = self._complete_evidence()
                if self.stop_evidence:
                    break
            if not self.stop_evidence:
                self._joint_service(None, force=True)
                self.stop_evidence = self._complete_evidence()
            if self.stop_evidence:
                status = "complete"
            else:
                error = "missing_completion_certificate"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._record("failure", error=error)
        finally:
            # Do not send new actions when an earlier action remains ambiguous.
            if self.client.entered and not self.client.exited and not getattr(self.client, "pending_request", None):
                try:
                    self.client.exit()
                except Exception as exc:
                    if status == "complete":
                        status = "complete_exit_unconfirmed"
                    error = (error+"; " if error else "")+f"exit: {type(exc).__name__}: {exc}"
        computed_time = (self.stats["walk_distance_m"]/5+self.stats["switches"]
                         +5*self.stats["measures"]+3*self.stats["clear_attempts"]
                         +2*self.stats["clear_successes"])
        result = {"status": status, "error": error, "problem": self.config.problem,
                  "config": asdict(self.config), **self.stats,
                  "total_virtual_time_s": self.client.virtual_time,
                  "independently_accounted_time_s": computed_time,
                  "timing_residual_s": self.client.virtual_time-computed_time,
                  "average_clear_time_s": (self.client.virtual_time/self.stats["clear_successes"]
                                           if self.stats["clear_successes"] else None),
                  "program_real_time_s": time.monotonic()-start,
                  "stop_evidence": self.stop_evidence,
                  "source_total": None, "clear_fraction": None}
        self._record("finish", result=result)
        return result
