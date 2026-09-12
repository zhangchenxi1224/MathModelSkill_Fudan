"""Certified reception geometry after one Q2 direction observation.

Let the first station be s, the reported bearing alpha, and angular uncertainty
epsilon < 90 degrees.  With no target-domain clipping, hidden targets are
g=s+r*u(theta), 5<r<=1500, |theta-alpha|<=epsilon; the same receiver radius R
must obey max(1000,r)<=R<=1500.  A next station q is reception-safe for every
such (g,R) exactly when it lies in four radius-1000 disks, with centers
s+5*u(alpha+-epsilon) and s+1000*u(alpha+-epsilon).

Continuous proof: for r in [5,1000], squared distance is convex in r, so only
the two radial endpoints need checking.  For r>=1000, distance(q,g)<=r is
equivalent to |q-s|^2<=2*r*(q-s).u(theta), whose tightest radial condition is
r=1000.  Those endpoint disks force both edge-direction projections to be
nonnegative.  Every intermediate direction is a positive combination of the
edge directions with coefficient sum >=1; thus its projection is no smaller
than the smaller edge projection.  No interior angular minimum was omitted.
For epsilon=0 the same statement follows directly without combinations.
Necessity uses r=1000 and the r down-to-5 limit, since the near boundary is
open.  A target-domain restriction can remove witnesses; these four disks
remain sufficient under such clipping but need not give the largest safe set.

Only geometry is implemented here.  Reception does not by itself certify
localization, an optimal measurement, or successful clearance.
"""
from __future__ import annotations

import math

from bsolver.geometry import distance


INNER_M = 5.0
RECEPTION_M = 1000.0


def _finite(value, label):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _point(value, label):
    try:
        x, y = value
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must have exactly two coordinates") from exc
    return _finite(x, label + ".x"), _finite(y, label + ".y")


def _epsilon(value):
    epsilon = _finite(value, "epsilon_deg")
    if not 0.0 <= epsilon < 90.0:
        raise ValueError("epsilon_deg must lie in [0, 90)")
    return epsilon


def _unit(angle_deg):
    angle = math.radians(math.remainder(angle_deg, 360.0))
    return math.cos(angle), math.sin(angle)


def lens_centers(s, alpha, epsilon_deg):
    """Four global disk centers in (inner edges, reception-radius edges) order."""
    station = _point(s, "s")
    alpha = _finite(alpha, "alpha")
    epsilon = _epsilon(epsilon_deg)
    alpha = math.remainder(alpha, 360.0)
    directions = [_unit(alpha - epsilon), _unit(alpha + epsilon)]
    return [(station[0] + radius * u[0], station[1] + radius * u[1])
            for radius in (INNER_M, RECEPTION_M) for u in directions]


def certify_joint_lens(q, s, alpha, epsilon_deg, margin=1e-5):
    """Check all four disks with an outward floating-point guard.

    ``max_distance_m`` is the guarded maximum distance to the four centers;
    ``margin_m`` is 1000 minus that bound, not a target-clearance margin.
    The default requires a strictly interior station.  Rejection says only
    that this sufficient certificate did not pass with the requested margin.
    For rejected points, the four-center maximum need not equal a maximum
    distance over all hidden target angles.
    """
    point, station = _point(q, "q"), _point(s, "s")
    alpha = _finite(alpha, "alpha")
    epsilon = _epsilon(epsilon_deg)
    required = _finite(margin, "margin")
    if required < 0.0:
        raise ValueError("margin must be nonnegative")
    # Work relative to s to avoid adding s to a disk center and then undoing
    # that addition in each distance. A scale-aware outward guard covers the
    # remaining ordinary-float subtraction and trig roundoff.
    relative = point[0] - station[0], point[1] - station[1]
    centers = lens_centers((0.0, 0.0), alpha, epsilon)
    raw_maximum = max(distance(relative, center) for center in centers)
    scale = max(RECEPTION_M, *(abs(v) for v in point + station + relative))
    guard = 32.0 * math.ulp(scale)
    bound = math.nextafter(raw_maximum + guard, math.inf)
    clearance = RECEPTION_M - bound
    return {"certified": clearance >= required, "max_distance_m": bound,
            "margin_m": clearance, "required_margin_m": required,
            "numeric_guard_m": guard}


