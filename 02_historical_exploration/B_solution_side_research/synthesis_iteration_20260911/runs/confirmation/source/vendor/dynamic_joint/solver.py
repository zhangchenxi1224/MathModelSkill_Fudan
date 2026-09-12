"""Feedback-driven, cross-channel policy; takes public client observations only."""
from dataclasses import asdict
import math
import time

from ._core.strategy import Solver, SolverConfig
from ._core.geometry import certify_clear, distance, distance_to_polygon
from ._core.sensing import optical_fallback_points
from .counts import CountPrior
from .model import Action, Target, Node, BeliefCache, candidate_actions, choose
from .working_belief import PlanningBudgetExceeded


DEFAULTS = dict(horizon=2, local_limit=2, target_limit=3, station_limit=2,
                candidate_limit=6, branch_limit=3, node_limit=250,
                planning_budget_s=.25, particle_count=16, unknown_samples=96,
                seed=2026091107, likelihood_floor=.002, angle_bin_deg=4.,
                allow_probe=True, probe_threshold=.35, offstation_limit=1,
                unseen_service_s=150.)


def validated_options(values=None):
    values = dict(values or {})
    if set(values)-set(DEFAULTS):
        raise ValueError(f"unknown planning options: {sorted(set(values)-set(DEFAULTS))}")
    result = {**DEFAULTS, **values}
    bounds = dict(horizon=(1,3), local_limit=(0,10), target_limit=(1,16),
                  station_limit=(1,31), candidate_limit=(5,12), branch_limit=(3,6),
                  node_limit=(1,5000), particle_count=(4,128), unknown_samples=(16,4096),
                  offstation_limit=(0,3), seed=(0,2**32-1))
    for key,(low,high) in bounds.items():
        value=result[key]
        if isinstance(value,bool) or not isinstance(value,int) or not low<=value<=high:
            raise ValueError(f"{key} must be an integer in [{low}, {high}]")
    for key,low,high in [('planning_budget_s',.001,5.),('likelihood_floor',1e-8,.1),
                         ('angle_bin_deg',.5,30.),('probe_threshold',.01,1.),
                         ('unseen_service_s',0.,2000.)]:
        value=result[key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
            raise ValueError(f"invalid {key}")
    if abs(360/result['angle_bin_deg']-round(360/result['angle_bin_deg']))>1e-8:
        raise ValueError('angle_bin_deg must divide 360')
    if not isinstance(result['allow_probe'],bool): raise ValueError('allow_probe must be boolean')
    return result


class DynamicJointSolver(Solver):
    def __init__(self, client, config=None, decision_log=None, *, options=None, prior=None):
        self.options=validated_options(options)
        config=config or SolverConfig(local_measure_limit=self.options['local_limit'])
        if config.local_measure_limit!=self.options['local_limit']:
            raise ValueError('local_measure_limit and local_limit disagree')
        self.prior=CountPrior.from_config(config.problem,prior)
        super().__init__(client,config,decision_log)
        self.beliefs=BeliefCache(self.options,self.prior)
        self.local_used={f:0 for f in self.channels}
        self.offstation_used={f:0 for f in self.channels}
        self.probe_used=set()
        self.optical_points={}
        self.optical_used={f:set() for f in self.channels}
        self.fallback_channels=set()
        self.stats.update(dynamic_decisions=0, completed_plans=0, budget_fallbacks=0,
                          cross_channel_changes=0, planning_real_time_s=0.)
        self.previous_target=None

    def _refresh_proofs(self):
        for f,k in self.channels.items():
            if k.status=='unknown' and len(k.coverage_indices)==len(self.points):
                k.status='absent'
                self._record('channel_absence_certificate', target_channel=f,
                             checked_indices=sorted(k.coverage_indices))
            if k.status!='detected': continue
            if k.first_direction is None:
                raise ValueError('detected source lacks first direction')
            if f not in self.optical_points:
                self.optical_points[f]=tuple(map(tuple,optical_fallback_points(k.first_direction)))
            for i,p in enumerate(self.optical_points[f]):
                if distance_to_polygon(k.hull,p)>20.+1e-5:
                    self.optical_used[f].add(i)

    def _snapshot(self, deadline=None, with_beliefs=True):
        targets={}; info={}
        # Immutable copies keep hypothetical branches away from real ledgers.
        for f,k in self.channels.items():
            particles,likelihood,diagnostic=(self.beliefs.for_channel(k,deadline)
                                            if with_beliefs else ((),1.,{}))
            targets[f]=Target(f,k.status,tuple(map(tuple,k.hull)),particles,likelihood,
                              frozenset(k.coverage_indices),tuple(tuple(o.position) for o in k.observations),
                              self.local_used[f],f in self.probe_used,self.offstation_used[f],
                              self.optical_points.get(f,()),frozenset(self.optical_used[f]))
            if diagnostic: info[f]=diagnostic
        return Node(tuple(self.client.position),self.client.current_channel,targets),info

    def _fallback(self, node):
        candidates=candidate_actions(node,self.points,self.prior,self.options)
        if not candidates:
            raise ValueError('finite candidate allowances exhausted without completion proof')
        # Guaranteed clears first, then nearest remaining finite action. None
        # of these actions needs a successful Monte Carlo approximation.
        certified=[a for a in candidates if a.certified]
        return min(certified or candidates,
                   key=lambda a:(distance(node.position,a.point)/5+
                                 (5+int(a.channel!=node.current_channel) if a.kind=='measure' else 3),
                                 a.channel,a.role))

    def _decision(self):
        start=time.perf_counter()
        remaining=self.client.remaining_real_time()
        allowance=min(self.options['planning_budget_s'],
                      max(.001,remaining-self.config.reserve_real_s-.25))
        deadline=start+allowance
        node=None; diagnostic={}; reason=None
        try:
            node,diagnostic=self._snapshot(deadline)
            action,plan=choose(node,self.points,self.prior,self.options,deadline)
            self.stats['completed_plans']+=1
        except PlanningBudgetExceeded as exc:
            reason=str(exc)
            if node is None: node,diagnostic=self._snapshot(with_beliefs=False)
            action=self._fallback(node)
            plan={'scope':'finite-progress budget fallback','reason':reason}
            self.stats['budget_fallbacks']+=1
        elapsed=time.perf_counter()-start
        self.stats['planning_real_time_s']+=elapsed
        self.stats['dynamic_decisions']+=1
        if self.previous_target is not None and action.channel!=self.previous_target:
            self.stats['cross_channel_changes']+=1
        self.previous_target=action.channel
        self._record('dynamic_decision', action=asdict(action),counts=node.counts(self.prior),
                     planning_seconds=elapsed,planning_budget_s=allowance,planner=plan,
                     belief_diagnostics=diagnostic,
                     remaining_real_s=remaining if math.isfinite(remaining) else None)
        return action

    def _execute(self, action):
        k=self.channels[action.channel]
        if k.status not in ('unknown','detected'):
            raise ValueError('planner selected an inactive channel')
        if action.kind=='measure':
            if action.station is not None:
                if (action.point!=tuple(self.points[action.station]) or
                        action.station in k.coverage_indices):
                    raise ValueError('invalid or repeated coverage action')
            elif any(distance(action.point,o.position)<1. for o in k.observations):
                raise ValueError('repeated local measurement')
            if action.role=='local_measure':
                if self.local_used[action.channel]>=self.options['local_limit']:
                    raise ValueError('local budget exceeded')
                self.local_used[action.channel]+=1
            elif action.station is None:
                if self.offstation_used[action.channel]>=self.options['offstation_limit']:
                    raise ValueError('off-station budget exceeded')
                self.offstation_used[action.channel]+=1
            self._measure(action.channel,action.point,'dynamic_'+action.role,station=action.station)
        elif action.certified:
            # Certification is rechecked against the REAL conservative hull.
            if not certify_clear(k.hull,action.point,radius=20.,margin=1e-5):
                raise ValueError('root action lacks a real geometric certificate')
            self._clear(action.channel,action.point,{'type':'outer_hull','selector':'dynamic_joint'})
        elif action.role=='probe':
            if action.channel in self.probe_used: raise ValueError('probe budget exceeded')
            self.probe_used.add(action.channel)
            self._clear(action.channel,action.point,{'type':'bounded_probe','max_per_channel':1})
        elif action.role=='optical':
            index=action.fallback_index
            if (index is None or index in self.optical_used[action.channel] or
                    self.optical_points[action.channel][index]!=action.point):
                raise ValueError('invalid optical action')
            self.optical_used[action.channel].add(index)
            if action.channel not in self.fallback_channels:
                self.fallback_channels.add(action.channel); self.stats['fallback_targets']+=1
            self._clear(action.channel,action.point,{'type':'optical_grid_attempt','max_grid_points':110,
                                                   'index':index})
        else: raise ValueError('invalid dynamic action')

    def run(self):
        start=time.monotonic(); status='incomplete'; error=None
        # Coverage, local sensing, one probe and 110 optical points each have
        # finite allowances. Certificates can clear each target at most once.
        limit=20*(len(self.points)+self.options['offstation_limit'])+16*(self.options['local_limit']+112)
        try:
            if not self.client.entered: self.client.enter()
            self._record('start',config=asdict(self.config),planning=self.options,
                         prior=asdict(self.prior),coverage_points=self.points,max_decisions=limit)
            for _ in range(limit+1):
                self._refresh_proofs()
                self.stop_evidence=self._complete_evidence()
                if self.stop_evidence:
                    status='complete'; break
                self._check_budget(self.client.position,6)
                self._execute(self._decision())
            else: error='finite_decision_limit'
        except Exception as exc:
            error=f'{type(exc).__name__}: {exc}'
            self._record('failure',error=error)
        finally:
            if self.client.entered and not self.client.exited and not getattr(self.client,'pending_request',None):
                try: self.client.exit()
                except Exception as exc:
                    if status=='complete': status='complete_exit_unconfirmed'
                    error=(error+'; ' if error else '')+f'exit: {type(exc).__name__}: {exc}'
        computed=(self.stats['walk_distance_m']/5+self.stats['switches']+5*self.stats['measures']+
                  3*self.stats['clear_attempts']+2*self.stats['clear_successes'])
        result=dict(status=status,error=error,problem=self.config.problem,config=asdict(self.config),
                    planning=self.options,prior=asdict(self.prior),**self.stats,
                    total_virtual_time_s=self.client.virtual_time,independently_accounted_time_s=computed,
                    timing_residual_s=self.client.virtual_time-computed,
                    average_clear_time_s=self.client.virtual_time/self.stats['clear_successes'] if self.stats['clear_successes'] else None,
                    program_real_time_s=time.monotonic()-start,stop_evidence=self.stop_evidence,
                    source_total=None,clear_fraction=None)
        self._record('finish',result=result)
        return result


def make_solver(client, problem, spec=None, decision_log=None):
    """Same factory signature as existing methods; no case/truth parameter."""
    spec=dict(spec or {})
    allowed={'name','implementation','problems','limit','planner','prior'}
    if set(spec)-allowed: raise ValueError(f'unknown policy fields: {sorted(set(spec)-allowed)}')
    if spec.get('implementation','dynamic_joint')!='dynamic_joint':
        raise ValueError('implementation must be dynamic_joint')
    if problem not in spec.get('problems',[3,4]): raise ValueError('policy does not support problem')
    planner=dict(spec.get('planner',{}))
    if 'limit' in spec:
        if 'local_limit' in planner and planner['local_limit']!=spec['limit']:
            raise ValueError('conflicting local limits')
        planner['local_limit']=spec['limit']
    options=validated_options(planner)
    config=SolverConfig(problem=problem,local_measure_limit=options['local_limit'])
    return DynamicJointSolver(client,config,decision_log,options=options,prior=spec.get('prior'))
