"""Frozen local candidates. Policies receive only the public RobotClient."""
from fractions import Fraction as F

from bsolver.geometry import distance
from bsolver.knowledge import InconsistentKnowledge
from bsolver.nosignal_sensing import choose_measurement_nosignal
from bsolver.refined_coverage import refined_directional_route, refined_coverage_certificate
from bsolver.refined_local import RefinedLocalSolver, hull_cell_cover
from bsolver.strategy import Solver, SolverConfig
from side_methods.solver import make_solver as side_solver


SPECS = {
    3: ['main', 'compact1150', 'compact1200'],
    4: ['main', 'side31_l2', 'merged25_l1', 'stationary_rescue'],
}


def disk_disjoint(hull, center, radius=20.):
    """Exact comparison for the polygon actually maintained by the policy.

    Degenerate polygons are handled. The input float coordinates are converted
    exactly; this predicate never deletes from a sampled approximation.
    """
    if not hull:
        raise InconsistentKnowledge('empty outer hull')
    p = tuple(map(F, center))
    vs = [tuple(map(F, v)) for v in hull]
    r2 = F(radius) ** 2
    sq = lambda x, y: sum((a-b)**2 for a, b in zip(x, y))
    if len(vs) == 1:
        return sq(p, vs[0]) > r2
    crosses = []
    distances = []
    for a, b in zip(vs, vs[1:]+vs[:1]):
        dx, dy = b[0]-a[0], b[1]-a[1]
        crosses.append(dx*(p[1]-a[1])-dy*(p[0]-a[0]))
        denom = dx*dx+dy*dy
        t = max(F(0), min(F(1), ((p[0]-a[0])*dx+(p[1]-a[1])*dy)/denom)) if denom else F(0)
        distances.append(sq(p, (a[0]+t*dx, a[1]+t*dy)))
    area2 = sum(a[0]*b[1]-a[1]*b[0] for a, b in zip(vs, vs[1:]+vs[:1]))
    if len(vs) >= 3 and area2 and (all(c >= 0 for c in crosses) or all(c <= 0 for c in crosses)):
        return False
    return min(distances) > r2


class Coverage25Mixin:
    def _complete_evidence(self):
        evidence = super()._complete_evidence()
        if evidence and evidence['type'] == 'per_channel_coverage':
            evidence.update(construction='refined_directional_triangular',
                            refined_coverage_certificate=refined_coverage_certificate(),
                            executed_ordered_points=self.points)
        return evidence


class Main25(Coverage25Mixin, RefinedLocalSolver):
    pass


class StationaryRescue(Main25):
    """One stationary measurement, unchanged conditional baseline clear route.

    shadow_failures belongs to a mathematical continuation, not the executed
    observation ledger. A skipped clear is known to fail by exact disk/hull
    separation, and is added to that continuation only. This preserves the
    baseline's dynamic failed-disk skips while removing unnecessary visits.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rescue_used = set()
        self.stats.update(rescue_measures=0, rescue_direction=0,
                          rescue_no_signal=0, rescue_skipped_clears=0)

    def _localize(self, channel):
        k = self.channels[channel]
        if k.status == 'cleared' or self._try_certified_clear(k):
            return
        for _ in range(self.config.local_measure_limit):
            q, info = choose_measurement_nosignal(k, self.client.position, self.config.sensing,
                self.config.bin_width_deg, self.config.radius_weight, config=self.nosignal_config)
            if q is None:
                break
            self._record_selector(channel, info)
            self._measure(channel, q, 'local_active_sensing')
            if k.status == 'cleared' or self._try_certified_clear(k):
                return
        if k.first_direction is None:
            raise InconsistentKnowledge('detected target has no direction')
        self.stats['fallback_targets'] += 1
        self.stats['refined_cover_targets'] += 1
        plan = hull_cell_cover(k.hull, self.client.position,
            failed_positions=k.failed_clear_positions, cell_side_m=self.refined_config.cell_side_m)
        self.stats['refined_cover_cells'] += len(plan.cells)
        self.stats['refined_failed_cells_skipped'] += plan.excluded_cells
        self._record('refined_cover_plan', target_channel=channel, plan=plan.summary())
        shadow_failures = list(k.failed_clear_positions)
        eligible = len(plan.cells) >= 20 and channel not in self.rescue_used
        measured = False
        for cell in plan.cells:
            if cell.excluded_by_failures(shadow_failures):
                self.stats['refined_failed_cells_skipped'] += 1
                continue
            if measured and disk_disjoint(k.hull, cell.center):
                shadow_failures.append(cell.center)
                self.stats['rescue_skipped_clears'] += 1
                self._record('rescue_certified_skip', target_channel=channel,
                    skipped_center=cell.center, predicate='exact dist(center, maintained hull)>20',
                    executed_request=False, continuation_only=True)
                continue
            if self._clear(channel, cell.center, {'type': 'optical_grid_attempt',
                    'cover_kind': 'current_outer_hull_cells', 'max_grid_points': len(plan.cells),
                    'cell_index': cell.index, 'cell_side_m': self.refined_config.cell_side_m,
                    'cell_radius_certificate_m': 19.999}):
                return
            shadow_failures.append(cell.center)
            if (eligible and not measured and self.client.current_channel == channel
                    and not any(tuple(o.position) == tuple(cell.center) for o in k.observations)):
                self.rescue_used.add(channel)
                response = self._measure(channel, cell.center, 'stationary_rescue')
                outcome = response['measure_result']
                if outcome == 'near':
                    raise InconsistentKnowledge('near at the same point immediately after a clear failure')
                measured = True
                self.stats['rescue_measures'] += 1
                self.stats['rescue_'+outcome] += 1
        raise InconsistentKnowledge('certified conditional optical continuation exhausted')


def make_policy(client, problem, variant, decision_log=None):
    config = SolverConfig(problem=problem, local_measure_limit=1)
    if problem == 3:
        if variant == 'main':
            return Solver(client, config, decision_log)
        if variant not in ('compact1150', 'compact1200'):
            raise ValueError(variant)
        return side_solver(client, problem, {'limit': 1, 'cover': 'bbox', 'reorder': True,
            'ring': 1150 if variant == 'compact1150' else 1200,
            'schedule_options': {'reorder': True, 'service_score': 'hull', 'shared': False}}, decision_log)
    if variant in ('main', 'stationary_rescue'):
        policy = (Main25 if variant == 'main' else StationaryRescue)(client, config, decision_log, mode='cover')
    elif variant in ('side31_l2', 'merged25_l1'):
        spec = {'limit': 2 if variant == 'side31_l2' else 1, 'cover': 'bbox', 'reorder': True,
                'schedule_options': {'reorder': True, 'service_score': 'hull', 'shared': False},
                'config': {'joint_detour_m': 1200 if variant == 'side31_l2' else 800}}
        policy = side_solver(client, problem, spec, decision_log)
        if variant == 'side31_l2':
            return policy
        # Preserve set-based coverage evidence after lawful suffix reordering.
        old_complete = policy._complete_evidence
        def complete():
            evidence = old_complete()
            if evidence and evidence['type'] == 'per_channel_coverage':
                evidence.update(construction='refined_directional_triangular',
                                refined_coverage_certificate=refined_coverage_certificate())
            return evidence
        policy._complete_evidence = complete
    else:
        raise ValueError(variant)
    policy.points = refined_directional_route()
    return policy
