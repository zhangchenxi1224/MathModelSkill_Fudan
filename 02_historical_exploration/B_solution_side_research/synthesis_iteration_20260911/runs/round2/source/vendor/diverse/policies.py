"""Experimental policy adapters over the frozen, observation-only solver.

Planning scores never become safety certificates.  The original action
transport, accounting, budget checks, completion logic, and run/finally stay
in the vendor Solver; only bounded local proposals and joint ordering differ.
"""
from __future__ import annotations

import math
from typing import Mapping

from bsolver.coverage import order_route
from bsolver.geometry import (certify_clear, distance, distance_to_polygon,
                              minimum_enclosing_circle, nearest_safe_clear)
from bsolver.knowledge import InconsistentKnowledge
from bsolver.sensing import (choose_measurement, direction_outcome_bound,
                            optical_fallback_points)
from bsolver.strategy import Solver


ROUTES = ("robust", "fisher", "infogain", "rollout", "joint_route", "probe")
ORIGINAL_RING = 900.0 * math.sqrt(3.0)


def ring_certificate(ring_radius: float) -> dict:
    """Analytic continuous-domain Q3 certificate, with a numerical guard."""
    if isinstance(ring_radius, bool):
        raise ValueError("ring_radius must be a finite length")
    ring = float(ring_radius)
    domain, reception = 1800.0, 1000.0
    if not math.isfinite(ring) or not 0.0 <= ring <= math.sqrt(3.0) * domain:
        raise ValueError("ring_radius outside the analytic formula domain")
    rho = math.sqrt(max(ring * ring / 3.0,
                        domain * domain + ring * ring - math.sqrt(3.0) * domain * ring))
    bound = rho + 1e-6
    if bound >= reception:
        raise ValueError("ring_radius does not certify coverage with a positive margin")
    return {"type": "analytic_regular_hexagon_and_origin", "problem": 3,
            "domain_radius_m": domain, "minimum_reception_radius_m": reception,
            "ring_radius_m": ring, "covering_radius_upper_m": bound,
            "distance_margin_m": reception - bound, "point_count": 7,
            "requires_each_unresolved_channel_at_all_seven_points": True,
            "guarantees_localization_alone": False, "proof": "docs/policies.md"}


class GeometrySolver(Solver):
    """Optional, explicitly requested Q3 geometry ablation only."""

    def __init__(self, client, config=None, decision_log=None, *, options=None):
        self.options = dict(options or {})
        super().__init__(client, config, decision_log)
        self.coverage_proof = None
        ring = self.options.get("ring_radius")
        if ring is not None:
            if self.config.problem != 3:
                raise ValueError("ring_radius is proved only for Q3 and is forbidden for Q4")
            self.coverage_proof = ring_certificate(ring)
            # Scale the existing ordered points; do not introduce tie-break
            # rotations as an unintended second experimental variable.
            scale = float(ring) / ORIGINAL_RING
            self.points = [(x * scale, y * scale) for x, y in self.points]
            self._record("coverage_ablation", certificate=self.coverage_proof,
                         coverage_points=self.points)

    def _complete_evidence(self):
        evidence = super()._complete_evidence()
        if (evidence is not None and self.coverage_proof is not None
                and evidence["type"] == "per_channel_coverage"):
            evidence["construction"] = "origin_and_regular_hexagon_ablation"
            evidence["analytic_certificate"] = self.coverage_proof
        return evidence


