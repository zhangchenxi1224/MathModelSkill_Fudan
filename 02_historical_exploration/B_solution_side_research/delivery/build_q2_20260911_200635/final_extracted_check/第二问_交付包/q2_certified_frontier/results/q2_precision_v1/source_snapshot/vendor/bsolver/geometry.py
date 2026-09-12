"""Conservative 2-D geometry for CUMCM 2026 B.

Coordinates are metres; bearing angles are degrees counterclockwise from east.
Polygons are convex, in boundary order, without a duplicated closing vertex.
One/two vertices represent a point/segment. Empty sets never certify a clear.

The strategy path uses outward-padded floating-point clipping. The independent
Q1 classifier uses exact rational predicates on the supplied finite floats and
never substitutes a large bounding box for an unbounded intersection.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
import random
from typing import Iterable, Sequence

Point = tuple[float, float]
Polygon = list[Point]
Halfplane = tuple[Point, float]
DEFAULT_BEARING_EPSILON_DEG = 1.0051
_MACHINE_EPS = 2.220446049250313e-16


def _point(p: Sequence[float]) -> Point:
    if len(p) != 2:
        raise ValueError("a point must have two coordinates")
    q = (float(p[0]), float(p[1]))
    if not all(math.isfinite(v) for v in q):
        raise ValueError("coordinates must be finite")
    return q


def _points(points: Iterable[Sequence[float]]) -> Polygon:
    return [_point(p) for p in points]


def add(a: Point, b: Point) -> Point:
    return a[0] + b[0], a[1] + b[1]


def sub(a: Point, b: Point) -> Point:
    return a[0] - b[0], a[1] - b[1]


def scale(a: Point, factor: float) -> Point:
    return a[0] * factor, a[1] * factor


def dot(a: Point, b: Point) -> float:
    return math.fsum((a[0] * b[0], a[1] * b[1]))


def cross(a: Point, b: Point) -> float:
    return math.fsum((a[0] * b[1], -a[1] * b[0]))


def norm(a: Point) -> float:
    return math.hypot(*a)


def distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def wrap_angle_deg(angle: float) -> float:
    """Map a finite angle to [-180, 180)."""
    if not math.isfinite(angle):
        raise ValueError("angle must be finite")
    return (float(angle) + 180.0) % 360.0 - 180.0


def unit_from_deg(angle: float) -> Point:
    a = math.radians(wrap_angle_deg(angle))
    return math.cos(a), math.sin(a)


def bearing_deg(origin: Point, target: Point) -> float:
    """Return a bearing in [0, 360); coincident points have no bearing."""
    if origin == target:
        raise ValueError("coincident points do not define a bearing")
    return math.degrees(math.atan2(target[1] - origin[1], target[0] - origin[0])) % 360.0


def _roundoff_guard(points: Sequence[Point], extra: float = 0.0) -> float:
    magnitude = max([1.0, abs(extra)] + [abs(x) for p in points for x in p])
    return 64.0 * _MACHINE_EPS * magnitude


def _dedup_boundary(poly: Polygon) -> Polygon:
    # Never merge merely close extreme points: that can shrink an outer bound.
    out: Polygon = []
    for p in poly:
        if not out or p != out[-1]:
            out.append(p)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def disk_outer_polygon(center: Point, radius: float, n: int = 128) -> Polygon:
    """Return a CCW circumscribed regular polygon, never an inscribed one.

    Its supporting lines have distance at least ``radius`` from ``center``.
    The tiny outward numerical pad is in addition to the sec(pi/n) correction.
    """
    center = _point(center)
    radius = float(radius)
    if not math.isfinite(radius) or radius < 0:
        raise ValueError("radius must be finite and nonnegative")
    if not isinstance(n, int) or n < 3:
        raise ValueError("n must be an integer at least 3")
    if radius == 0:
        return [center]
    pad = max(1e-9, _roundoff_guard([center], radius))
    circumradius = (radius + pad) / math.cos(math.pi / n)
    return [
        (center[0] + circumradius * math.cos((2 * j + 1) * math.pi / n),
         center[1] + circumradius * math.sin((2 * j + 1) * math.pi / n))
        for j in range(n)
    ]


def clip_halfplane(poly: Sequence[Point], normal: Point, offset: float,
                   tol: float = 1e-9) -> Polygon:
    """Clip a convex polygon by normal dot x <= offset, padded outwards.

    ``tol`` is a nonnegative distance in metres, independent of normal length.
    A coordinate-scale roundoff guard is added; tolerance does not move a
    boundary inward. The resulting polygon bounds the intersection of the
    supplied polygon and the original halfplane.
    """
    points = _dedup_boundary(_points(poly))
    normal = _point(normal)
    offset = float(offset)
    if not math.isfinite(offset) or not math.isfinite(tol) or tol < 0:
        raise ValueError("offset/tolerance must be finite and tolerance nonnegative")
    if not points:
        return []
    length = norm(normal)
    if length == 0:
        return points if offset >= 0 else []
    n = scale(normal, 1.0 / length)
    b = offset / length
    b += tol + _roundoff_guard(points, b)

    def residual(p: Point) -> float:
        return dot(n, p) - b

    out: Polygon = []
    previous = points[-1]
    fp = residual(previous)
    for current in points:
        fc = residual(current)
        previous_inside, current_inside = fp <= 0, fc <= 0
        if previous_inside != current_inside:
            t = fp / (fp - fc)
            # Opposite signs imply 0 <= t <= 1. Clamping only counters rounding.
            t = min(1.0, max(0.0, t))
            q = (previous[0] + t * (current[0] - previous[0]),
                 previous[1] + t * (current[1] - previous[1]))
            out.append(q)
        if current_inside:
            out.append(current)
        previous, fp = current, fc
    return _dedup_boundary(out)


def bearing_halfplanes(s: Point, theta_deg: float,
                       epsilon_deg: float = DEFAULT_BEARING_EPSILON_DEG) -> list[Halfplane]:
    """Return the two forward-wedge sides plus an explicit forward constraint.

    The official error is +/-1 degree, and Attachment 2 specifies two decimal
    places for the returned angle. 1.0051 conservatively adds a possible
    0.005-degree rounding error plus 0.0001-degree numerical slack. Whether the
    stated +/-1 bound already includes rounding is not unambiguously separated;
    1.0051 is our protective allowance, not an official exact error bound.
    epsilon in [0,90) makes a convex forward wedge; epsilon=0 gives a ray.
    """
    s = _point(s)
    if not math.isfinite(epsilon_deg) or not 0 <= epsilon_deg < 90:
        raise ValueError("bearing half-width must be in [0, 90) degrees")
    direction = unit_from_deg(theta_deg)
    lower = unit_from_deg(theta_deg - epsilon_deg)
    upper = unit_from_deg(theta_deg + epsilon_deg)
    # cross(lower, x-s) >= 0, cross(upper, x-s) <= 0.
    normals = [(lower[1], -lower[0]), (-upper[1], upper[0]),
               (-direction[0], -direction[1])]
    return [(n, dot(n, s)) for n in normals]


def clip_bearing(poly: Sequence[Point], s: Point, theta_deg: float,
                 epsilon_deg: float = DEFAULT_BEARING_EPSILON_DEG,
                 tol: float = 1e-9) -> Polygon:
    result = _points(poly)
    for n, b in bearing_halfplanes(s, theta_deg, epsilon_deg):
        result = clip_halfplane(result, n, b, tol=tol)
        if not result:
            break
    return result


def clip_disk_outer(poly: Sequence[Point], center: Point, radius: float,
                    n: int = 128, tol: float = 1e-9) -> Polygon:
    """Intersect with a circumscribed disk approximation using tangent lines."""
    center = _point(center)
    if not math.isfinite(radius) or radius < 0 or not isinstance(n, int) or n < 3:
        raise ValueError("invalid disk parameters")
    result = _points(poly)
    for j in range(n):
        normal = unit_from_deg(j * 360.0 / n)
        result = clip_halfplane(result, normal, dot(normal, center) + radius, tol)
        if not result:
            break
    return result


def polygon_diameter(poly: Sequence[Point]) -> float:
    """Maximum pairwise vertex distance; empty set convention is 0."""
    points = _points(poly)
    return max((distance(p, q) for i, p in enumerate(points) for q in points[:i]), default=0.0)


def max_distance(poly: Sequence[Point], point: Point) -> float:
    """Farthest distance over a polygon equals its farthest vertex distance.

    Empty input returns infinity, preventing vacuous safety certification.
    """
    point = _point(point)
    return max((distance(point, _point(v)) for v in poly), default=math.inf)


def distance_to_segment(point: Point, a: Point, b: Point) -> float:
    ab = sub(b, a)
    denom = dot(ab, ab)
    if denom == 0:
        return distance(point, a)
    t = max(0.0, min(1.0, dot(sub(point, a), ab) / denom))
    return distance(point, add(a, scale(ab, t)))


def contains(poly: Sequence[Point], point: Point, tol: float = 1e-8) -> bool:
    """Membership in a convex polygon (either orientation), point or segment.

    ``tol`` is a diagnostic distance tolerance, not an expansion of stored
    vertices. Safety decisions must use ``certify_clear``, not membership.
    """
    points = _dedup_boundary(_points(poly))
    point = _point(point)
    if tol < 0 or not math.isfinite(tol):
        raise ValueError("tolerance must be finite and nonnegative")
    if not points:
        return False
    if len(points) == 1:
        return distance(points[0], point) <= tol
    if len(points) == 2:
        return distance_to_segment(point, points[0], points[1]) <= tol
    positive = negative = False
    for a, b in zip(points, points[1:] + points[:1]):
        edge = sub(b, a)
        z = cross(edge, sub(point, a))
        threshold = tol * norm(edge)
        positive |= z > threshold
        negative |= z < -threshold
        if positive and negative:
            return False
    if not positive and not negative:
        # All-collinear polygons must not accidentally mean the entire plane.
        return min(distance_to_segment(point, a, b)
                   for a, b in zip(points, points[1:] + points[:1])) <= tol
    return True


def distance_to_polygon(poly: Sequence[Point], point: Point) -> float:
    points = _dedup_boundary(_points(poly))
    point = _point(point)
    if not points:
        return math.inf
    if contains(points, point, tol=0.0):
        return 0.0
    if len(points) == 1:
        return distance(point, points[0])
    return min(distance_to_segment(point, a, b)
               for a, b in zip(points, points[1:] + points[:1]))


def polygon_centroid(poly: Sequence[Point]) -> Point:
    """Area centroid; midpoint/mean fallback for degenerate convex polygons."""
    points = _dedup_boundary(_points(poly))
    if not points:
        raise ValueError("empty polygon has no centroid")
    if len(points) == 1:
        return points[0]
    anchor = points[0]
    local = [sub(p, anchor) for p in points]
    z = [cross(a, b) for a, b in zip(local, local[1:] + local[:1])]
    twice_area = math.fsum(z)
    if twice_area == 0:
        return (math.fsum(p[0] for p in points) / len(points),
                math.fsum(p[1] for p in points) / len(points))
    cx = math.fsum((a[0] + b[0]) * v for a, b, v in zip(local, local[1:] + local[:1], z))
    cy = math.fsum((a[1] + b[1]) * v for a, b, v in zip(local, local[1:] + local[:1], z))
    return add(anchor, (cx / (3 * twice_area), cy / (3 * twice_area)))


def _diameter_circle(a: Point, b: Point) -> tuple[Point, float]:
    center = (a[0] + (b[0] - a[0]) / 2, a[1] + (b[1] - a[1]) / 2)
    return center, max(distance(center, a), distance(center, b))


def _circumcircle(a: Point, b: Point, c: Point) -> tuple[Point, float] | None:
    # Translation keeps the common 1800 m search coordinates well conditioned.
    bx, by = sub(b, a)
    cx, cy = sub(c, a)
    determinant = 2.0 * (bx * cy - by * cx)
    if abs(determinant) <= 1e-13 * max(1.0, norm((bx, by)) * norm((cx, cy))):
        # Exact fallback handles nearly parallel triples without an arbitrary
        # collinearity tolerance throwing away a legitimate support point.
        axf, ayf = map(Fraction, a)
        bxf, byf = Fraction(b[0]) - axf, Fraction(b[1]) - ayf
        cxf, cyf = Fraction(c[0]) - axf, Fraction(c[1]) - ayf
        detf = 2 * (bxf * cyf - byf * cxf)
        if not detf:
            return None
        bb, cc = bxf * bxf + byf * byf, cxf * cxf + cyf * cyf
        center = (float(axf + (cyf * bb - byf * cc) / detf),
                  float(ayf + (bxf * cc - cxf * bb) / detf))
    else:
        bb, cc = bx * bx + by * by, cx * cx + cy * cy
        center = (a[0] + (cy * bb - by * cc) / determinant,
                  a[1] + (bx * cc - cx * bb) / determinant)
    if not all(math.isfinite(v) for v in center):
        return None
    return center, max(distance(center, p) for p in (a, b, c))


def _in_circle(p: Point, circle: tuple[Point, float]) -> bool:
    center, radius = circle
    return distance(p, center) <= radius + _roundoff_guard([p, center], radius)


def _circle_two_support(points: Sequence[Point], p: Point, q: Point) -> tuple[Point, float]:
    diameter = _diameter_circle(p, q)
    pq = sub(q, p)
    left: tuple[Point, float] | None = None
    right: tuple[Point, float] | None = None
    for r in points:
        if _in_circle(r, diameter):
            continue
        side = cross(pq, sub(r, p))
        circle = _circumcircle(p, q, r)
        if circle is None:
            continue
        center_side = cross(pq, sub(circle[0], p))
        if side > 0 and (left is None or center_side > cross(pq, sub(left[0], p))):
            left = circle
        elif side < 0 and (right is None or center_side < cross(pq, sub(right[0], p))):
            right = circle
    if left is None and right is None:
        return diameter
    if left is None:
        return right  # type: ignore[return-value]
    if right is None:
        return left
    return left if left[1] <= right[1] else right


def _circle_one_support(points: Sequence[Point], p: Point) -> tuple[Point, float]:
    circle = (p, 0.0)
    for i, q in enumerate(points):
        if not _in_circle(q, circle):
            circle = _diameter_circle(p, q) if circle[1] == 0 else _circle_two_support(points[:i + 1], p, q)
    return circle


def minimum_enclosing_circle(poly: Sequence[Point]) -> tuple[Point, float]:
    """Deterministic-shuffle incremental minimum enclosing circle.

    Returns a slightly inflated radius so all *supplied* vertices are enclosed.
    Degenerate points/segments/collinear sets are supported. Empty input raises.
    The radius is minimal to floating-point tolerance; a final maximum-distance
    pass makes a roundoff containment failure visible in the returned radius.
    """
    points = list(dict.fromkeys(_points(poly)))
    if not points:
        raise ValueError("empty polygon has no enclosing circle")
    if len(points) == 1:
        return points[0], 0.0
    random.Random(0xC0DE2026).shuffle(points)
    circle: tuple[Point, float] | None = None
    for i, p in enumerate(points):
        if circle is None or not _in_circle(p, circle):
            circle = _circle_one_support(points[:i + 1], p)
    assert circle is not None
    center = circle[0]
    radius = max_distance(points, center)
    return center, math.nextafter(radius + _roundoff_guard(points, radius), math.inf)


def _circle_intersections(a: Point, b: Point, radius: float) -> Polygon:
    d = distance(a, b)
    if d == 0 or d > 2 * radius:
        return []
    midpoint = (a[0] + (b[0] - a[0]) / 2, a[1] + (b[1] - a[1]) / 2)
    h2 = (radius - d / 2) * (radius + d / 2)
    if h2 < 0:
        return []
    h = math.sqrt(h2)
    perpendicular = (-(b[1] - a[1]) / d, (b[0] - a[0]) / d)
    first = add(midpoint, scale(perpendicular, h))
    return [first] if h == 0 else [first, add(midpoint, scale(perpendicular, -h))]


def certify_clear(poly: Sequence[Point], point: Point, radius: float = 20.0,
                  margin: float = 1e-6) -> bool:
    """Certify every point in the supplied outer polygon is within range.

    Uses a physical distance margin and a coordinate-scale arithmetic guard.
    It does not certify an incorrectly constructed or stale feasible region.
    """
    points = _points(poly)
    point = _point(point)
    if not math.isfinite(radius) or not math.isfinite(margin) or radius < 0 or margin < 0:
        raise ValueError("radius and margin must be finite and nonnegative")
    if not points:
        return False
    return max_distance(points, point) + _roundoff_guard(points + [point], radius) <= radius - margin


def nearest_safe_clear(poly: Sequence[Point], current: Point,
                       radius: float = 19.999) -> Point | None:
    """Closest point in the intersection of radius-disks around all vertices.

    The result minimizes movement over this conservative sufficient-clear set,
    up to arithmetic tolerance. None means no certified candidate was found;
    never use a polygon's diameter/2 as a substitute for this condition.
    Caller should run certify_clear again on the final transmitted coordinates.
    """
    points = list(dict.fromkeys(_points(poly)))
    current = _point(current)
    if not math.isfinite(radius) or radius < 0:
        raise ValueError("radius must be finite and nonnegative")
    if not points:
        return None
    if max_distance(points, current) <= radius:
        return current
    center, _ = minimum_enclosing_circle(points)
    center_radius = max_distance(points, center)
    guard = _roundoff_guard(points + [current, center], radius)
    if center_radius > radius + guard:
        return None
    candidates: Polygon = [center]
    for vertex in points:
        d = distance(current, vertex)
        if d > 0:
            candidates.append(add(vertex, scale(sub(current, vertex), radius / d)))
    for i, a in enumerate(points):
        for b in points[:i]:
            candidates.extend(_circle_intersections(a, b, radius))
    valid: Polygon = []
    for candidate in candidates:
        farthest = max_distance(points, candidate)
        if farthest > radius + guard:
            continue
        if farthest > radius:
            # Bring an almost-feasible arc intersection into the convex feasible
            # region using a known strict interior point. Never relax radius.
            if center_radius >= radius:
                continue
            weight = min(1.0, (farthest - radius + guard) / (farthest - center_radius))
            candidate = add(scale(candidate, 1.0 - weight), scale(center, weight))
        if max_distance(points, candidate) <= radius:
            valid.append(candidate)
    return min(valid, key=lambda p: distance(p, current)) if valid else None


@dataclass(frozen=True)
class HalfplaneIntersection:
    """Classification of an intersection without an artificial bounding box.

    ``kind``: empty, unbounded, point, segment, polygon. Unbounded lines and
    rays have dimension 1; wedges/strips/halfplanes/the plane dimension 2.
    ``vertices`` are finite extreme points, not a bounding polygon when
    unbounded. Numerical vertex coordinates are rounded from exact fractions.
    """
    kind: str
    vertices: Polygon
    feasible_point: Point | None
    dimension: int
    bounded: bool
    recession_directions: Polygon


def _fraction_hull(points: Iterable[tuple[Fraction, Fraction]]) -> list[tuple[Fraction, Fraction]]:
    pts = sorted(set(points))
    if len(pts) <= 1:
        return pts

    def turn(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and turn(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and turn(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def classify_halfplanes(halfplanes: Iterable[Halfplane], tol: float = 1e-9) -> HalfplaneIntersection:
    """Classify finite-float halfplanes by exact rational predicates.

    Each pair is (normal, offset) with normal dot x <= offset. A zero normal
    with negative offset is impossible. ``tol`` is accepted for API symmetry
    but does NOT relax inequalities or change classification; this avoids
    making a thin infeasible set look feasible or a point look like a polygon.
    Complexity is O(m^3) in the worst case, intended for modest Q1 input sizes.
    For repeated strategy updates use conservative polygon clipping instead.
    """
    if not math.isfinite(tol) or tol < 0:
        raise ValueError("tolerance must be finite and nonnegative")
    constraints: list[tuple[Fraction, Fraction, Fraction]] = []
    empty = HalfplaneIntersection("empty", [], None, -1, True, [])
    for normal, offset in halfplanes:
        nx, ny = _point(normal)
        offset = float(offset)
        if not math.isfinite(offset):
            raise ValueError("offset must be finite")
        a, b, c = Fraction(nx), Fraction(ny), Fraction(offset)
        if a == b == 0:
            if c < 0:
                return empty
        else:
            constraints.append((a, b, c))
    constraints = list(dict.fromkeys(constraints))
    zero = (Fraction(0), Fraction(0))

    def feasible(p):
        return all(a * p[0] + b * p[1] <= c for a, b, c in constraints)

    witnesses: list[tuple[Fraction, Fraction]] = []
    if feasible(zero):
        witnesses.append(zero)
    vertices: list[tuple[Fraction, Fraction]] = []
    for i, (a, b, c) in enumerate(constraints):
        projection = (a * c / (a * a + b * b), b * c / (a * a + b * b))
        if feasible(projection):
            witnesses.append(projection)
        for d, e, f in constraints[:i]:
            determinant = a * e - b * d
            if determinant == 0:
                continue
            p = ((c * e - b * f) / determinant, (a * f - c * d) / determinant)
            if feasible(p):
                vertices.append(p)
                witnesses.append(p)
    if not witnesses:
        return empty

    directions = [(Fraction(1), Fraction(0)), (Fraction(-1), Fraction(0)),
                  (Fraction(0), Fraction(1)), (Fraction(0), Fraction(-1))]
    for a, b, _ in constraints:
        directions.extend([(-b, a), (b, -a)])
    recession = []
    for p in directions:
        if all(a * p[0] + b * p[1] <= 0 for a, b, _ in constraints):
            magnitude = max(abs(p[0]), abs(p[1]))
            q = (p[0] / magnitude, p[1] / magnitude)
            if q not in recession:
                recession.append(q)
    anchor = min(witnesses, key=lambda p: p[0] * p[0] + p[1] * p[1])
    vectors = [(p[0] - anchor[0], p[1] - anchor[1]) for p in witnesses] + recession
    vectors = [v for v in vectors if v != zero]
    dimension = 0
    if vectors:
        dimension = 1
        first = vectors[0]
        if any(first[0] * v[1] - first[1] * v[0] != 0 for v in vectors[1:]):
            dimension = 2
    bounded = not recession
    kind = "unbounded" if not bounded else ("point" if dimension == 0 else "segment" if dimension == 1 else "polygon")
    hull = _fraction_hull(vertices)
    if bounded and dimension == 0 and not hull:
        hull = [anchor]
    return HalfplaneIntersection(
        kind=kind,
        vertices=[(float(p[0]), float(p[1])) for p in hull],
        feasible_point=(float(anchor[0]), float(anchor[1])),
        dimension=dimension,
        bounded=bounded,
        recession_directions=[scale((float(v[0]), float(v[1])),
                                    1.0 / math.hypot(float(v[0]), float(v[1])))
                               for v in recession],
    )


def intersect_bearings(observations: Iterable[tuple[Point, float]],
                        epsilon_deg: float = 1.0) -> HalfplaneIntersection:
    """Q1 exact-classification helper; default uses the stated physical bound.

    Unlike clip_bearing's operational default, this analytic helper uses the
    stated +/-1 bound without an additional rounding allowance unless requested.
    """
    halfplanes: list[Halfplane] = []
    for station, theta in observations:
        halfplanes.extend(bearing_halfplanes(station, theta, epsilon_deg))
    return classify_halfplanes(halfplanes)
