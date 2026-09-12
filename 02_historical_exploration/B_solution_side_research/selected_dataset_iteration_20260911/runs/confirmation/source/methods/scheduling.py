"""Cheap, observation-only service ordering over the frozen solver.

RouteMixin has no __init__; set ``solver.schedule_options`` to a dict.  It
changes service order and an unobserved route suffix, never safety decisions.
"""
from __future__ import annotations

from collections import Counter
import math

from bsolver.coverage import order_route, route_length
from bsolver.geometry import (certify_clear, contains, distance,
                              minimum_enclosing_circle, nearest_safe_clear)
from bsolver.knowledge import InconsistentKnowledge


def closest_hull_point(hull, point):
    """Euclidean projection on a closed convex hull; used only as a proxy."""
    if not hull:
        raise InconsistentKnowledge("detected channel has an empty hull")
    if contains(hull, point, tol=0.0):
        return tuple(point)
    candidates = list(hull)
    for a, b in zip(hull, list(hull[1:]) + [hull[0]]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        norm2 = dx * dx + dy * dy
        if norm2:
            t = min(1.0, max(0.0, ((point[0] - a[0]) * dx +
                                   (point[1] - a[1]) * dy) / norm2))
            candidates.append((a[0] + t * dx, a[1] + t * dy))
    return min(candidates, key=lambda p: (distance(point, p), p[0], p[1]))


class RouteMixin:
    """Mix before Solver/IterationSolver; bounded O(targets * hull size) ranking.

    Supported options: reorder (bool, True), service_score (center|hull,
    center), shared (False).  Shared observations deliberately remain disabled
    because they need a common per-target measurement budget in the host.
    """

    def _route_options(self):
        options = getattr(self, "schedule_options", {})
        reorder = options.get("reorder", True)
        score = options.get("service_score", "center")
        if not isinstance(reorder, bool):
            raise ValueError("schedule reorder must be boolean")
        if score not in ("center", "hull"):
            raise ValueError("service_score must be 'center' or 'hull'")
        if options.get("shared", False) is not False:
            raise ValueError("cheap scheduling supports shared=False only")
        return reorder, score

    def _route_reorder_suffix(self, start):
        if start is None or start >= len(self.points):
            return None
        if start < 0:
            raise ValueError("negative pending coverage index")
        if any(i >= start for k in self.channels.values() for i in k.coverage_indices):
            raise InconsistentKnowledge("refusing to move an observed coverage index")
        old = self.points[start:]
        candidate = order_route(old, self.client.position)
        if Counter(candidate) != Counter(old):
            raise InconsistentKnowledge("route optimizer changed coverage stations")
        old_length = route_length(old, self.client.position)
        new_length = route_length(candidate, self.client.position)
        # A fresh nearest-neighbor initialization can be worse than the old
        # suffix. Retain the old suffix unless a measured length gain exists.
        if new_length < old_length - 1e-7:
            self.points[start:] = candidate
            self._record("cheap_reorder_pending_coverage", first_pending_index=start,
                         remaining_points=candidate, saved_route_distance_m=old_length-new_length,
                         preserves_completed_indices=True)
        return self.points[start]

    def _route_geometry(self, knowledge):
        cache = getattr(self, "_route_geometry_cache", None)
        if cache is None:
            self._route_geometry_cache = cache = {}
        key = tuple(knowledge.hull)
        saved = cache.get(knowledge.channel)
        if saved is None or saved[0] != key:
            saved = (key, minimum_enclosing_circle(knowledge.hull))
            cache[knowledge.channel] = saved
        return saved[1]

    def _route_destination(self, knowledge, score):
        center, radius = self._route_geometry(knowledge)
        if radius <= self.config.clear_radius:
            destination = (nearest_safe_clear(knowledge.hull, self.client.position,
                                              radius=self.config.clear_radius)
                           if self.config.nearest_safe else center)
            if destination is not None and certify_clear(knowledge.hull, destination,
                                                         radius=20., margin=1e-5):
                return destination, 5.0, "certified_clear_destination"
        destination = (center if score == "center" else
                       closest_hull_point(knowledge.hull, self.client.position))
        # This is an approximate service location, not a proposed sensor point.
        # choose_measurement is called only for the single selected channel.
        return destination, 5.0 + int(self.client.current_channel != knowledge.channel), score + "_proxy"

    def _joint_service(self, next_point, force=False):
        reorder, score = self._route_options()
        if next_point is None:
            suffix_start = None
        else:
            try:
                suffix_start = self.points.index(next_point)
            except ValueError as exc:
                raise InconsistentKnowledge("next coverage station is missing") from exc
        if reorder and suffix_start is not None:
            next_point = self._route_reorder_suffix(suffix_start)
        # At most one localization call per channel in this invocation. This
        # also prevents a non-progressing host override from an infinite loop.
        serviced = set()
        while True:
            candidates = []
            for knowledge in self._detected():
                if knowledge.channel in serviced:
                    continue
                destination, action_cost, kind = self._route_destination(knowledge, score)
                detour = distance(self.client.position, destination)
                if next_point is not None:
                    detour += distance(destination, next_point) - distance(self.client.position, next_point)
                detour = max(0.0, detour)
                if not math.isfinite(detour):
                    raise InconsistentKnowledge("nonfinite scheduling geometry")
                candidates.append((detour / 5.0 + action_cost, knowledge.channel,
                                   detour, destination, kind))
            if not candidates:
                return
            cost, channel, detour, destination, kind = min(candidates)
            if not force and detour > self.config.joint_detour_m:
                return
            self._record("cheap_joint_service_selection", target_channel=channel,
                         service_location_proxy=destination, proxy_kind=kind,
                         estimated_incremental_cost_s=cost, detour_m=detour,
                         score_status="geometric ranking heuristic; not remaining-time bound",
                         service_score=score)
            serviced.add(channel)
            self._localize(channel)
            if self._complete_evidence() is not None:
                return
            if reorder and suffix_start is not None:
                next_point = self._route_reorder_suffix(suffix_start)
