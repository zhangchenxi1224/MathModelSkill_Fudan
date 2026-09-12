"""Budgeted current-channel candidate ranking; not a full POMCP solver."""
from __future__ import annotations

import math
import time

from bsolver.geometry import bearing_deg, distance
from bsolver.sensing import candidate_points
from .belief import (PlanningBudgetExceeded, build_belief, check_budget,
                     normalize, predict_response, response_bin, unique_observations)


DEFAULT_CONFIG = {
    "particle_count": 64, "candidate_count": 8, "planning_budget_s": .20,
    "min_particles": 8, "max_position_proposals": 512,
    "directional_prior": .5, "angle_bin_deg": 2., "seed": 20260911,
    "terminal_weight": 1., "fisher_ridge_m": 1800.,
    "allow_clear": True,
}


def _config(config):
    unknown = set(config or {})-set(DEFAULT_CONFIG)
    if unknown:
        raise ValueError(f"unknown planning configuration keys: {sorted(unknown)}")
    result = {**DEFAULT_CONFIG, **(config or {})}
    if not isinstance(result["allow_clear"], bool):
        raise ValueError("allow_clear must be a bool")
    for name, lo, hi in (("particle_count", 8, 2048), ("candidate_count", 2, 32),
                         ("min_particles", 1, 2048), ("max_position_proposals", 1, 8192)):
        value = result[name]
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise ValueError(f"{name} must be an integer in [{lo},{hi}]")
    for name in ("planning_budget_s", "angle_bin_deg", "fisher_ridge_m"):
        if not math.isfinite(result[name]) or result[name] <= 0:
            raise ValueError(f"{name} must be finite and positive")
    if result["angle_bin_deg"] > 90:
        raise ValueError("angle_bin_deg must be <=90")
    if not math.isfinite(result["terminal_weight"]) or result["terminal_weight"] < 0:
        raise ValueError("terminal_weight must be nonnegative")
    if not math.isfinite(result["directional_prior"]) or not 0 < result["directional_prior"] < 1:
        raise ValueError("directional_prior must lie strictly between 0 and 1 for Q4 mixture support")
    if isinstance(result["seed"], bool) or not isinstance(result["seed"], int):
        raise ValueError("seed must be an integer")
    if result["min_particles"] > result["particle_count"]:
        raise ValueError("min_particles cannot exceed particle_count")
    return result


def _mean_spread(particles):
    mean = (math.fsum(p.position[0]*p.weight for p in particles),
            math.fsum(p.position[1]*p.weight for p in particles))
    variance = math.fsum(p.weight*distance(p.position, mean)**2 for p in particles)
    return mean, math.sqrt(max(0., variance))


def clear_success_probability(particles, point):
    return math.fsum(p.weight for p in particles if distance(p.position, point) <= 20)


def physical_information_gain(particles, point, bin_width_deg=2.):
    """I((g,R,u); response), marginalizing a five-point working error law.

    Error-seed identity is deliberately not an information target. The fixed
    trapezoidal quadrature approximates a bounded uniform error distribution;
    it is not a calibrated sensor likelihood. Historical coordinates instead
    have their observed, deterministic response.
    """
    physical = {}
    for p in particles:
        key = p.position, p.radius, p.direction_deg
        if key in physical:
            representative, weight = physical[key]
            physical[key] = representative, weight+p.weight
        else:
            physical[key] = p, p.weight
    marginal = {}
    conditional_entropy = 0.
    point = tuple(map(float, point))
    for particle, weight in physical.values():
        predicted = predict_response(particle, point)
        histogram = {}
        old_angle = next((angle for old_point, angle in particle.observed_angles if old_point == point), None)
        if predicted[0] != "direction" or old_angle is not None:
            histogram[response_bin(predicted, bin_width_deg)] = 1.
        else:
            bearing = bearing_deg(point, particle.position)
            for offset, probability in ((-1., .125), (-.5, .25), (0., .25), (.5, .25), (1., .125)):
                angle = round((bearing+offset*min(1., particle.epsilon_deg)) % 360, 2) % 360
                label = response_bin(("direction", angle), bin_width_deg)
                histogram[label] = histogram.get(label, 0.)+probability
        conditional_entropy -= weight*math.fsum(p*math.log(p) for p in histogram.values() if p > 0)
        for label, probability in histogram.items():
            marginal[label] = marginal.get(label, 0.)+weight*probability
    response_entropy = -math.fsum(p*math.log(p) for p in marginal.values() if p > 0)
    return max(0., response_entropy-conditional_entropy), {
        "response_entropy_nats": response_entropy,
        "conditional_sensor_entropy_nats": conditional_entropy,
        "physical_state_count": len(physical),
        "information_target": "physical g,R,u; error seed marginalized",
        "sensor_likelihood": "fixed five-point trapezoidal bounded-uniform working quadrature",
        "branches": [{"outcome": label, "probability": probability} for label, probability in marginal.items()],
    }


