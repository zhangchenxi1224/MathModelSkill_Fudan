"""Small observation-compatible hypothesis grid for no-signal-aware ranking.

Sampling is a heuristic only. Candidate points, angle-region certificates,
20 m clearance certification and the finite optical fallback remain unchanged.
The production path receives ChannelKnowledge and RobotClient, never source truth.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
import math

from .geometry import distance, distance_to_polygon, minimum_enclosing_circle
from .knowledge import InconsistentKnowledge
from .sensing import (candidate_points, choose_measurement, direction_outcome_bound,
                      optical_fallback_points)
from .strategy import Solver


VARIANT = "sampled_nosignal_v1"


@dataclass(frozen=True)
class NoSignalConfig:
    directional_prior: float = .5
    position_sample_count: int = 7
    radius_samples: tuple[float, ...] = (1000., 1250., 1500.)
    orientation_sample_count: int = 24
    interior_fraction: float = .75

    def __post_init__(self):
        if not math.isfinite(self.directional_prior) or not 0 <= self.directional_prior <= 1:
            raise ValueError("directional_prior must lie in [0,1]")
        for value, limit, name in ((self.position_sample_count, 7, "position_sample_count"),
                                   (self.orientation_sample_count, 24, "orientation_sample_count")):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= limit:
                raise ValueError(f"{name} must be an integer in [1,{limit}]")
        samples = tuple(self.radius_samples)
        if (not 3 <= len(samples) <= 5 or len(set(samples)) != len(samples)
                or not all(not isinstance(r, bool) and math.isfinite(r) and 1000 <= r <= 1500 for r in samples)):
            raise ValueError("radius_samples must contain 3..5 distinct radii in [1000,1500]")
        object.__setattr__(self, "radius_samples", samples)
        if not math.isfinite(self.interior_fraction) or not 0 < self.interior_fraction < 1:
            raise ValueError("interior_fraction must lie strictly between 0 and 1")


@dataclass(frozen=True)
class Hypothesis:
    position: tuple[float, float]
    radius: float
    direction_deg: float | None
    weight: float


def _position_samples(hull, count, fraction):
    if not hull:
        return []
    center, _ = minimum_enclosing_circle(hull)
    points = [tuple(center)]
    for i in range(count-1):
        vertex = hull[(i*len(hull))//(count-1)]
        q = (center[0]*(1-fraction)+vertex[0]*fraction,
             center[1]*(1-fraction)+vertex[1]*fraction)
        if q not in points:
            points.append(q)
    return points


def compatible_hypotheses(knowledge, config=None):
    """Filter whole fixed (g,R,u) states through the complete observation ledger.

    Each g/R pair has equal base grid weight. Directional component prior mass
    is split across its orientation grid; 24 directions do not count as 24 times
    the omni prior. Filtering and one global renormalization then update masses.
    These are deterministic grid weights, not a calibrated probability posterior.
    """
    config = config or NoSignalConfig()
    positions = _position_samples(knowledge.hull, config.position_sample_count,
                                  config.interior_fraction)
    prior = config.directional_prior if knowledge.problem == 4 else 0.
    grids = len(positions)*len(config.radius_samples)
    raw = []
    evaluated = 0
    if grids:
        for g in positions:
            for radius in config.radius_samples:
                if prior < 1:
                    evaluated += 1
                    if knowledge.compatible_hidden_state(g, radius, None):
                        raw.append(Hypothesis(g, radius, None, (1-prior)/grids))
                if prior > 0:
                    for i in range(config.orientation_sample_count):
                        direction = 360.*i/config.orientation_sample_count
                        evaluated += 1
                        if knowledge.compatible_hidden_state(g, radius, direction):
                            raw.append(Hypothesis(g, radius, direction,
                                prior/(grids*config.orientation_sample_count)))
    total = math.fsum(h.weight for h in raw)
    samples = ([Hypothesis(h.position, h.radius, h.direction_deg, h.weight/total)
                for h in raw] if total > 0 else [])
    summary = {
        "position_grid_count": len(positions), "radius_grid_count": len(config.radius_samples),
        "orientation_grid_count": config.orientation_sample_count,
        "evaluated_states": evaluated, "compatible_states": len(samples),
        "compatible_omni_states": sum(h.direction_deg is None for h in samples),
        "compatible_directional_states": sum(h.direction_deg is not None for h in samples),
        "directional_prior": prior,
        "directional_weight_after_filter": math.fsum(h.weight for h in samples if h.direction_deg is not None),
        "compatible_prior_grid_mass": total,
        "weight_interpretation": "finite-grid heuristic; not certified posterior probabilities",
        "fixed_state_filter": "one fixed g,R,u explains all historical observations and failed clears",
    }
    return samples, summary


def branch_probabilities(hypotheses, point):
    """Heuristic weighted outcome fractions at a proposed measurement point."""
    weights = {"direction": [], "near": [], "no_signal": []}
    for h in hypotheses:
        d = distance(h.position, point)
        visible = d <= h.radius
        if h.direction_deg is not None and d > 1e-12:
            angle = math.radians(h.direction_deg)
            visible = visible and ((point[0]-h.position[0])*math.cos(angle)
                                   +(point[1]-h.position[1])*math.sin(angle) >= -1e-8)
        outcome = "no_signal" if not visible else "near" if d <= 5 else "direction"
        weights[outcome].append(h.weight)
    result = {key: math.fsum(value) for key, value in weights.items()}
    total = math.fsum(result.values())
    if total <= 0:
        return None
    return {key: value/total for key, value in result.items()}


def _fraction_point(point):
    return Fraction(float(point[0])), Fraction(float(point[1]))


def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def _positive_hull(points):
    # Exact predicates on submitted finite float coordinates. An outward
    # tolerance here would falsely certify visibility just outside the hull.
    points = sorted(set(_fraction_point(point) for point in points))
    if len(points) <= 1:
        return points
    lower, upper = [], []
    for p in points:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(points):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1]+upper[:-1]


def _positive_hull_contains(hull, point):
    q = _fraction_point(point)
    if not hull:
        return False
    if len(hull) == 1:
        return q == hull[0]
    if len(hull) == 2:
        a, b = hull
        return (_cross(a, b, q) == 0 and
                min(a[0], b[0]) <= q[0] <= max(a[0], b[0]) and
                min(a[1], b[1]) <= q[1] <= max(a[1], b[1]))
    return all(_cross(a, b, q) >= 0 for a, b in zip(hull, hull[1:]+hull[:1]))


def positive_convex_visibility(positive_positions, point):
    """Exact membership in a guaranteed-visible region; outside means unknown."""
    return _positive_hull_contains(_positive_hull(positive_positions), point)


def _fallback_geometry(knowledge):
    if knowledge.first_direction is None:
        return None
    full = optical_fallback_points(knowledge.first_direction)
    retained = [p for p in full if distance_to_polygon(knowledge.hull, p) <= 20.+1e-5]
    if not retained:
        return None
    return {"original_first": full[0], "original_last": full[-1],
            "retained_first": retained[0], "retained_last": retained[-1],
            "retained_count": len(retained),
            "retained_path_length_m": math.fsum(distance(a, b) for a, b in zip(retained, retained[1:]))}


def _fallback_cost(geometry, point):
    reverse = distance(point, geometry["original_last"]) < distance(point, geometry["original_first"])
    first = geometry["retained_last"] if reverse else geometry["retained_first"]
    move = distance(point, first)+geometry["retained_path_length_m"]
    return {"cost_s": move/5+3*geometry["retained_count"]+2,
            "walk_distance_m": move, "optical_attempt_upper_bound": geometry["retained_count"],
            "meaning": "upper bound for immediate finite optical fallback, conditional on the outer-hull invariant"}


def optical_fallback_cost_bound(knowledge, point):
    geometry = _fallback_geometry(knowledge)
    return _fallback_cost(geometry, point) if geometry is not None else None


def choose_measurement_nosignal(knowledge, current, mode="active", bin_width_deg=4.,
                               radius_weight=2., *, config=None):
    config = config or NoSignalConfig()

    def baseline(reason, summary=None):
        point, info = choose_measurement(knowledge, current, mode, bin_width_deg, radius_weight)
        return point, {**info, "sensing_variant": VARIANT, "baseline_fallback_reason": reason,
                       "hypothesis_summary": summary, "selection_changed_from_baseline": False}

    if mode != "active":
        return baseline("variant_only_changes_active_mode")
    # This is exactly the original active candidate list, in its original order.
    candidates = candidate_points(knowledge, current, mode)
    if not candidates:
        return baseline("no_candidate")
    hypotheses, summary = compatible_hypotheses(knowledge, config)
    if not hypotheses:
        return baseline("no_compatible_grid_states", summary)
    geometry = _fallback_geometry(knowledge)
    if geometry is None:
        return baseline("no_certified_fallback_cost_available", summary)
    positive_hull = _positive_hull(knowledge.positive_positions)
    evaluations = []
    for point, certificate in candidates:
        probabilities = branch_probabilities(hypotheses, point)
        visible_certificate = _positive_hull_contains(positive_hull, point)
        if visible_certificate and probabilities["no_signal"] > 1e-12:
            # A numerical/model mismatch must not overwrite an independent
            # geometric fact. Return the original selector for the whole choice.
            return baseline("positive_convex_certificate_sample_conflict", summary)
        radius = direction_outcome_bound(knowledge.hull, point, knowledge.epsilon_deg, bin_width_deg)
        optical = _fallback_cost(geometry, point)
        movement = distance(current, point)/5
        direction_proxy = radius_weight*radius/5
        score = (movement+5+probabilities["direction"]*direction_proxy
                 +probabilities["near"]*5+probabilities["no_signal"]*optical["cost_s"])
        evaluations.append({
            "point": point, "score_s": score,
            "baseline_score_s": movement+5+direction_proxy,
            "direction_posterior_radius_bound_m": radius,
            "distance_guarantee": certificate,
            "branch_probabilities": probabilities,
            "positive_convex_visibility_certified": visible_certificate,
            "cost_terms_s": {"movement": movement, "measurement": 5.,
                             "direction_branch_proxy": direction_proxy, "near_branch_clear": 5.,
                             "no_signal_immediate_fallback": optical["cost_s"]},
            "optical_fallback_bound": optical,
        })
    selected = min(evaluations, key=lambda e: e["score_s"])
    original = min(evaluations, key=lambda e: e["baseline_score_s"])
    probabilities = [e["branch_probabilities"]["no_signal"] for e in evaluations]
    certified = [e["positive_convex_visibility_certified"] for e in evaluations]
    return tuple(selected["point"]), {
        "mode": mode, "sensing_variant": VARIANT, "selected": selected,
        "baseline_selected_point": original["point"],
        "selection_changed_from_baseline": selected["point"] != original["point"],
        "candidate_count": len(evaluations), "evaluations": evaluations,
        "hypothesis_summary": summary,
        "no_signal_probability_min": min(probabilities), "no_signal_probability_max": max(probabilities),
        "convex_visibility_certified_count": sum(certified),
        "convex_visibility_mixed_candidates": any(certified) and not all(certified),
        "score_status": "seconds-valued finite-grid heuristic; only angle and optical bounds are certificates",
        "posterior_claim": "no calibrated probability posterior or joint-feasibility certificate is claimed",
    }


class NoSignalSolver(Solver):
    """Baseline control flow with a different active finite-candidate ranking."""

    def __init__(self, client, config=None, decision_log=None, *, nosignal_config=None):
        super().__init__(client, config, decision_log)
        self.nosignal_config = nosignal_config or NoSignalConfig()
        self.stats.update(nosignal_choices=0, nosignal_changed_choices=0,
                          nosignal_baseline_fallbacks=0, nosignal_convex_mixed_choices=0)

    def _record(self, event, **data):
        if event == "start":
            data.update(sensing_variant=VARIANT, nosignal_config=asdict(self.nosignal_config))
        elif event == "finish":
            data["result"].update(sensing_variant=VARIANT, nosignal_config=asdict(self.nosignal_config))
        super()._record(event, **data)

    def _localize(self, channel):
        # Keep the original loop and all exits unchanged. No global monkeypatch
        # is used; different variants can run concurrently in one interpreter.
        knowledge = self.channels[channel]
        if knowledge.status == "cleared":
            return
        if self._try_certified_clear(knowledge):
            return
        for _ in range(self.config.local_measure_limit):
            q, info = choose_measurement_nosignal(
                knowledge, self.client.position, self.config.sensing,
                self.config.bin_width_deg, self.config.radius_weight,
                config=self.nosignal_config)
            if q is None:
                break
            self.stats["nosignal_choices"] += 1
            self.stats["nosignal_changed_choices"] += int(info["selection_changed_from_baseline"])
            self.stats["nosignal_baseline_fallbacks"] += int("baseline_fallback_reason" in info)
            self.stats["nosignal_convex_mixed_choices"] += int(info.get("convex_visibility_mixed_candidates", False))
            self._record("choose_measurement", target_channel=channel, selection=info)
            self._measure(channel, q, "local_active_sensing")
            if knowledge.status == "cleared" or self._try_certified_clear(knowledge):
                return
        if knowledge.first_direction is None:
            raise InconsistentKnowledge("detected source lacks direction and near was not cleared")
        self.stats["fallback_targets"] += 1
        points = optical_fallback_points(knowledge.first_direction)
        if distance(self.client.position, points[-1]) < distance(self.client.position, points[0]):
            points.reverse()
        attempted = 0
        for point in points:
            if distance_to_polygon(knowledge.hull, point) > 20.+1e-5:
                continue
            attempted += 1
            if self._clear(channel, point, {"type": "optical_grid_attempt", "max_grid_points": 110}):
                return
        raise InconsistentKnowledge(f"finite optical cover exhausted after {attempted} attempts")
