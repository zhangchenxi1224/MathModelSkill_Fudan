"""An exact symbolic observation ledger plus a conservative convex position hull.

Negative observations remain explicit constraints: they are NOT incorrectly
subtracted from a convex polygon. The hull deliberately loses hole information.
Safety certificates are checked on the entire hull, which contains the exact
symbolic feasible position projection under the stated numerical assumptions.
"""
from dataclasses import dataclass, field
import math
from .geometry import (disk_outer_polygon, clip_halfplane, clip_bearing,
                       contains, distance, max_distance, bearing_deg, wrap_angle_deg)

Point = tuple[float, float]


class InconsistentKnowledge(RuntimeError):
    pass


def intersect_outer_disk(poly, center, radius, n=96):
    result = list(poly)
    for k in range(n):
        angle = 2 * math.pi * k / n
        normal = math.cos(angle), math.sin(angle)
        result = clip_halfplane(result, normal,
                                normal[0]*center[0]+normal[1]*center[1]+radius+1e-7)
        if not result:
            break
    return result


@dataclass
class Observation:
    position: Point
    result: str
    angle: float | None = None


@dataclass
class ChannelKnowledge:
    channel: int
    problem: int
    epsilon_deg: float = 1.0051
    status: str = "unknown"  # unknown, detected, cleared, absent
    hull: list[Point] = field(default_factory=lambda: disk_outer_polygon((0., 0.), 1800., 128))
    observations: list[Observation] = field(default_factory=list)
    failed_clear_positions: list[Point] = field(default_factory=list)
    coverage_indices: set[int] = field(default_factory=set)
    first_direction: Observation | None = None
    positive_positions: list[Point] = field(default_factory=list)
    clear_position: Point | None = None

    def observe(self, position, response, coverage_index=None):
        p = float(position[0]), float(position[1])
        result = response["measure_result"]
        if coverage_index is not None:
            self.coverage_indices.add(coverage_index)
        angle = float(response["svd_deg"]) if result == "direction" else None
        obs = Observation(p, result, angle)
        for old in self.observations:
            if old.position == p and self.status != "cleared":
                if old.result != result or (angle is not None and
                        abs(wrap_angle_deg(angle-old.angle)) > 1e-8):
                    raise InconsistentKnowledge("same-place observation changed before target removal")
        self.observations.append(obs)
        if result in ("direction", "near"):
            if self.status in ("absent", "cleared"):
                raise InconsistentKnowledge("positive observation contradicts channel status")
            self.status = "detected"
            self.positive_positions.append(p)
            if result == "direction":
                if self.first_direction is None:
                    self.first_direction = obs
                updated = clip_bearing(self.hull, p, angle, self.epsilon_deg)
                updated = intersect_outer_disk(updated, p, 1500.)
            else:
                updated = intersect_outer_disk(self.hull, p, 5.)
            if not updated:
                raise InconsistentKnowledge("empty positive-observation hull; do not silently reset")
            self.hull = updated
        elif result != "no_signal":
            raise ValueError("unknown measure_result")

    def record_clear(self, position, response):
        if response["clear_result"] == "success":
            self.status = "cleared"
            self.clear_position = tuple(position)
        elif response["clear_result"] == "no_target_in_range":
            self.failed_clear_positions.append(tuple(position))
        else:
            raise ValueError("unknown clear_result")

    def compatible_hidden_state(self, g, radius, direction_deg=None):
        """Exact ledger predicate for a *fixed* hypothetical g,R,u.

        Used by the external test evaluator and optional candidate reasoning;
        the deployed policy never receives real g/R/u. No per-observation reset
        of the radius or orientation is allowed.
        """
        if self.status == "absent" or not (1000 <= radius <= 1500):
            return False
        if distance(g, (0., 0.)) > 1800 + 1e-8:
            return False
        if self.problem == 3 and direction_deg is not None:
            return False
        if self.clear_position is not None and distance(g, self.clear_position) > 20 + 1e-8:
            return False
        for p in self.failed_clear_positions:
            if distance(g, p) <= 20:
                return False
        for obs in self.observations:
            d = distance(g, obs.position)
            visible = d <= radius
            if direction_deg is not None and d > 1e-12:
                u = math.radians(direction_deg)
                visible = visible and ((obs.position[0]-g[0])*math.cos(u)
                                       +(obs.position[1]-g[1])*math.sin(u) >= -1e-8)
            if obs.result == "no_signal":
                if visible:
                    return False
            elif not visible:
                return False
            elif obs.result == "near":
                if d > 5 + 1e-8:
                    return False
            else:
                if d <= 5 - 1e-8:
                    return False
                if abs(wrap_angle_deg(bearing_deg(obs.position, g)-obs.angle)) > self.epsilon_deg+1e-8:
                    return False
        return True

    def radius_interval_for_position(self, g):
        """Omnidirectional existence interval; upper bound is open after a negative."""
        lower = max([1000.] + [distance(g, p) for p in self.positive_positions])
        negatives = [distance(g, o.position) for o in self.observations if o.result == "no_signal"]
        upper = min([1500.] + negatives)
        return {"lower": lower, "upper": upper, "upper_open": bool(negatives and upper in negatives)}

    def position_in_outer_hull(self, g):
        return contains(self.hull, g, tol=1e-5)

    def snapshot(self):
        return {"channel": self.channel, "status": self.status, "hull": self.hull,
                "coverage_indices": sorted(self.coverage_indices),
                "positive_count": len(self.positive_positions),
                "negative_count": sum(o.result == "no_signal" for o in self.observations),
                "failed_clear_positions": self.failed_clear_positions,
                "radius_bounds": "R in [1000,1500], shared across all ledger constraints",
                "orientation_bounds": "fixed u, all positive and disjunctive negative constraints" if self.problem == 4 else "omnidirectional"}
