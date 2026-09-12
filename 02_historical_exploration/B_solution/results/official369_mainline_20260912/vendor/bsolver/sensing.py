"""Finite-candidate active sensing with deterministic direction-outcome bounds."""
import math
from .geometry import (distance, polygon_centroid, minimum_enclosing_circle,
                       max_distance, clip_bearing, bearing_deg, wrap_angle_deg, contains)


def guaranteed_1000(hull, q, margin=1e-5):
    return max_distance(hull, q) <= 1000. - margin


def candidate_points(knowledge, current, mode="active"):
    """A finite set, not a continuous globally optimal solution.

    In Q3, points are certified in Q_safe, or convex combinations of Q_safe
    points with a previous positive station. Every feasible receiver disk
    contains both endpoints, hence their segment. This uses joint g,R evidence.
    In Q4 this does NOT guarantee angular visibility; finite optical fallback
    remains available and no_signal retains its disjunctive meaning.
    """
    hull = knowledge.hull
    first = knowledge.first_direction
    if first is None:
        return []
    a = math.radians(first.angle)
    u, v = (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))
    s = first.position
    seeds = []
    for along, side in [(750, 450), (750, -450), (700, 400), (700, -400),
                        (800, 400), (800, -400)]:
        seeds.append((s[0]+along*u[0]+side*v[0], s[1]+along*u[1]+side*v[1]))
    center, rad = minimum_enclosing_circle(hull)
    if mode == "fixed":
        remaining = [q for q in seeds if guaranteed_1000(hull, q)
                     and all(distance(q, o.position) >= .05 for o in knowledge.observations)]
        return [min(remaining, key=lambda q: distance(current, q))] if remaining else []
    for rr in [max(25., rad * .4), max(50., rad * .8), min(600., max(100., rad))]:
        for k in range(8):
            th = 2*math.pi*k/8
            seeds.append((center[0]+rr*math.cos(th), center[1]+rr*math.sin(th)))
    safe = [q for q in seeds if guaranteed_1000(hull, q)]
    candidates = [(q, "Q_safe_vertex_certificate") for q in safe]
    for q in safe[:8]:
        for lam in [.25, .5, .75]:
            candidates.append(((s[0]*(1-lam)+q[0]*lam,
                                s[1]*(1-lam)+q[1]*lam), "joint_radius_convexity_certificate"))
    if knowledge.problem == 4:
        # These local proposals may lose visibility. The policy treats that as
        # a possible outcome, not a violation or an absent target.
        candidates += [(q, "visibility_uncertain") for q in seeds]
    result = []
    used = set()
    for q, certificate in candidates:
        key = round(q[0], 6), round(q[1], 6)
        if key in used or distance(q, current) < 1.:
            continue
        if any(distance(q, ob.position) < .05 for ob in knowledge.observations):
            continue
        used.add(key)
        result.append((q, certificate))
    if mode == "estimated":
        # Near the estimated target, larger crossing angle is only a heuristic.
        result.sort(key=lambda item: distance(item[0], center)+.2*distance(item[0], current))
        return result[:1]
    # Cheap deterministic preselection. Only the retained finite candidates
    # participate in the more expensive certified outcome partition.
    result.sort(key=lambda item: distance(item[0], current)+.3*distance(item[0], center))
    return result[:18]


def direction_outcome_bound(hull, q, epsilon_deg=1.0051, bin_width_deg=4.):
    """Upper bound on posterior MEC radius for every direction response.

    Cover every possible measured angle by bins. For an entire response bin,
    enlarge the wedge by epsilon+half-bin-width. Each bin's clipped polygon is
    an outer hull of all true positions yielding a direction within that bin.
    Evaluating a few sampled source points would not provide this guarantee.
    The near branch has radius <=5. Q4 no_signal is separately NOT covered.
    """
    if (not math.isfinite(bin_width_deg) or bin_width_deg <= 0
            or not math.isfinite(epsilon_deg) or epsilon_deg < 0
            or epsilon_deg + bin_width_deg/2 + 1e-7 >= 90):
        raise ValueError("direction bins require positive width and epsilon + width/2 < 90 degrees")
    if not hull:
        return math.inf
    if contains(hull, q):
        lo, hi, reference = -180., 180., 0.
    else:
        reference = bearing_deg(q, polygon_centroid(hull))
        values = [wrap_angle_deg(bearing_deg(q, p)-reference) for p in hull]
        lo, hi = min(values)-epsilon_deg, max(values)+epsilon_deg
        if hi-lo > 180.:
            lo, hi, reference = -180., 180., 0.
    bins = max(1, math.ceil((hi-lo)/bin_width_deg))
    width = (hi-lo)/bins
    worst = 5.
    for k in range(bins):
        theta = reference+lo+(k+.5)*width
        posterior = clip_bearing(hull, q, theta, epsilon_deg+width/2+1e-7)
        if posterior:
            _, r = minimum_enclosing_circle(posterior)
            worst = max(worst, r)
    return worst


def choose_measurement(knowledge, current, mode="active", bin_width_deg=4., radius_weight=2.):
    candidates = candidate_points(knowledge, current, mode)
    if not candidates:
        return None, {"reason": "no_candidate"}
    if mode == "fixed":
        q = candidates[0]
        return q, {"mode": mode, "guarantee": "Q_safe_vertex_certificate",
                   "max_distance_m": max_distance(knowledge.hull, q),
                   "candidate_count": len(candidates)}
    evaluations = []
    for q, certificate in candidates:
        r = direction_outcome_bound(knowledge.hull, q, knowledge.epsilon_deg, bin_width_deg)
        # A time-valued surrogate, NOT a proven remaining-time upper bound.
        score = distance(current, q)/5+5+radius_weight*r/5
        evaluations.append({"point": q, "score_s": score,
                            "direction_posterior_radius_bound_m": r,
                            "distance_guarantee": certificate})
    best = min(evaluations, key=lambda e: e["score_s"])
    return tuple(best["point"]), {"mode": mode, "selected": best, "candidate_count": len(evaluations),
                                "evaluations": evaluations,
                                "no_signal": "impossible under Q3 distance certificate; retained possible in Q4",
                                "score_status": "time-valued heuristic; bound certifies direction branches only"}


def optical_fallback_points(first_observation):
    """110 centers cover the entire initial 1500m x ±26.32m bearing strip.

    Grid longitudinal spacing 28m, transverse rows ±14m give covering radius
    <=sqrt(14²+14²)<20. Includes endpoint 1512 to cover [0,1500].
    """
    a = math.radians(first_observation.angle)
    u, v = (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))
    s = first_observation.position
    out = []
    for row, side in enumerate([-14., 14.]):
        xs = list(range(0, 1513, 28))
        if row:
            xs.reverse()
        for x in xs:
            out.append((s[0]+x*u[0]+side*v[0], s[1]+x*u[1]+side*v[1]))
    return out
