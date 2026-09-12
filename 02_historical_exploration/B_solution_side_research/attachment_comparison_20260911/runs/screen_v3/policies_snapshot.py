"""Local attachment comparison. Policies receive only a legal client and problem.

The attachment has no end-to-end controller. AttachmentSolver operationalizes
its P3 rules; it is NOT presented as an execution of an original absent solver.
All hard geometry and action semantics use the same conservative core.
"""
import math
import time
from pathlib import Path
import sys
import json

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'vendor'))
sys.path.insert(0, str(ROOT / 'attachment' / 'solution'))
import geom as attachment_geom
from bsolver.strategy import Solver, SolverConfig
from bsolver.geometry import distance, minimum_enclosing_circle, max_distance
from bsolver.knowledge import InconsistentKnowledge
from bsolver.sensing import choose_measurement
from bsolver.refined_local import hull_cell_cover, RefinedLocalSolver
from bsolver.nosignal_sensing import choose_measurement_nosignal
from methods.solver import make_solver as side_solver
from methods.scheduling import closest_hull_point, RouteMixin
from diverse.policies import ring_certificate


def disk_coverage_certificate(stations, min_half=0.75, node_limit=12000):
    """Conservative continuous certificate, NOT a sample-only stopping test.

    Each square is discarded if outside D, or wholly in one measured 1000m
    disk via distance(center, station)+half-diagonal. Unresolved leaves mean
    not certified. A strict 1e-5m guard absorbs floating arithmetic here.
    """
    if not stations:
        return False, {'nodes': 0, 'unresolved': 1}
    stack = [(0., 0., 1800.)]
    nodes = 0
    while stack:
        x, y, h = stack.pop(); nodes += 1
        dx, dy = max(abs(x)-h, 0.), max(abs(y)-h, 0.)
        if math.hypot(dx, dy) > 1800.+1e-5:
            continue
        nearest = min(math.hypot(x-a, y-b) for a, b in stations)
        if nearest+h*math.sqrt(2.) < 1000.-1e-5:
            continue
        if h <= min_half or nodes >= node_limit:
            return False, {'nodes': nodes, 'unresolved': len(stack)+1,
                           'unresolved_center': [x,y], 'half_side_m': h}
        hh = h/2
        stack.extend((x+sx*hh,y+sy*hh,hh) for sx in (-1,1) for sy in (-1,1))
    return True, {'nodes': nodes, 'unresolved': 0,
                  'proof': 'D is contained in certified quadtree boxes covered by actual per-channel measurement disks'}


_SEARCH_SAMPLES = [(float(x),float(y)) for x in range(-1800,1801,100)
                   for y in range(-1800,1801,100) if x*x+y*y <= 1800**2]
_SEARCH_SAMPLES += [(1800*math.cos(math.radians(a)),1800*math.sin(math.radians(a)))
                    for a in range(360)]


def cover_plan(knowledge, start):
    return hull_cell_cover(knowledge.hull, start,
                           failed_positions=knowledge.failed_clear_positions)


def execute_cover(policy, knowledge, plan=None):
    plan = plan or cover_plan(knowledge, policy.client.position)
    policy.stats['fallback_targets'] += 1
    policy._record('attachment_cover_plan', target_channel=knowledge.channel,
                   plan=plan.summary())
    for cell in plan.cells:
        if cell.excluded_by_failures(knowledge.failed_clear_positions):
            continue
        if policy._clear(knowledge.channel, cell.center,
                         {'type': 'optical_grid_attempt', 'construction': 'common_current_hull',
                          'cell_count': len(plan.cells)}):
            return
    raise InconsistentKnowledge('finite optical cover exhausted')


def attachment_measure_point(knowledge, current, standoff=200.):
    """Attachment: orthogonal second station, then approach along service path.

    The estimated center is a proposal only, never a true target or clear
    certificate. P3 candidates use a conservative guaranteed-distance filter.
    P4 does not inherit that filter as a visibility guarantee.
    """
    center = attachment_geom.centroid(knowledge.hull)
    positives = knowledge.positive_positions
    candidates = []
    if len(positives) <= 1:
        origin = positives[0]
        d = distance(origin, center)
        if d > 1e-9:
            ux, uy = (center[0]-origin[0])/d, (center[1]-origin[1])/d
            for radius in (min(d,800.), 400., 200.):
                for sign in (-1,1):
                    candidates.append((center[0]-sign*radius*uy,
                                       center[1]+sign*radius*ux))
    else:
        d = distance(current, center)
        if d > 1e-9:
            step = max(0.,d-standoff)/d
            candidates.append((current[0]+step*(center[0]-current[0]),
                               current[1]+step*(center[1]-current[1])))
        candidates.append(tuple(center))
    candidates = [q for q in candidates
                  if all(distance(q, o.position) > .1 for o in knowledge.observations)]
    if knowledge.problem == 3:
        candidates = [q for q in candidates if max_distance(knowledge.hull,q) < 999.999]
    if candidates:
        # Favor the longest affordable orthogonal baseline, then its nearer side.
        if len(positives) <= 1:
            scale = max(distance(center,q) for q in candidates)
            candidates = [q for q in candidates if distance(center,q) > scale-1e-6]
        return min(candidates, key=lambda q: distance(current,q))
    q, _ = choose_measurement(knowledge,current,'active',4.,2.)
    return q