class BoundedLocalSolver(GeometrySolver):
    """Finite proposal budget plus the original independent optical cover."""

    def __init__(self, client, config=None, decision_log=None, *, options=None):
        super().__init__(client, config, decision_log, options=options)
        limit = self.config.local_measure_limit
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("local_measure_limit must be a nonnegative integer")
        self._local_actions = {channel: 0 for channel in self.channels}
        self._probe_attempted = set()

    def _remaining_local_actions(self, channel):
        return max(0, self.config.local_measure_limit - self._local_actions[channel])

    def _baseline_action(self, knowledge):
        point, info = choose_measurement(knowledge, self.client.position,
                                         self.config.sensing, self.config.bin_width_deg,
                                         self.config.radius_weight)
        return ({"kind": "measure", "point": point} if point is not None else None), info

    def _select_action(self, knowledge):
        return self._baseline_action(knowledge)

    @staticmethod
    def _valid_action(action, knowledge, probe_attempted):
        if not isinstance(action, dict) or action.get("kind") not in ("measure", "clear"):
            return False
        try:
            x, y = action["point"]
            if not math.isfinite(float(x)) or not math.isfinite(float(y)):
                return False
            point = float(x), float(y)
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
        if action["kind"] == "clear":
            return (knowledge.channel not in probe_attempted
                    and all(distance(point, old) >= .05
                            for old in knowledge.failed_clear_positions))
        return all(distance(point, obs.position) >= .05 for obs in knowledge.observations)

    def _localize(self, channel):
        knowledge = self.channels[channel]
        if knowledge.status == "cleared" or self._try_certified_clear(knowledge):
            return
        while self._remaining_local_actions(channel):
            action, info = self._select_action(knowledge)
            if action is None:
                break
            # A planner cannot bypass finite actions or repeat fixed-location
            # noise measurements. Baseline malformed proposals also fail closed.
            if not self._valid_action(action, knowledge, self._probe_attempted):
                self._record("proposal_rejected", target_channel=channel,
                             reason="invalid_repeated_or_probe_limit", selection=info)
                action, info = self._baseline_action(knowledge)
                if action is None or not self._valid_action(action, knowledge, self._probe_attempted):
                    break
            point = tuple(float(v) for v in action["point"])
            self._record("choose_action", target_channel=channel, action=action,
                         selection=info, remaining_local_actions=self._remaining_local_actions(channel))
            self._local_actions[channel] += 1
            if action["kind"] == "clear":
                self._probe_attempted.add(channel)
                if self._clear(channel, point,
                               {"type": "bounded_experimental_probe", "safety_certificate": False,
                                "maximum_per_target": 1}):
                    return
            else:
                self._measure(channel, point, "local_experimental_sensing")
            if knowledge.status == "cleared" or self._try_certified_clear(knowledge):
                return
        self._optical_fallback(channel)

    def _optical_fallback(self, channel):
        # Kept equivalent to vendor Solver._localize's finite fallback. Neither
        # particles nor negative-observation holes are used to prune this cover.
        knowledge = self.channels[channel]
        if knowledge.first_direction is None:
            raise InconsistentKnowledge("detected source lacks direction and near was not cleared")
        self.stats["fallback_targets"] += 1
        points = optical_fallback_points(knowledge.first_direction)
        if distance(self.client.position, points[-1]) < distance(self.client.position, points[0]):
            points.reverse()
        attempted = 0
        for point in points:
            if distance_to_polygon(knowledge.hull, point) > 20. + 1e-5:
                continue
            attempted += 1
            if self._clear(channel, point, {"type": "optical_grid_attempt", "max_grid_points": 110}):
                return
        raise InconsistentKnowledge(f"finite optical cover exhausted after {attempted} attempts")


class PlannedSolver(BoundedLocalSolver):
    def __init__(self, client, config=None, decision_log=None, *, mode, options=None):
        self.mode = mode
        super().__init__(client, config, decision_log, options=options)

    def _select_action(self, knowledge):
        # Import lazily so the robust branch needs no probabilistic dependency.
        from .planning import choose_action
        planner_options = {k: v for k, v in self.options.items()
                           if k not in {"ring_radius", "shared_max_per_target", "shared_radius_ratio"}}
        planner_options['allow_clear'] = knowledge.channel not in self._probe_attempted
        try:
            action, info = choose_action(knowledge, self.client.position,
                                        self.client.current_channel, self.mode, planner_options)
        except Exception as exc:
            action, info = None, {"fallback_reason": "planner_exception",
                                  "error": f"{type(exc).__name__}: {exc}"}
        if action is not None and self._valid_action(action, knowledge, self._probe_attempted):
            return action, info
        action, baseline_info = self._baseline_action(knowledge)
        return action, {"mode": self.mode, "planner": info,
                        "fallback": "frozen_choose_measurement", "baseline": baseline_info}