def radial_limit(phi_relative_deg, epsilon_deg, inner=5.0, reception=1000.0):
    """Maximum nonnegative t for q=s+t*u(alpha+phi) in the closed lens.

    For a disk center r*u(edge), t satisfies
    t^2-2*r*cos(phi-edge)*t+r^2-reception^2<=0.  Every disk contains t=0,
    so its nonnegative interval ends at the quadratic's positive root.
    The minimum of the four analytic roots is the exact real-arithmetic
    boundary; this float proposal must still pass ``certify_joint_lens``
    before use. ``inner`` and ``reception`` generalize just this formula.
    """
    phi = math.remainder(_finite(phi_relative_deg, "phi_relative_deg"), 360.0)
    epsilon = _epsilon(epsilon_deg)
    inner, reception = _finite(inner, "inner"), _finite(reception, "reception")
    if not 0.0 < inner <= reception:
        raise ValueError("radii must satisfy 0 < inner <= reception")
    roots = []
    for edge in (-epsilon, epsilon):
        delta = math.radians(math.remainder(phi - edge, 360.0))
        cosine, sine = math.cos(delta), math.sin(delta)
        # The reception-radius disks touch the first station.  Their positive
        # roots simplify exactly and do not need cancellation-prone sqrt.
        roots.append(2.0 * reception * max(0.0, cosine))
        discriminant = max(0.0, reception * reception - inner * inner * sine * sine)
        roots.append(inner * cosine + math.sqrt(discriminant))
    # Trig evaluation of a right angle may leave a sub-picometre positive root.
    result = max(0.0, min(roots))
    return 0.0 if result <= 32.0 * math.ulp(reception) else result


def generate_candidates(s, alpha, epsilon_deg, angle_step_deg=5,
                        radial_fractions=(.4, .7, 1), margin_m=.02):
    """Generate mirrored radial proposals and certify every returned point.

    Relative angles lie in [-90+epsilon, 90-epsilon].  The outer radial length
    is reduced by ``margin_m`` before fractions are applied.  This radial
    reduction is only a construction heuristic; the final four-disk check
    independently requires a reception margin of at least 1e-5 m.  End rays,
    repeated points, and points within 1 m of s are omitted as appropriate.
    No localization score or hidden simulator state enters this function.
    """
    station = _point(s, "s")
    alpha = math.remainder(_finite(alpha, "alpha"), 360.0)
    epsilon = _epsilon(epsilon_deg)
    step = _finite(angle_step_deg, "angle_step_deg")
    padding = _finite(margin_m, "margin_m")
    if step <= 0.0 or padding < 0.0:
        raise ValueError("angle_step_deg must be positive and margin_m nonnegative")
    try:
        fractions = tuple(_finite(value, "radial_fraction") for value in radial_fractions)
    except TypeError as exc:
        raise ValueError("radial_fractions must be an iterable") from exc
    if any(not 0.0 < value <= 1.0 for value in fractions):
        raise ValueError("radial_fractions must lie in (0, 1]")
    maximum_angle = 90.0 - epsilon
    positive = [k * step for k in range(1, math.floor(maximum_angle / step) + 1)]
    positive.append(maximum_angle)
    angles = sorted({0.0, *positive, *(-angle for angle in positive)})
    candidates, seen = [], set()
    for phi in angles:
        extent = max(0.0, radial_limit(phi, epsilon) - padding)
        direction = _unit(alpha + phi)
        for fraction in fractions:
            t = extent * fraction
            if t < 1.0:
                continue
            point = station[0] + t * direction[0], station[1] + t * direction[1]
            key = round(point[0], 8), round(point[1], 8)
            if key in seen or distance(point, station) < 1.0:
                continue
            if not certify_joint_lens(point, station, alpha, epsilon)["certified"]:
                continue
            seen.add(key)
            candidates.append(point)
    return candidates
