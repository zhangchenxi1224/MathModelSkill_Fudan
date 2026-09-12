"""Unify bounded sensing, certified optical cover, and cheap joint routing."""
from dataclasses import replace
from bsolver.strategy import Solver, SolverConfig
from bsolver.nosignal_sensing import NoSignalSolver, NoSignalConfig, choose_measurement_nosignal
from bsolver.sensing import choose_measurement, optical_fallback_points
from bsolver.geometry import distance, distance_to_polygon
from bsolver.knowledge import InconsistentKnowledge
from diverse.policies import ring_certificate, ORIGINAL_RING


def champion_mode(problem):
    return 'normal' if problem == 3 else 'nosignal'


class IterationSolver(Solver):
    def __init__(self, client, config, decision_log=None, *, spec=None):
        super().__init__(client, config, decision_log)
        self.spec = dict(spec or {})
        self.mode = self.spec.get('mode', champion_mode(config.problem))
        self.cover_mode = self.spec.get('cover', 'original')
        self.schedule_options = self.spec.get('schedule_options', {})
        self.probe_used = set()
        self.ring_proof = None
        if self.spec.get('ring') is not None:
            if config.problem != 3:
                raise ValueError('ring ablation is only certified for P3')
            ring = self.spec['ring']
            self.ring_proof = ring_certificate(ring)
            self.points = [(x*ring/ORIGINAL_RING, y*ring/ORIGINAL_RING) for x, y in self.points]

    def _complete_evidence(self):
        result = super()._complete_evidence()
        if result and result['type'] == 'per_channel_coverage' and self.ring_proof:
            result.update(construction='certified_contracted_seven', analytic_certificate=self.ring_proof)
        return result

    def _select(self, knowledge):
        mode = self.mode
        if mode in ('fisher', 'infogain', 'rollout', 'probe'):
            from diverse.planning import choose_action
            options = dict(self.spec.get('planner', {}), allow_clear=knowledge.channel not in self.probe_used)
            action, info = choose_action(knowledge, self.client.position,
                                        self.client.current_channel, mode, options)
            if action:
                return action, info
            self._record('planning_fallback', target_channel=knowledge.channel, selection=info)
            mode = champion_mode(self.config.problem)
        if mode == 'nosignal':
            point, info = choose_measurement_nosignal(knowledge, self.client.position, self.config.sensing,
                        self.config.bin_width_deg, self.config.radius_weight,
                        config=NoSignalConfig(**self.spec.get('nosignal', {})))
        else:
            point, info = choose_measurement(knowledge, self.client.position, self.config.sensing,
                                            self.config.bin_width_deg, self.config.radius_weight)
        return ({'kind':'measure', 'point':point} if point else None), info

    def _localize(self, channel):
        knowledge = self.channels[channel]
        if knowledge.status == 'cleared' or self._try_certified_clear(knowledge):
            return
        for _ in range(self.config.local_measure_limit):
            action, info = self._select(knowledge)
            if not action:
                break
            point = tuple(action['point'])
            self._record('selected_local_action', target_channel=channel, action=action, selection=info)
            if action['kind'] == 'clear':
                if channel in self.probe_used:
                    raise ValueError('planner exceeded clear probe cap')
                self.probe_used.add(channel)
                if self._clear(channel, point, {'type':'bounded_probe', 'max_per_channel':1}):
                    return
            else:
                if any(distance(point, old.position) < .05 for old in knowledge.observations):
                    break
                self._measure(channel, point, 'local_active_sensing')
            if knowledge.status == 'cleared' or self._try_certified_clear(knowledge):
                return
        self._fallback(knowledge)

    def _fallback(self, knowledge):
        if knowledge.first_direction is None:
            raise InconsistentKnowledge('detected source lacks direction')
        self.stats['fallback_targets'] += 1
        if self.cover_mode == 'original':
            points = optical_fallback_points(knowledge.first_direction)
            if distance(self.client.position, points[-1]) < distance(self.client.position, points[0]):
                points.reverse()
            certificate = {'kind':'original_strip', 'point_count':len(points)}
        else:
            from .clear_cover import build_cover
            points, certificate = build_cover(knowledge, self.client.position, self.cover_mode)
        self._record('optical_cover_choice', target_channel=knowledge.channel, cover_certificate=certificate)
        for point in points:
            if distance_to_polygon(knowledge.hull, point) > 20.+1e-5:
                continue
            if self._clear(knowledge.channel, point, {'type':'optical_grid_attempt',
                    'construction':certificate.get('kind'), 'max_grid_points':len(points)}):
                return
        # A cover certificate is not reinterpreted after failure.
        raise InconsistentKnowledge('finite optical cover exhausted')


def make_solver(client, problem, spec, decision_log=None):
    config = SolverConfig(problem=problem, local_measure_limit=spec.get('limit', 1))
    for name, value in spec.get('config', {}).items():
        if name not in config.__dataclass_fields__ or name == 'problem':
            raise ValueError('unknown or forbidden SolverConfig field')
        setattr(config, name, value)
    if spec.get('implementation') == 'legacy':
        return Solver(client, replace(config, local_measure_limit=5), decision_log)
    if spec.get('implementation') == 'champion':
        return (Solver(client, config, decision_log) if problem == 3 else
                NoSignalSolver(client, config, decision_log, nosignal_config=NoSignalConfig()))
    if spec.get('reorder'):
        from .scheduling import RouteMixin
        class RoutedIterationSolver(RouteMixin, IterationSolver):
            pass
        return RoutedIterationSolver(client, config, decision_log, spec=spec)
    return IterationSolver(client, config, decision_log, spec=spec)