class JointRouteSolver(BoundedLocalSolver):
    """Reorder pending cover points and service visits, sharing useful stations."""

    def __init__(self, client, config=None, decision_log=None, *, options=None):
        super().__init__(client, config, decision_log, options=options)
        maximum = self.options.get("shared_max_per_target", 1)
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
            raise ValueError("shared_max_per_target must be a nonnegative integer")
        self.shared_max_per_target = maximum
        self.shared_radius_ratio = float(self.options.get("shared_radius_ratio", .9))
        if not 0.0 < self.shared_radius_ratio < 1.0:
            raise ValueError("shared_radius_ratio must lie strictly between 0 and 1")
        self._shared_actions = {channel: 0 for channel in self.channels}

    def _reorder_suffix(self, start):
        if start is None or start >= len(self.points):
            return None
        if any(i >= start for knowledge in self.channels.values() for i in knowledge.coverage_indices):
            raise InconsistentKnowledge("refusing to reorder an already observed coverage index")
        original = self.points[start:]
        reordered = order_route(original, self.client.position)
        if len(reordered) != len(original) or set(reordered) != set(original):
            raise InconsistentKnowledge("route optimizer changed the discovery cover")
        if reordered != original:
            # enumerate(self.points) in the inherited run sees the new suffix;
            # no completed index moves, so every coverage ledger stays valid.
            self.points[start:] = reordered
            self._record("reorder_pending_coverage", first_pending_index=start,
                         remaining_points=reordered, preserves_completed_indices=True)
        return self.points[start]

    def _share_at_current(self):
        point = self.client.position
        targets = sorted(self._detected(),
                         key=lambda k: (k.channel != self.client.current_channel, k.channel))
        for knowledge in targets:
            channel = knowledge.channel
            if (not self._remaining_local_actions(channel)
                    or self._shared_actions[channel] >= self.shared_max_per_target
                    or any(distance(point, obs.position) < .05 for obs in knowledge.observations)):
                continue
            _, before = minimum_enclosing_circle(knowledge.hull)
            if before <= self.config.clear_radius:
                continue
            after = direction_outcome_bound(knowledge.hull, point, knowledge.epsilon_deg,
                                             self.config.bin_width_deg)
            if not math.isfinite(after) or after >= self.shared_radius_ratio * before:
                continue
            self._local_actions[channel] += 1
            self._shared_actions[channel] += 1
            self._record("shared_station_selection", target_channel=channel,
                         direction_radius_before_m=before, direction_radius_bound_m=after,
                         visibility_guaranteed=False,
                         selection_status="direction-branch bound; no_signal remains possible")
            self._measure(channel, point, "shared_detected_station")
            # _measure handles a near response. Delay other clear moves until
            # service selection so subsequent shared measurements stay colocated.

    def _service_destination(self, knowledge):
        center, radius = minimum_enclosing_circle(knowledge.hull)
        if radius <= self.config.clear_radius:
            point = (nearest_safe_clear(knowledge.hull, self.client.position,
                                         radius=self.config.clear_radius)
                     if self.config.nearest_safe else center)
            if point is not None and certify_clear(knowledge.hull, point, radius=20., margin=1e-5):
                return point, 5., "certified_clear"
        if self._remaining_local_actions(knowledge.channel):
            action, _ = self._baseline_action(knowledge)
            if action is not None:
                return action["point"], 5. + int(self.client.current_channel != knowledge.channel), "predicted_measurement"
        points = optical_fallback_points(knowledge.first_direction)
        if distance(self.client.position, points[-1]) < distance(self.client.position, points[0]):
            points.reverse()
        point = next((p for p in points if distance_to_polygon(knowledge.hull, p) <= 20. + 1e-5), center)
        return point, 5., "first_optical_fallback"

    def _joint_service(self, next_point, force=False):
        suffix_start = self.points.index(next_point) if next_point is not None else None
        next_point = self._reorder_suffix(suffix_start) if suffix_start is not None else None
        self._share_at_current()
        while self._detected():
            candidates = []
            for knowledge in self._detected():
                destination, action_cost, kind = self._service_destination(knowledge)
                detour = distance(self.client.position, destination)
                if next_point is not None:
                    detour += distance(destination, next_point) - distance(self.client.position, next_point)
                candidates.append((detour / 5. + action_cost, knowledge.channel, detour, destination, kind))
            estimated_cost, channel, detour, destination, kind = min(candidates)
            if not force and detour > self.config.joint_detour_m:
                return
            self._record("joint_service_selection", target_channel=channel,
                         estimated_first_action_point=destination, first_action_kind=kind,
                         estimated_incremental_cost_s=estimated_cost, detour_m=detour,
                         score_status="first-action heuristic, not total-service bound")
            self._localize(channel)
            if self._complete_evidence() is not None:
                return
            next_point = self._reorder_suffix(suffix_start) if suffix_start is not None else None
            self._share_at_current()


def make_solver(client, config, route, decision_log=None, options: Mapping | None = None):
    """Construct one route without modifying any shared SolverConfig fields."""
    if route not in ROUTES:
        raise ValueError(f"route must be one of {ROUTES}")
    opts = dict(options or {})
    if opts.get("ring_radius") is not None:
        if config is not None and config.problem != 3:
            raise ValueError("ring_radius is forbidden for Q4")
        ring_certificate(opts["ring_radius"])
    if route == "robust":
        if opts.get("ring_radius") is None:
            return Solver(client, config, decision_log)
        return GeometrySolver(client, config, decision_log, options=opts)
    if route == "joint_route":
        return JointRouteSolver(client, config, decision_log, options=opts)
    return PlannedSolver(client, config, decision_log, mode=route, options=opts)