class AttachmentSolver(Solver):
    """P3 lazy coverage and pooled stop-point measurements, finite continuation."""
    def __init__(self, client, spec):
        super().__init__(client,SolverConfig(problem=3,local_measure_limit=2))
        self.spec = spec
        self.sweep_positions = []
        self.completed_sweeps = 0
        self.cover_cache = None
        self.lazy_count = 0
        self.backup_index = 0
        self.ring = float(spec.get('ring',1260.))
        self.ring_proof = ring_certificate(self.ring)
        self.backup_points = [(0.,0.)]+[(self.ring*math.cos(k*math.pi/3),
                                       self.ring*math.sin(k*math.pi/3)) for k in range(6)]
        self.stats.update(shared_measures=0,lazy_stations=0,approach_measures=0,
                          local_direction=0,local_no_signal=0)

    def _known_count(self):
        return sum(k.status in ('detected','cleared') for k in self.channels.values())

    def _sweep(self, point):
        unknown = [k.channel for k in self.channels.values() if k.status == 'unknown']
        if self._known_count() >= 16:
            unknown = []
        if unknown and self.sweep_positions and self.spec.get('coverage_gain_gate',0)>0:
            new_cover=sum(distance(point,q)<999.99 and
                          all(distance(q,p)>1000. for p in self.sweep_positions)
                          for q in _SEARCH_SAMPLES)
            if new_cover/len(_SEARCH_SAMPLES)<self.spec['coverage_gain_gate']:
                # This skips an optional scan, never pretends its region is covered.
                # Forced new lazy/fixed search stations bypass this efficiency gate.
                if not getattr(self,'forced_sweep',False):unknown=[]
        known = []
        if self.spec.get('shared',True):
            cap = self.spec.get('shared_cap',100)
            known = [k.channel for k in self._detected() if len(k.positive_positions) < cap
                     and all(distance(point,o.position) > .1 for o in k.observations)]
        channels = list(dict.fromkeys(unknown+known))
        if self.client.current_channel in channels:
            channels.remove(self.client.current_channel);channels.insert(0,self.client.current_channel)
        for ch in channels:
            if self.channels[ch].status == 'cleared':continue
            if self.channels[ch].status == 'unknown' and self._known_count() >= 16:continue
            self._measure(ch,point,'attachment_sweep')
            if ch in known:self.stats['shared_measures'] += 1
        # Every still unknown channel was measured at this exact location.
        if unknown:
            self.sweep_positions.append(tuple(point));self.completed_sweeps += 1
            self.cover_cache = None

    def _complete_evidence(self):
        cleared = sorted(k.channel for k in self.channels.values() if k.status == 'cleared')
        if len(cleared) == 16:
            return {'type':'known_upper_bound','cleared_channels':cleared}
        if self._detected():return None
        unknown = [k for k in self.channels.values() if k.status == 'unknown']
        if not unknown:return {'type':'all_channels_resolved','cleared_channels':cleared}
        if self.cover_cache is None:
            self.cover_cache = disk_coverage_certificate(self.sweep_positions)
        good, proof = self.cover_cache
        if not good:return None
        for k in unknown:
            # Explicit per-channel check prevents using an unmeasured visit.
            measured = {o.position for o in k.observations if o.result == 'no_signal'}
            if any(p not in measured for p in self.sweep_positions):
                raise InconsistentKnowledge('coverage position missing for unknown channel')
            k.status = 'absent'
        return {'type':'per_channel_lazy_disk_cover','cleared_channels':cleared,
                'absent_channels':[k.channel for k in unknown],
                'stations':self.sweep_positions,'certificate':proof}

    def _localize(self, channel):
        k = self.channels[channel]
        if self._try_certified_clear(k):return
        for attempt in range(self.spec.get('local_limit',2)):
            plan = cover_plan(k,self.client.position)
            if len(plan.cells) <= self.spec.get('sweep_threshold',3):
                execute_cover(self,k,plan);return
            if self.spec.get('first_sensing')=='active' and len(k.positive_positions)<=1:
                q,_=choose_measurement(k,self.client.position,'active',4.,2.)
            else:
                q = attachment_measure_point(k,self.client.position,self.spec.get('standoff',200.))
            if q is None:break
            self._record('attachment_measure_choice',target_channel=channel,point=q,
                         current_cover_cells=len(plan.cells),current_cover_bound_s=plan.cost_bound_s)
            response=self._measure(channel,q,'attachment_local')
            self.stats['approach_measures'] += 1
            self.stats['local_'+('no_signal' if response['measure_result']=='no_signal' else 'direction')] += 1
            if k.status == 'cleared' or self._try_certified_clear(k):return
        execute_cover(self,k)

    def _next_station(self):
        if self.spec.get('lazy',True) and self.lazy_count < 12:
            if self.spec.get('lazy_cost',False):
                uncovered=[q for q in _SEARCH_SAMPLES if all(distance(q,p)>1000. for p in self.sweep_positions)]
                if uncovered:
                    ranked=sorted(uncovered,key=lambda q:min(distance(q,p) for p in self.sweep_positions),reverse=True)
                    deepest=[]
                    for q in ranked:
                        if all(distance(q,p)>250. for p in deepest):deepest.append(q)
                        if len(deepest)>=16:break
                    options=[]
                    n_unknown=sum(k.status=='unknown' for k in self.channels.values())
                    current=self.client.position
                    for deep in deepest:
                        d=distance(current,deep);pull=min(800.,.95*d)
                        q=(deep[0]+pull*(current[0]-deep[0])/d,
                           deep[1]+pull*(current[1]-deep[1])/d)
                        gain=sum(distance(q,p)<999.99 for p in uncovered)
                        if gain and all(distance(q,p)>1. for p in self.sweep_positions):
                            options.append(((distance(current,q)/5+6*n_unknown)/gain,q))
                    if options:
                        self.lazy_count+=1;self.stats['lazy_stations']+=1
                        return min(options)[1]
            # Grid samples propose an action; only the continuous certificate stops.
            deep = max(_SEARCH_SAMPLES,key=lambda q:min(distance(q,p) for p in self.sweep_positions))
            anchor = min(self.sweep_positions,key=lambda p:distance(p,deep))
            d=distance(anchor,deep);pull=min(800.,.95*d)
            q=(deep[0]+pull*(anchor[0]-deep[0])/d,
               deep[1]+pull*(anchor[1]-deep[1])/d)
            if all(distance(q,p)>1. for p in self.sweep_positions):
                self.lazy_count += 1;self.stats['lazy_stations'] += 1
                return q
        while self.backup_index < len(self.backup_points):
            q=self.backup_points[self.backup_index];self.backup_index += 1
            if all(distance(q,p)>.1 for p in self.sweep_positions):return q
        raise InconsistentKnowledge('fixed certified ring exhausted without cover evidence')

    def run(self):
        start=time.monotonic();status='incomplete';error=None
        try:
            if not self.client.entered:self.client.enter()
            self._record('start',variant='attachment_operationalization',spec=self.spec)
            self._sweep((0.,0.))
            for _ in range(80):
                self.stop_evidence=self._complete_evidence()
                if self.stop_evidence:status='complete';break
                known=self._detected()
                if known:
                    def target_distance(k):
                        q=(closest_hull_point(k.hull,self.client.position) if self.spec.get('service')=='hull'
                           else attachment_geom.centroid(k.hull))
                        return distance(self.client.position,q),k.channel
                    chosen=min(known,key=target_distance)
                    self._localize(chosen.channel)
                    if self._known_count()<16 or self._detected():self._sweep(self.client.position)
                else:
                    self.forced_sweep=True
                    self._sweep(self._next_station())
                    self.forced_sweep=False
            if status!='complete':error='bounded outer loop exhausted'
        except Exception as exc:
            error=f'{type(exc).__name__}: {exc}'
        finally:
            if self.client.entered and not self.client.exited:self.client.exit()
        return {'status':status,'error':error,'program_real_time_s':time.monotonic()-start,
                'stop_evidence':self.stop_evidence,**self.stats}


