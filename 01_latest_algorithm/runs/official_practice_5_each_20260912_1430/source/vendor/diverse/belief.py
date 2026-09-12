"""Finite working beliefs derived only from a ChannelKnowledge ledger.

The proposal model has continuous support on the compatible position/radius/
orientation interiors. Its finite approximation is neither a certificate nor a
calibrated posterior. No environment, client, hidden target, or learned data is
read here. A particle's source, receiver radius, orientation and spatial error
field stay fixed across the complete history and every planning branch.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import math
import random
import time

from bsolver.geometry import bearing_deg, distance, polygon_centroid, wrap_angle_deg


class PlanningBudgetExceeded(RuntimeError):
    pass


def check_budget(deadline):
    if deadline is not None and time.perf_counter() >= deadline:
        raise PlanningBudgetExceeded("planning_wall_clock_budget_exceeded")


@dataclass(frozen=True)
class Particle:
    position: tuple[float, float]
    radius: float
    direction_deg: float | None
    weight: float
    error_seed: int = 0
    # Historical *responses*, shared by every particle, imply each particle's
    # own residual. Repeated coordinates therefore cannot resample the error.
    observed_angles: tuple = ()
    epsilon_deg: float = 1.0051


def unique_observations(knowledge):
    seen = {}
    for observation in knowledge.observations:
        key = tuple(observation.position)
        if key in seen:
            previous = seen[key]
            if (previous.result != observation.result or
                    (previous.angle is not None and abs(wrap_angle_deg(
                        previous.angle-observation.angle)) > 1e-8)):
                raise ValueError("inconsistent repeated observation")
        seen[key] = observation
    return list(seen.values())


def visible(particle, point):
    d = distance(particle.position, point)
    if d > particle.radius:
        return False
    if particle.direction_deg is None or d <= 1e-12:
        return True
    angle = math.radians(particle.direction_deg)
    return ((point[0]-particle.position[0])*math.cos(angle) +
            (point[1]-particle.position[1])*math.sin(angle)) >= -1e-8


def predict_response(particle, point):
    """Deterministic bounded spatial error in this *working* world, not truth."""
    point = tuple(map(float, point))
    if not visible(particle, point):
        return "no_signal", None
    if distance(particle.position, point) <= 5:
        return "near", None
    for old_point, old_angle in particle.observed_angles:
        if old_point == point:
            return "direction", old_angle
    key = f"{particle.error_seed}:{point[0].hex()}:{point[1].hex()}".encode("ascii")
    draw = int.from_bytes(hashlib.blake2b(key, digest_size=8).digest(), "big") / 2**64
    # 1 degree before the two-decimal response rounding; epsilon is the
    # historical compatibility allowance and may include rounding slack.
    error = (2*draw-1)*min(1., particle.epsilon_deg)
    return "direction", round((bearing_deg(point, particle.position)+error) % 360, 2) % 360


def response_bin(response, bin_width_deg=2.):
    kind, angle = response
    if kind != "direction":
        return kind, None
    bins = max(1, math.ceil(360/bin_width_deg))
    return kind, int((angle % 360)/(360/bins)) % bins


def normalize(particles):
    total = math.fsum(p.weight for p in particles)
    return [replace(p, weight=p.weight/total) for p in particles] if total > 0 else []


def condition_clear(particles, point, success):
    """Failure excludes the entire closed 20 m disk in every particle world."""
    return normalize([p for p in particles if (distance(p.position, point) <= 20) == success])


def condition_measurement(particles, point, outcome_bin, bin_width_deg=2.):
    # Keep seed and historical residuals intact. Repeated hypothetical points
    # thus reproduce exactly the same response, even across separate branches.
    return normalize([p for p in particles
                      if response_bin(predict_response(p, point), bin_width_deg) == outcome_bin])


def _semicircle(center):
    low, high = (center-90) % 360, (center+90) % 360
    return [(low, high)] if low <= high else [(0., high), (low, 360.)]


def _orientation_intervals(position, positives):
    intervals = [(0., 360.)]
    for observation in positives:
        if distance(position, observation.position) <= 1e-12:
            continue
        permitted = _semicircle(bearing_deg(position, observation.position))
        intervals = [(max(a, c), min(b, d)) for a, b in intervals for c, d in permitted
                     if min(b, d) > max(a, c)]
        if not intervals:
            break
    return intervals


def _draw_position(hull, rng):
    """Uniform-area convex fan; lower-dimensional hulls use length/point laws."""
    center = polygon_centroid(hull)
    triangles, total = [], 0.
    for a, b in zip(hull, hull[1:]+hull[:1]):
        area = abs((a[0]-center[0])*(b[1]-center[1]) -
                   (a[1]-center[1])*(b[0]-center[0])) / 2
        total += area
        triangles.append((total, a, b))
    if total <= 1e-20:
        a = min(hull)
        b = max(hull, key=lambda p: distance(p, a))
        return (a[0]+rng.random()*(b[0]-a[0]), a[1]) if a == b else _segment(a, b, rng.random())
    pick = rng.random()*total
    _, a, b = next(item for item in triangles if item[0] >= pick)
    r, s = math.sqrt(rng.random()), rng.random()
    return (center[0]*(1-r)+r*((1-s)*a[0]+s*b[0]),
            center[1]*(1-r)+r*((1-s)*a[1]+s*b[1]))


def _segment(a, b, fraction):
    return a[0]+fraction*(b[0]-a[0]), a[1]+fraction*(b[1]-a[1])


def _draw_interval(intervals, rng):
    total = math.fsum(b-a for a, b in intervals)
    pick = rng.random()*total
    for a, b in intervals:
        if pick < b-a:
            return a+pick, total/360
        pick -= b-a
    return intervals[-1][1], total/360


def build_belief(knowledge, config, deadline=None):
    """Conditional importance sampler for a transparent bounded-uniform model.

    g is uniform on the convex hull proposal, R uniform on [1000,1500],
    Q4 mixes omni with uniform u, and unique-coordinate errors are bounded
    uniform before rounding. The full exact ledger rejects incompatible g.
    For each directional proposal u is sampled conditional on positives;
    all radius-visible negatives then restrict R. This preserves their OR
    (out of radius OR behind u), with one shared R and u throughout history.
    """
    check_budget(deadline)
    observations = unique_observations(knowledge)
    summary = {"prior_label": "bounded_uniform_working_model_not_empirically_calibrated",
               "finite_sample_is_certificate": False,
               "proposal_support": "continuous compatible interiors; finite samples can miss regions",
               "unique_observations": len(observations),
               "fixed_state_filter": "one g,R,u and coordinate-fixed error explains all history",
               "sampler": "conditional_importance_then_systematic_resampling",
               "vendor_reuse": "frozen nosignal_sensing has no joint_feasible_hypotheses"}
    if not knowledge.hull or knowledge.status in ("cleared", "absent"):
        return [], {**summary, "reason": "no_active_feasible_hull"}
    positives = [o for o in observations if o.result != "no_signal"]
    negatives = [o for o in observations if o.result == "no_signal"]
    observed_angles = tuple((tuple(o.position), o.angle) for o in positives if o.result == "direction")
    rng = random.Random(config["seed"] + 1009*knowledge.channel + 9176*knowledge.problem)
    proposal_count = min(config["max_position_proposals"], max(32, 2*config["particle_count"]))
    prior = config["directional_prior"] if knowledge.problem == 4 else 0.
    raw = []
    for index in range(proposal_count):
        check_budget(deadline)
        g = _draw_position(knowledge.hull, rng)
        if distance(g, (0., 0.)) > 1800:
            continue
        if any(distance(g, q) <= 20 for q in knowledge.failed_clear_positions):
            continue
        # All bearing/near constraints are independent of R and u. Checking
        # once here avoids orientation-dependent accidental relaxation.
        if any((o.result == "near" and distance(g, o.position) > 5+1e-8) or
               (o.result == "direction" and (distance(g, o.position) <= 5-1e-8 or
                abs(wrap_angle_deg(bearing_deg(o.position, g)-o.angle)) > knowledge.epsilon_deg+1e-8))
               for o in positives):
            continue
        alternatives = [(None, 1-prior)] if prior < 1 else []
        if prior > 0:
            intervals = _orientation_intervals(g, positives)
            if intervals:
                orientation, fraction = _draw_interval(intervals, rng)
                alternatives.append((orientation, prior*fraction))
        lower = max([1000.] + [distance(g, o.position) for o in positives])
        for orientation, mass in alternatives:
            hypothetical = Particle(g, 1500, orientation, 1)
            upper = min([1500.] + [distance(g, o.position) for o in negatives
                                  if visible(hypothetical, o.position)])
            if upper <= lower:
                continue
            radius = lower + (upper-lower)*rng.random()
            if knowledge.compatible_hidden_state(g, radius, orientation):
                raw.append(Particle(g, radius, orientation, mass*(upper-lower)/500,
                                    config["seed"]+index*2+(orientation is not None),
                                    observed_angles, knowledge.epsilon_deg))
    check_budget(deadline)
    total = math.fsum(p.weight for p in raw)
    summary.update(position_proposals=proposal_count, compatible_proposals=len(raw),
                   compatible_proposal_mass=total/proposal_count,
                   proposal_effective_sample_size=(total**2/math.fsum(p.weight**2 for p in raw) if raw else 0.))
    if not raw or total <= 0:
        return [], {**summary, "reason": "no_compatible_particles"}
    normalized = normalize(raw)
    # Fixed-size systematic resampling limits branch work. Each duplicate gets
    # its own fixed future error field, so noise uncertainty is not suppressed.
    count = config["particle_count"]
    offset = rng.random()/count
    cumulative, cursor = normalized[0].weight, 0
    particles = []
    for i in range(count):
        while offset+i/count > cumulative and cursor < len(normalized)-1:
            cursor += 1
            cumulative += normalized[cursor].weight
        particles.append(replace(normalized[cursor], weight=1/count,
                                 error_seed=normalized[cursor].error_seed+7919*i))
    summary.update(particle_count=len(particles),
                   distinct_physical_states=len({(p.position, p.radius, p.direction_deg) for p in particles}),
                   directional_weight=math.fsum(p.weight for p in particles if p.direction_deg is not None))
    if (len(raw) < config["min_particles"] or
            summary["distinct_physical_states"] < config["min_particles"] or
            summary["proposal_effective_sample_size"] < config["min_particles"]):
        return [], {**summary, "reason": "too_few_effective_compatible_particles"}
    return particles, summary
