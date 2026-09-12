"""Observation-only local refinement, separate from every frozen policy module.

The default changes only the finite optical cover.  Adaptive mode additionally
compares conservative, seconds-valued continuation bounds; it never treats a
sampled visibility probability as a safety or completion certificate.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
import math

from .geometry import (certify_clear, clip_bearing, distance, max_distance,
                       minimum_enclosing_circle, nearest_safe_clear)
from .knowledge import InconsistentKnowledge
from .nosignal_sensing import (NoSignalSolver, choose_measurement_nosignal,
                               positive_convex_visibility)


VARIANT = "current_hull_cell_cover_v1"
Point = tuple[float, float]
ExactPoint = tuple[Fraction, Fraction]
_INTERSECTION_PAD_M = Fraction("0.0000001")
_FAILURE_MARGIN_M = Fraction("0.000001")
_ACTION_TIMING_GUARD_S = 1e-6


@dataclass(frozen=True)
class RefinedLocalConfig:
    cell_side_m: float = 28.0
    max_extra_measures: int = 5
    min_bound_gain_s: float = 0.0
    direction_bin_width_deg: float = 30.0

    def __post_init__(self):
        if not math.isfinite(self.cell_side_m) or not 1 <= self.cell_side_m <= 28.:
            raise ValueError("cell_side_m must be in [1,28] metres")
        if (isinstance(self.max_extra_measures, bool)
                or not isinstance(self.max_extra_measures, int)
                or not 0 <= self.max_extra_measures <= 5):
            raise ValueError("max_extra_measures must be an integer in [0,5]")
        if not math.isfinite(self.min_bound_gain_s) or self.min_bound_gain_s < 0:
            raise ValueError("min_bound_gain_s must be finite and nonnegative")
        if (not math.isfinite(self.direction_bin_width_deg)
                or not 5 <= self.direction_bin_width_deg <= 60):
            raise ValueError("direction_bin_width_deg must be in [5,60]")


def _exact_point(point):
    if len(point) != 2 or not all(math.isfinite(float(x)) for x in point):
        raise ValueError("points must contain two finite coordinates")
    return Fraction(float(point[0])), Fraction(float(point[1]))


def _convex_hull(points):
    points = sorted(set(points))
    if len(points) <= 1:
        return points

    def cross(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])

    lower, upper = [], []
    for p in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1]+upper[:-1]


def _clip_axis(poly, axis, boundary, keep_less):
    """Closed rational half-plane clipping, including point/segment cases."""
    if not poly:
        return []
    out = []
    previous = poly[-1]
    old = previous[axis]-boundary
    for current in poly:
        new = current[axis]-boundary
        old_in = old <= 0 if keep_less else old >= 0
        new_in = new <= 0 if keep_less else new >= 0
        if old_in != new_in:
            t = old/(old-new)
            out.append(tuple(previous[k]+t*(current[k]-previous[k]) for k in (0, 1)))
        if new_in:
            out.append(current)
        previous, old = current, new
    return list(dict.fromkeys(out))


def _square_distance(a, b):
    return (a[0]-b[0])**2+(a[1]-b[1])**2


@dataclass(frozen=True)
class CoverCell:
    index: tuple[int, int]
    center: Point
    # Rational vertices of a conservative hull/cell intersection.  These stay
    # internal: rounding a logged polygon is never used to discard a cell.
    support: tuple[ExactPoint, ...]

    def excluded_by_failures(self, failed_positions):
        """Only a whole support contained in one failed disk can be removed."""
        radius_squared = (Fraction(20)-_FAILURE_MARGIN_M)**2
        return any(all(_square_distance(vertex, _exact_point(p)) <= radius_squared
                       for vertex in self.support) for p in failed_positions)


@dataclass(frozen=True)
class CoverPlan:
    cells: tuple[CoverCell, ...]
    walk_distance_m: float
    cost_bound_s: float
    generated_cells: int
    excluded_cells: int
    basis: Point
    route_kind: str

    def summary(self):
        return {"cover_kind": "current_outer_hull_cells", "cell_count": len(self.cells),
                "generated_cells": self.generated_cells, "excluded_cells": self.excluded_cells,
                "centers": [cell.center for cell in self.cells], "basis": self.basis,
                "route_kind": self.route_kind, "walk_distance_upper_bound_m": self.walk_distance_m,
                "cost_upper_bound_s": self.cost_bound_s,
                "timing_quantization_guard_s": len(self.cells)*_ACTION_TIMING_GUARD_S,
                "cost_formula": "walk/5 + 3*attempt_count + 2 + 1e-6*attempt_count; clear does not switch channel",
                "certificate_scope": "conditional on the current conservative outer hull and valid clear feedback"}


def _ordered_cells(cells, start):
    if not cells:
        return (), 0., "empty"
    routes = []
    for primary in (0, 1):
        groups = {}
        for cell in cells:
            groups.setdefault(cell.index[primary], []).append(cell)
        route = []
        for row, key in enumerate(sorted(groups)):
            route += sorted(groups[key], key=lambda c: c.index[1-primary], reverse=bool(row % 2))
        routes.extend([(f"serpentine_axis{primary}", route),
                       (f"serpentine_axis{primary}_reverse", list(reversed(route)))])
    # Greedy is a route candidate only; it makes no claim about target density.
    remaining, route, previous = list(cells), [], start
    while remaining:
        cell = min(remaining, key=lambda c: (distance(previous, c.center), c.index))
        remaining.remove(cell)
        route.append(cell)
        previous = cell.center
    routes.append(("nearest_remaining", route))

    def length(route):
        return math.fsum(distance(a, b) for a, b in zip(
            [start]+[c.center for c in route[:-1]], [c.center for c in route]))

    name, route = min(routes, key=lambda item: (length(item[1]), item[0]))
    return tuple(route), length(route), name


def _cover_with_basis(hull, start, failed_positions, side, basis):
    # u=(ux,uy), v=(-uy,ux) are exactly perpendicular as rational vectors.
    # Their norm need not round to exactly one.  Inversion and intersections
    # below are rational, and every submitted center is checked independently.
    u = _exact_point(basis)
    v = -u[1], u[0]
    norm_squared = u[0]**2+u[1]**2
    if not norm_squared:
        raise ValueError("zero grid basis")
    exact_hull = [_exact_point(p) for p in hull]
    origin = exact_hull[0]

    def to_grid(p):
        d = p[0]-origin[0], p[1]-origin[1]
        return ((d[0]*u[0]+d[1]*u[1])/norm_squared,
                (d[0]*v[0]+d[1]*v[1])/norm_squared)

    def to_world(p):
        return (origin[0]+p[0]*u[0]+p[1]*v[0],
                origin[1]+p[0]*u[1]+p[1]*v[1])

    poly = _convex_hull([to_grid(p) for p in exact_hull])
    xmin, xmax = min(p[0] for p in poly), max(p[0] for p in poly)
    ymin, ymax = min(p[1] for p in poly), max(p[1] for p in poly)
    width = Fraction(float(side))
    nx, ny = max(1, math.ceil((xmax-xmin)/width)), max(1, math.ceil((ymax-ymin)/width))
    if nx*ny > 1_000_000:
        raise ValueError("grid extent exceeds the finite implementation guard")
    pad = _INTERSECTION_PAD_M
    cells, generated, excluded = [], 0, 0
    for j in range(ny):
        low_y, high_y = ymin+j*width, ymin+(j+1)*width
        row = _clip_axis(_clip_axis(poly, 1, low_y-pad, False), 1, high_y+pad, True)
        if not row:
            continue
        lo = max(0, math.floor((min(p[0] for p in row)-xmin-pad)/width))
        hi = min(nx-1, math.floor((max(p[0] for p in row)-xmin+pad)/width))
        for i in range(lo, hi+1):
            low_x, high_x = xmin+i*width, xmin+(i+1)*width
            support = _clip_axis(_clip_axis(row, 0, low_x-pad, False), 0, high_x+pad, True)
            if not support:
                continue
            generated += 1
            exact_center = to_world(((low_x+high_x)/2, (low_y+high_y)/2))
            center = float(exact_center[0]), float(exact_center[1])
            submitted_center = _exact_point(center)
            # Exact squared-distance check includes intersection padding and
            # rounding of the actual submitted float center; margin is strict.
            corners = [to_world((x, y)) for x in (low_x-pad, high_x+pad)
                       for y in (low_y-pad, high_y+pad)]
            if not all(_square_distance(c, submitted_center) <= Fraction("19.999")**2
                       for c in corners):
                raise ValueError("submitted cell center does not certify its padded cell")
            cell = CoverCell((i, j), center, tuple(to_world(p) for p in support))
            if cell.excluded_by_failures(failed_positions):
                excluded += 1
            else:
                cells.append(cell)
    ordered, walk, route_kind = _ordered_cells(cells, start)
    return CoverPlan(ordered, walk, walk/5+3*len(ordered)+2+len(ordered)*_ACTION_TIMING_GUARD_S if ordered else 0.,
                     generated, excluded, basis, route_kind)


def hull_cell_cover(hull, start, *, failed_positions=(), cell_side_m=28.):
    """Cover the complete current hull, never an inscribed disk or samples.

    Compare a world-axis grid and a grid parallel to the longest hull edge.
    Every retained cell has an exactly checked <20 m covering center.  Rational
    closed intersections with outward padding preserve boundary contacts.
    """
    if not hull:
        raise ValueError("an empty hull has no clearance certificate")
    RefinedLocalConfig(cell_side_m=cell_side_m)
    points = [tuple(float(x) for x in p) for p in hull]
    for p in points+[tuple(start)]+list(failed_positions):
        _exact_point(p)
    bases = [(1., 0.)]
    if len(points) > 1:
        a, b = max(zip(points, points[1:]+points[:1]), key=lambda pair: distance(*pair))
        length = distance(a, b)
        if length:
            u = ((b[0]-a[0])/length, (b[1]-a[1])/length)
            if abs(u[1]) > 1e-12 and abs(u[0]) > 1e-12:
                bases.append(u)
    plans = [_cover_with_basis(points, tuple(start), tuple(failed_positions), cell_side_m, u)
             for u in bases]
    return min(plans, key=lambda plan: (plan.cost_bound_s, len(plan.cells), plan.basis))


def remaining_cost_bound(hull, start, *, failed_positions=(), config=None,
                         clear_radius=19.999, nearest_safe=True):
    """Cost of an explicit safe continuation, conditional on the supplied hull."""
    if not hull:
        raise ValueError("an empty hull has no continuation certificate")
    config = config or RefinedLocalConfig()
    center, radius = minimum_enclosing_circle(hull)
    if radius <= clear_radius:
        q = nearest_safe_clear(hull, start, radius=clear_radius) if nearest_safe else center
        if q is not None and certify_clear(hull, q, radius=20., margin=1e-5):
            return distance(start, q)/5+5.+_ACTION_TIMING_GUARD_S, {
                "kind": "certified_clear", "point": q,
                "timing_quantization_guard_s": _ACTION_TIMING_GUARD_S}
    plan = hull_cell_cover(hull, start, failed_positions=failed_positions,
                           cell_side_m=config.cell_side_m)
    # Every cell excluded by previous failures means this branch contains no
    # feasible source.  A bound of zero must not masquerade as a successful plan.
    if not plan.cells:
        return None, {"kind": "infeasible_after_failed_clears"}
    return plan.cost_bound_s, {"kind": "cell_cover", **plan.summary()}


def measurement_cost_decision(knowledge, current, current_channel, *, solver_config,
                              nosignal_config=None, refined_config=None):
    """Conservative one-step bound comparison, not a probability-based VOI claim.

    All 360 degrees of possible direction reports are covered by expanded bins.
    The no-signal branch retains the hull unless independently proved impossible.
    An improvement between upper bounds need not improve realized stopping time.
    """
    cfg = refined_config or RefinedLocalConfig()
    kwargs = dict(failed_positions=knowledge.failed_clear_positions, config=cfg,
                  clear_radius=solver_config.clear_radius, nearest_safe=solver_config.nearest_safe)
    immediate, immediate_info = remaining_cost_bound(knowledge.hull, current, **kwargs)
    if immediate is None:
        raise InconsistentKnowledge("all current hull cells contradict failed clears")
    q, selector = choose_measurement_nosignal(
        knowledge, current, solver_config.sensing, solver_config.bin_width_deg,
        solver_config.radius_weight, config=nosignal_config)
    info = {"immediate_cost_upper_bound_s": immediate, "immediate_plan": immediate_info,
            "selected_point": q, "selector": selector,
            "interpretation": "comparison of certified continuation upper bounds; no expected-time or optimality guarantee",
            "take_measurement": False}
    if q is None:
        return None, {**info, "reason": "no_candidate"}
    visible = positive_convex_visibility(knowledge.positive_positions, q)
    visibility_reason = "convex_hull_of_positive_stations" if visible else None
    if knowledge.problem == 3 and max_distance(knowledge.hull, q) <= 1000.-1e-5:
        visible, visibility_reason = True, "omni_all_hull_within_minimum_radius"
    no_signal_cost = None
    if not visible:
        no_signal_cost, _ = remaining_cost_bound(knowledge.hull, q, **kwargs)
        movement = distance(current, q)/5
        switch = int(current_channel != knowledge.channel)
        # With an unchanged-hull no-signal branch, an optimal immediate optical
        # policy can instead travel through q without paying to measure there.
        # Different approximate route bounds must not manufacture information
        # value.  Therefore a smaller computed bound alone cannot pass this gate.
        return None, {**info, "reason": "visibility_not_certified_no_information_value_claim",
                      "measurement_cost_s": movement+5.+switch,
                      "measurement_timing_guard_s": _ACTION_TIMING_GUARD_S,
                      "measurement_cost_terms_s": {"movement": movement, "measure": 5., "switch": switch},
                      "branch_cost_upper_bounds_s": {"near": 5., "no_signal": no_signal_cost,
                                                     "direction": None},
                      "direction_bins": [], "direction_full_circle_bins": 0,
                      "no_signal_impossible_certificate": None,
                      "measure_then_continue_upper_bound_s": None,
                      "upper_bound_gain_s": None}
    bins = math.ceil(360./cfg.direction_bin_width_deg)
    width = 360./bins
    direction_bounds = []
    for i in range(bins):
        center_angle = (i+.5)*width
        branch_hull = clip_bearing(knowledge.hull, q, center_angle,
                                   knowledge.epsilon_deg+width/2+1e-7)
        if not branch_hull:
            continue
        cost, continuation = remaining_cost_bound(branch_hull, q, **kwargs)
        if cost is not None:
            direction_bounds.append({"bin_center_deg": center_angle, "bin_width_deg": width,
                                     "remaining_cost_upper_bound_s": cost,
                                     "continuation_kind": continuation["kind"]})
    branches = [5.] + [b["remaining_cost_upper_bound_s"] for b in direction_bounds]
    if no_signal_cost is not None:
        branches.append(no_signal_cost)
    movement = distance(current, q)/5
    switch = int(current_channel != knowledge.channel)
    next_bound = movement+5.+switch+_ACTION_TIMING_GUARD_S+max(branches)
    gain = immediate-next_bound
    take = gain > cfg.min_bound_gain_s+1e-8
    info.update(measurement_cost_s=movement+5.+switch,
                measurement_timing_guard_s=_ACTION_TIMING_GUARD_S,
                measurement_cost_terms_s={"movement": movement, "measure": 5., "switch": switch},
                branch_cost_upper_bounds_s={"near": 5., "no_signal": no_signal_cost,
                                           "direction": max([b["remaining_cost_upper_bound_s"]
                                                             for b in direction_bounds], default=None)},
                direction_bins=direction_bounds, direction_full_circle_bins=bins,
                no_signal_impossible_certificate=visibility_reason,
                measure_then_continue_upper_bound_s=next_bound, upper_bound_gain_s=gain,
                take_measurement=take,
                reason="strict_upper_bound_reduction" if take else "no_strict_upper_bound_reduction")
    return tuple(q) if take else None, info


class RefinedLocalSolver(NoSignalSolver):
    """Preserve global coverage and base sensing; refine the local continuation."""

    def __init__(self, client, config=None, decision_log=None, *, nosignal_config=None,
                 mode="cover", refined_config=None):
        if mode not in ("cover", "adaptive"):
            raise ValueError("mode must be 'cover' or 'adaptive'")
        super().__init__(client, config, decision_log, nosignal_config=nosignal_config)
        if (isinstance(self.config.local_measure_limit, bool)
                or not isinstance(self.config.local_measure_limit, int)
                or not 0 <= self.config.local_measure_limit <= 5):
            raise ValueError("refined solver requires base local_measure_limit in [0,5]")
        self.local_refinement_mode = mode
        self.refined_config = refined_config or RefinedLocalConfig()
        self.stats.update(refined_cover_targets=0, refined_cover_cells=0,
                          refined_extra_measures=0, refined_failed_cells_skipped=0)

    def _record(self, event, **data):
        if event == "start":
            data.update(local_refinement_variant=VARIANT, local_refinement_mode=self.local_refinement_mode,
                        refined_config=asdict(self.refined_config))
        elif event == "finish":
            data["result"].update(local_refinement_variant=VARIANT,
                                  local_refinement_mode=self.local_refinement_mode,
                                  refined_config=asdict(self.refined_config))
        super()._record(event, **data)

    def _record_selector(self, channel, selection):
        self.stats["nosignal_choices"] += 1
        self.stats["nosignal_changed_choices"] += int(selection.get("selection_changed_from_baseline", False))
        self.stats["nosignal_baseline_fallbacks"] += int("baseline_fallback_reason" in selection)
        self.stats["nosignal_convex_mixed_choices"] += int(selection.get("convex_visibility_mixed_candidates", False))
        self._record("choose_measurement", target_channel=channel, selection=selection)

    def _localize(self, channel):
        knowledge = self.channels[channel]
        if knowledge.status == "cleared" or self._try_certified_clear(knowledge):
            return
        performed = 0
        for _ in range(self.config.local_measure_limit):
            q, selection = choose_measurement_nosignal(
                knowledge, self.client.position, self.config.sensing,
                self.config.bin_width_deg, self.config.radius_weight, config=self.nosignal_config)
            if q is None:
                break
            self._record_selector(channel, selection)
            self._measure(channel, q, "local_active_sensing")
            performed += 1
            if knowledge.status == "cleared" or self._try_certified_clear(knowledge):
                return
        if self.local_refinement_mode == "adaptive":
            # Five is a TOTAL local-measurement limit, including the frozen L1.
            # With L1, adaptive can therefore add at most four measurements.
            for _ in range(min(self.refined_config.max_extra_measures, 5-performed)):
                q, decision = measurement_cost_decision(
                    knowledge, self.client.position, self.client.current_channel,
                    solver_config=self.config, nosignal_config=self.nosignal_config,
                    refined_config=self.refined_config)
                self._record("refined_measure_decision", target_channel=channel, decision=decision)
                if q is None:
                    break
                self._record_selector(channel, decision["selector"])
                self.stats["refined_extra_measures"] += 1
                self._measure(channel, q, "refined_local_sensing")
                if knowledge.status == "cleared" or self._try_certified_clear(knowledge):
                    return
        if knowledge.first_direction is None:
            raise InconsistentKnowledge("detected source lacks a direction; a near response should already clear it")
        self.stats["fallback_targets"] += 1
        self.stats["refined_cover_targets"] += 1
        plan = hull_cell_cover(knowledge.hull, self.client.position,
                               failed_positions=knowledge.failed_clear_positions,
                               cell_side_m=self.refined_config.cell_side_m)
        self.stats["refined_cover_cells"] += len(plan.cells)
        self.stats["refined_failed_cells_skipped"] += plan.excluded_cells
        self._record("refined_cover_plan", target_channel=channel, plan=plan.summary())
        if not plan.cells:
            raise InconsistentKnowledge("all current hull cells contradict failed clears")
        attempted = 0
        for cell in plan.cells:
            if cell.excluded_by_failures(knowledge.failed_clear_positions):
                self.stats["refined_failed_cells_skipped"] += 1
                continue
            attempted += 1
            if self._clear(channel, cell.center,
                           {"type": "optical_grid_attempt", "cover_kind": "current_outer_hull_cells",
                            "max_grid_points": len(plan.cells), "cell_index": cell.index,
                            "cell_side_m": self.refined_config.cell_side_m,
                            "cell_radius_certificate_m": 19.999}):
                return
        raise InconsistentKnowledge(f"current-hull optical cover exhausted after {attempted} attempts")