class OnRouteRescue(RefinedLocalSolver):
    """P4-only transfer: keep 25-point search; add at most one approach measure.

    No P3 absence disk rule is reused. A target with <=3 cells is cleared now.
    An expensive cover (>12 cells) is considered for a <=1-rescue approach;
    the experimental gate limits extra travel relative to immediate first cell.
    """
    def __init__(self,*args,spec=None,**kwargs):
        super().__init__(*args,**kwargs);self.spec=spec or {}
        self.stats.update(rescue_measures=0,rescue_no_signal=0)

    def _localize(self,channel):
        k=self.channels[channel]
        if k.status=='cleared' or self._try_certified_clear(k):return
        q,selection=choose_measurement_nosignal(k,self.client.position,self.config.sensing,
                    self.config.bin_width_deg,self.config.radius_weight,config=self.nosignal_config)
        if q is not None:
            self._record_selector(channel,selection);self._measure(channel,q,'local_active_sensing')
            if k.status=='cleared' or self._try_certified_clear(k):return
        plan=cover_plan(k,self.client.position)
        q=attachment_measure_point(k,self.client.position,self.spec.get('standoff',200.))
        if q is not None and len(plan.cells)>self.spec.get('cell_threshold',12):
            first=plan.cells[0].center
            extra=(distance(self.client.position,q)+distance(q,first)-distance(self.client.position,first))/5+5
            # Cost gate is a heuristic, not a guaranteed information-gain claim.
            if extra < self.spec.get('gate_s',60.):
                self._record('attachment_rescue_choice',target_channel=channel,point=q,
                             current_cells=len(plan.cells),incremental_first_clear_proxy_s=extra)
                response=self._measure(channel,q,'attachment_rescue')
                self.stats['rescue_measures']+=1
                self.stats['rescue_no_signal']+=int(response['measure_result']=='no_signal')
                if k.status=='cleared' or self._try_certified_clear(k):return
                plan=cover_plan(k,self.client.position)
        execute_cover(self,k,plan)