def action_cost(particles, current, current_channel, channel, action):
    movement = distance(current, action["point"])/5
    if action["kind"] == "measure":
        switching = float(current_channel != channel)
        return movement+5+switching, {"movement": movement, "measurement": 5., "channel_switch": switching}
    probability = clear_success_probability(particles, action["point"])
    # Clear's 2 s is paid on success, and a clear does not change the channel.
    return movement+3+2*probability, {"movement": movement, "clear_attempt": 3.,
                                   "expected_clear_success": 2*probability, "channel_switch": 0.}


def terminal_cost(particles, current):
    """Residual time proxy, deliberately labelled and never used as a bound."""
    if not particles:
        return 0.
    mean, spread = _mean_spread(particles)
    probability = clear_success_probability(particles, mean)
    # One mean-location attempt plus a dispersion-dependent unresolved tail.
    # The tail is a modelling choice, not the certified optical fallback cost.
    return distance(current, mean)/5+3+2*probability+(1-probability)*(10+2*spread/5)


def _action(kind, point):
    return {"kind": kind, "point": tuple(map(float, point))}


def _clear_candidates(particles, current):
    mean, _ = _mean_spread(particles)
    # Medoid-like seeds address a bimodal posterior whose mean is in empty space.
    proposals = [tuple(current), mean]
    step = max(1, len(particles)//6)
    proposals.extend(p.position for p in particles[::step][:6])
    unique = list(dict.fromkeys(proposals))
    unique.sort(key=lambda q: (-clear_success_probability(particles, q), distance(current, q), q))
    return [_action("clear", q) for q in unique[:2] if clear_success_probability(particles, q) > 0]


class _Tree:
    def __init__(self, particles, knowledge, config, deadline):
        self.particles = particles
        self.knowledge = knowledge
        self.config = config
        self.deadline = deadline
        self.response_cache = {}
        self.second_stage_nodes = 0

    def branches(self, particles, action):
        check_budget(self.deadline)
        groups = {}
        point = tuple(action["point"])
        for p in particles:
            if action["kind"] == "clear":
                label = ("success" if distance(p.position, point) <= 20 else "no_target_in_range", None)
            else:
                key = (p.position, p.radius, p.direction_deg, p.error_seed, point)
                if key not in self.response_cache:
                    self.response_cache[key] = response_bin(predict_response(p, point), self.config["angle_bin_deg"])
                label = self.response_cache[key]
            groups.setdefault(label, []).append(p)
        return [(label, math.fsum(p.weight for p in members), normalize(members))
                for label, members in groups.items()]

    def one_step(self, particles, current, current_channel, action):
        cost, terms = action_cost(particles, current, current_channel, self.knowledge.channel, action)
        branches = self.branches(particles, action)
        expected_tail = math.fsum(probability*terminal_cost(posterior, action["point"])
                                 for label, probability, posterior in branches if label[0] != "success")
        return cost+self.config["terminal_weight"]*expected_tail, terms, branches

    def two_step(self, particles, current, current_channel, action, measure_candidates, mode):
        """E[min_a2 cost(a2 | response1)+E[tail | response1,response2]]."""
        cost, terms = action_cost(particles, current, current_channel, self.knowledge.channel, action)
        next_channel = self.knowledge.channel if action["kind"] == "measure" else current_channel
        branches = self.branches(particles, action)
        continuation = 0.
        branch_log = []
        for label, probability, posterior in branches:
            check_budget(self.deadline)
            if label[0] == "success":
                branch_log.append({"outcome": label, "probability": probability, "second_action": None,
                                   "continuation_score_s": 0., "terminal": "cleared"})
                continue
            second = [a for a in measure_candidates if distance(a["point"], action["point"]) >= .05]
            # At most ONE noncertified clear in a two-action tree. After root
            # failure the second action is a measurement, respecting the
            # external per-target clear-attempt cap in the default policy.
            if action["kind"] != "clear" and self.config["allow_clear"]:
                clears = _clear_candidates(posterior, action["point"])
                second = clears if mode == "probe" else clears+second
            second = second[:self.config["candidate_count"]]
            if not second:
                best_value = self.config["terminal_weight"]*terminal_cost(posterior, action["point"])
                best_action, second_outcomes = None, 0
            else:
                evaluated = []
                for candidate in second:
                    self.second_stage_nodes += 1
                    value, _, outcomes = self.one_step(posterior, action["point"], next_channel, candidate)
                    evaluated.append((value, candidate, len(outcomes)))
                best_value, best_action, second_outcomes = min(evaluated, key=lambda item: item[0])
            continuation += probability*best_value
            branch_log.append({"outcome": label, "probability": probability,
                               "posterior_particle_count": len(posterior),
                               "second_action": best_action, "second_outcome_count": second_outcomes,
                               "continuation_score_s": best_value})
        return cost+continuation, {"action": action, "immediate_cost_s": cost,
                                   "cost_terms_s": terms, "branches": branch_log,
                                   "expected_continuation_s": continuation}


def _fisher_score(particles, observations, action, current, current_channel, knowledge, config):
    sigma = math.radians(min(1., knowledge.epsilon_deg))/math.sqrt(3)
    ridge = 1/config["fisher_ridge_m"]**2
    expected_radius = 0.
    expected_gain = 0.
    for p in particles:
        a, b, c = ridge, 0., ridge
        for observation in observations:
            if observation.result != "direction":
                continue
            dx, dy = p.position[0]-observation.position[0], p.position[1]-observation.position[1]
            r2 = max(25., dx*dx+dy*dy)
            gx, gy = -dy/r2/sigma, dx/r2/sigma
            a, b, c = a+gx*gx, b+gx*gy, c+gy*gy
        old_det = max(1e-30, a*c-b*b)
        response = predict_response(p, action["point"])[0]
        if response == "direction":
            dx, dy = p.position[0]-action["point"][0], p.position[1]-action["point"][1]
            r2 = max(25., dx*dx+dy*dy)
            gx, gy = -dy/r2/sigma, dx/r2/sigma
            a, b, c = a+gx*gx, b+gx*gy, c+gy*gy
        det = max(1e-30, a*c-b*b)
        radius = 5. if response == "near" else math.sqrt((a+c)/det)
        expected_radius += p.weight*radius
        expected_gain += p.weight*math.log(det/old_det)
    immediate, terms = action_cost(particles, current, current_channel, knowledge.channel, action)
    score = immediate+config["terminal_weight"]*2*expected_radius/5
    return score, {"action": action, "cost_terms_s": terms, "fisher_rms_proxy_m": expected_radius,
                   "expected_log_determinant_gain": expected_gain,
                   "fisher_status": "local Gaussian geometry proxy for bounded-error working model"}


def choose_action(knowledge, current, current_channel, mode, config=None):
    """Return (measure/clear action, diagnostics), or (None, fallback reason).

    Does not call a robot client, mutate knowledge, certify particles, or rank
    other channels. Caller retains certified clear and finite optical fallback.
    All bounded work must finish before the deadline; partial winners are never
    returned after timeout. Thus deterministic work yields stable choices when
    it completes, while timeout rates remain machine/load dependent.
    """
    started = time.perf_counter()
    cfg = _config(config)
    if mode not in ("fisher", "infogain", "rollout", "probe"):
        raise ValueError(f"unknown planning mode: {mode}")
    deadline = started+cfg["planning_budget_s"]
    info = {"mode": mode, "planner": "current_channel_finite_candidate_working_belief",
            "posterior_is_certificate": False, "scope": "local channel only; not full POMCP",
            "config": {key: cfg[key] for key in DEFAULT_CONFIG}, "evaluations": []}
    try:
        particles, summary = build_belief(knowledge, cfg, deadline)
        info["belief"] = summary
        if not particles:
            info["fallback_reason"] = summary.get("reason", "no_compatible_particles")
            return None, info
        raw_candidates = candidate_points(knowledge, tuple(current), "active")
        check_budget(deadline)
        measures = [_action("measure", point) for point, _ in raw_candidates[:cfg["candidate_count"]]]
        if not measures:
            info["fallback_reason"] = "no_local_measurement_candidates"
            return None, info
        actions = measures
        if mode in ("rollout", "probe") and cfg["allow_clear"]:
            clears = _clear_candidates(particles, current)
            actions = measures[:max(1, cfg["candidate_count"]-len(clears))]+clears
        tree = _Tree(particles, knowledge, cfg, deadline)
        observations = unique_observations(knowledge)
        for action in actions:
            check_budget(deadline)
            if mode == "fisher":
                score, evaluation = _fisher_score(particles, observations, action, current, current_channel, knowledge, cfg)
            elif mode == "infogain":
                gain, information = physical_information_gain(particles, action["point"], cfg["angle_bin_deg"])
                immediate, terms = action_cost(particles, current, current_channel, knowledge.channel, action)
                score = immediate/max(gain, 1e-9)
                evaluation = {"action": action, "information_gain_nats": gain, "cost_terms_s": terms,
                              "score_units": "seconds per nat of discretized physical working-state information",
                              **information}
            else:
                score, evaluation = tree.two_step(particles, current, current_channel, action, measures, mode)
            info["evaluations"].append({**evaluation, "score": score})
        check_budget(deadline)
        best = min(info["evaluations"], key=lambda item: item["score"])
        info.update(selected=best, candidate_count=len(actions), second_stage_action_evaluations=tree.second_stage_nodes,
                    score_status="working-model heuristic; no probability calibration or global optimality claim")
        if best["action"]["kind"] == "clear":
            info["clear_is_probe"] = True
            info["requires_external_per_target_attempt_cap"] = True
        return best["action"], info
    except PlanningBudgetExceeded as exc:
        info["fallback_reason"] = str(exc)
        info["discarded_partial_evaluations"] = len(info["evaluations"])
        return None, info
    finally:
        info["elapsed_s"] = time.perf_counter()-started