SPECS={
 'main':{'kind':'main','problems':[3,4]},
 'side':{'kind':'side','problems':[3,4]},
 'attachment':{'kind':'attachment','problems':[3], 'shared':True,'lazy':True,
               'local_limit':2,'sweep_threshold':3,'standoff':200.,'ring':1260.},
 'p4_transfer':{'kind':'p4_transfer','problems':[4], 'standoff':200.,'cell_threshold':12,'gate_s':60.},
}
SPECS['attachment_cap2']={**SPECS['attachment'],'shared_cap':2}
SPECS['attachment_selective']={**SPECS['attachment'],'coverage_gain_gate':.03}
SPECS['attachment_slim']={**SPECS['attachment'],'shared_cap':2,'coverage_gain_gate':.03}
SPECS['attachment_active']={**SPECS['attachment_cap2'],'first_sensing':'active'}
SPECS['attachment_cost']={**SPECS['attachment_cap2'],'lazy_cost':True}
SPECS['attachment_active_cost']={**SPECS['attachment_active'],'lazy_cost':True}
SPECS['p4_route']={'kind':'p4_route','problems':[4]}
SPECS['p4_route_rescue']={**SPECS['p4_transfer'],'kind':'p4_route_rescue'}


def make_policy(client,problem,name):
    spec=SPECS[name]
    if problem not in spec['problems']:raise ValueError('inapplicable policy')
    if spec['kind']=='attachment':return AttachmentSolver(client,spec)
    if spec['kind']=='side':
        picked=({'limit':1,'cover':'bbox','reorder':True,'ring':1150.,
                  'schedule_options':{'reorder':True,'service_score':'hull','shared':False}}
                if problem==3 else
                {'limit':2,'cover':'bbox','reorder':True,
                 'schedule_options':{'reorder':True,'service_score':'hull','shared':False},
                 'config':{'joint_detour_m':1200.}})
        return side_solver(client,problem,picked)
    if spec['kind']=='main' and problem==3:
        return Solver(client,SolverConfig(problem=3,local_measure_limit=1))
    bundle=json.loads((ROOT/'main_policy_bundle.json').read_text(encoding='utf-8'))
    if spec['kind']=='main':
        from run_p4_refinement import make_policy as frozen_factory
        return frozen_factory(client,'combined_cover',bundle,bundle,None)
    from bsolver.nosignal_sensing import NoSignalConfig
    config=SolverConfig(**bundle['current_spec']['solver_config'])
    if spec['kind']=='p4_route':
        class RoutedCover(RouteMixin,RefinedLocalSolver):pass
        p=RoutedCover(client,config,mode='cover',nosignal_config=NoSignalConfig())
        p.schedule_options={'reorder':True,'service_score':'hull','shared':False}
    elif spec['kind']=='p4_route_rescue':
        class RoutedRescue(RouteMixin,OnRouteRescue):pass
        p=RoutedRescue(client,config,spec=spec,nosignal_config=NoSignalConfig())
        p.schedule_options={'reorder':True,'service_score':'hull','shared':False}
    else:
        p=OnRouteRescue(client,config,spec=spec,nosignal_config=NoSignalConfig())
    p.points=[tuple(q) for q in bundle['refinement']['refined_route']]
    return p
