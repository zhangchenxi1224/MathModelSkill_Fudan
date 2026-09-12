"""Coverage reuse and uncertainty-aware short-horizon service experiments."""
import copy,math
import json
from pathlib import Path
from log_policy import LogSolver,CANDIDATES as LOG_ARMS
from previous_policy import CANDIDATES as OLD_ARMS,selection
from feedback.solver import ARMS,install_refined_cover
from bsolver.strategy import SolverConfig
from run import base_specs
from continuum_cover import ContinuumCover

CANDIDATES={
 'current':{},
 'robust_deferral':{'robust_deferral':True},
 'tight_route':{'route_length_ratio':1.0},
 'substitute':{'coverage_reuse':True},
 'substitute_robust':{'coverage_reuse':True,'robust_deferral':True},
 'substitute_tight':{'coverage_reuse':True,'route_length_ratio':1.0},
 'ring_cover':{'ring_cover':True},
 'ring_robust':{'ring_cover':True,'robust_deferral':True},
 'ring_tight':{'ring_cover':True,'route_length_ratio':1.0},
 'ring_search':{'ring_cover':True,'opportunity_search':True},
 'ring_search_tight':{'ring_cover':True,'opportunity_search':True,'route_length_ratio':1.0},
}

class TailSolver(LogSolver):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.cover=ContinuumCover(self.config.problem)
        self.pruned=False;self.extra_checks=set();self.deferrals={}
        self.stats.update(replaced_stations=0,substitution_measures=0,continuum_absent=0,robust_deferrals=0)
        self.search_positions=set()
        self.stats.update(opportunity_search_measures=0,opportunity_search_discoveries=0)

    def _negatives(self,k):
        return {tuple(o.position) for o in k.observations if o.result=='no_signal'}

    def _certify_absent(self):
        if not self.feedback_options.get('coverage_reuse'):return
        for k in self.channels.values():
            if k.status!='unknown':continue
            points=self._negatives(k)
            if self.cover.prove(points):
                k.status='absent';self.stats['continuum_absent']+=1
                k.absence_proof={'type':'continuum_no_signal_cover','samples':sorted(points),
                                 'minimum_radius_m':1000.,'directional_convex_hull':self.config.problem==4}
                self._record('continuum_absence',target_channel=k.channel,certificate=k.absence_proof)

    def _complete_evidence(self):
        if not self.feedback_options.get('coverage_reuse'):return super()._complete_evidence()
        self._certify_absent()
        cleared=[f for f,k in self.channels.items() if k.status=='cleared']
        if len(cleared)>=16:return super()._complete_evidence()
        if self._detected():return None
        if all(k.status in ('cleared','absent') for k in self.channels.values()):
            return {'type':'continuum_no_signal_cover','cleared_channels':cleared,
                    'absent_channels':[f for f,k in self.channels.items() if k.status=='absent'],
                    'certificates':{str(f):k.absence_proof for f,k in self.channels.items() if k.status=='absent'}}
        return None if self.pruned else super()._complete_evidence()

    def _substitute(self,start):
        if not self.feedback_options.get('coverage_reuse') or start is None or len(self.points)-start<2:return
        q=tuple(self.client.position)
        # Only exploit new stopping points after paid localization movement.
        if q in self.extra_checks or any(math.dist(q,p)<80 for p in self.points):return
        self.extra_checks.add(q)
        unknown=[k for k in self.channels.values() if k.status=='unknown']
        if not unknown or self._known_count()>=16:return
        common=set.intersection(*(self._negatives(k) for k in unknown))
        rest=self.points[start:]
        candidates=sorted(range(len(rest)),key=lambda i:math.dist(q,rest[i]))[:3]
        for j in candidates:
            if math.dist(q,rest[j])>600:continue
            before=q if j==0 else rest[j-1]
            after=rest[j+1] if j+1<len(rest) else None
            saving=math.dist(before,rest[j])+(math.dist(rest[j],after)-math.dist(before,after) if after else 0)
            if saving<100:continue
            future=rest[:j]+rest[j+1:]
            if not self.cover.prove(common|set(future)|{q}):continue
            self._record('coverage_substitution_proposal',removed_point=rest[j],replacement_point=q,
                         route_saving_proxy_m=saving,unknown_channels=[k.channel for k in unknown])
            for k in unknown:
                if self._known_count()>=16:break
                if k.status!='unknown' or q in self._negatives(k):continue
                self.stats['substitution_measures']+=1
                self._measure(k.channel,q,'coverage_substitution')
            # A positive result changes the task set; only unknown channels need the cover.
            remaining=[k for k in self.channels.values() if k.status=='unknown']
            if all(self.cover.prove(self._negatives(k)|set(future)) for k in remaining):
                self.pruned=True;self.stats['replaced_stations']+=1
                del self.points[start+j]
                self._record('coverage_station_replaced',point=rest[j],remaining_stations=len(self.points)-start)
            return

    def _route_reorder_suffix(self,start):
        self._search_opportunity()
        self._substitute(start)
        return super()._route_reorder_suffix(start)

    def _search_opportunity(self):
        if not self.feedback_options.get('opportunity_search') or not self.count_model:return
        if not 12<=self._known_count()<16 or len(self.search_positions)>=3:return
        q=tuple(self.client.position)
        if q in self.search_positions or any(math.dist(q,p)<100 for p in self.points):return
        state=self.count_model.posterior(self.channels);vis=self.count_model.masks(q)
        choices=[]
        for f in state['unknown']:
            mass=self.count_model.mass((self.count_model.alive[f][0]&vis[0],self.count_model.alive[f][1]&vis[1]))
            gain=state['presence'][f]*mass/state['likelihood'][f]
            if gain>.025:choices.append((gain,f))
        if sum(g for g,f in choices)<.25:return
        self.search_positions.add(q)
        self._record('opportunity_search_selection',expected_discoveries=sum(g for g,f in choices),position=q,
                     count_posterior=state['N'],channels=[f for g,f in choices])
        for gain,f in sorted(choices,reverse=True):
            if self._known_count()>=16:break
            if self.channels[f].status!='unknown':continue
            self.stats['opportunity_search_measures']+=1
            response=self._measure(f,q,'opportunity_unknown_search')
            self.stats['opportunity_search_discoveries']+=int(response['measure_result']!='no_signal')

    def _joint_service(self,next_point,force=False):
        if not self.feedback_options.get('robust_deferral') or not self.feedback_options.get('future_service'):
            return super()._joint_service(next_point,force)
        self._station_information()
        if self._final_phase():next_point=None;force=True
        suffix=self.points.index(next_point) if next_point is not None else None
        if suffix is not None:next_point=self._route_reorder_suffix(suffix)
        serviced=set()
        while self._detected():
            self.feedback_options['next_goal']=next_point
            choices=[]
            for k in self._detected():
                if k.channel in serviced:continue
                p,cost,kind=self._route_destination(k,'hull');q=self.client.position
                def detour(point,a,b):return math.dist(a,point)+(math.dist(point,b)-math.dist(a,b) if b is not None else 0)
                now=detour(p,q,next_point);defer=False
                if not force and suffix is not None and now>300:
                    horizon=2 if self._known_count()>=14 else 3
                    rest=self.points[suffix:suffix+horizon]
                    edges=list(zip(rest,rest[1:]))
                    # Last vertex is not a promised terminal: do not credit a one-way trip there.
                    if edges:
                        gains=[]
                        for s in self._cloud(k):
                            future=min(detour(s.position,a,b) for a,b in edges)
                            gains.append((detour(s.position,q,next_point)-future,s.weight))
                        mass=sum(w for g,w in gains if g>200)
                        mean=sum(g*w for g,w in gains)
                        first=self.deferrals.get(k.channel,suffix)
                        defer=mass>=.8 and mean>250 and suffix-first<2
                        if defer:
                            self.deferrals.setdefault(k.channel,suffix)
                            self.stats['future_service_deferrals']+=1;self.stats['robust_deferrals']+=1
                            self._record('robust_service_deferral',target_channel=k.channel,working_advantage_mass=mass,
                                         expected_saved_m=mean,first_deferred_index=first,current_index=suffix)
                if not defer:choices.append((now/5+cost,k.channel,now,kind))
            if not choices:return
            cost,c,detour,kind=min(choices)
            if not force and detour>self.config.joint_detour_m:return
            self._record('robust_transit_selection',target_channel=c,estimated_incremental_cost_s=cost,proxy_kind=kind)
            serviced.add(c);self._localize(c)
            if self._complete_evidence():return
            if self._final_phase():next_point=None;force=True;suffix=None
            if suffix is not None:next_point=self._route_reorder_suffix(suffix)


def make_solver(client,problem,base_spec=None,arm='current',*,prior=None,options=None,decision_log=None):
    selected=selection()['policies'][str(problem)]
    old=OLD_ARMS['count_route' if problem==3 else 'recovery']
    deployed=LOG_ARMS['opportunity' if problem==3 else 'count_transit']
    opts={**ARMS[selected['arm']],**selected['options'],**old,**deployed,**CANDIDATES[arm],**(options or {})}
    spec=copy.deepcopy(base_specs()[problem]);config=SolverConfig(problem=problem,local_measure_limit=spec.get('limit',1))
    for k,v in spec.get('config',{}).items():setattr(config,k,v)
    solver=TailSolver(client,config,decision_log,spec=spec,feedback_options=opts,prior=None)
    if spec.get('refined_global'):install_refined_cover(solver)
    if opts.get('ring_cover') and problem==4:
        candidates=json.loads((Path(__file__).parent/'reports/geometry_cover_candidates.json').read_text(encoding='utf-8'))
        chosen=min(candidates,key=lambda x:x['full_unknown_cost_s'])
        solver.points=[tuple(q) for q in chosen['points']]
        if not solver.cover.prove(solver.points):raise ValueError('ring coverage certificate did not verify')
        solver.refined_proof={'certificate_type':'continuous_cell_convex_hull_reception_cover',
                             'domain_radius_m':1800.,'minimum_reception_radius_m':1000.,
                             'point_count':len(solver.points),'open_route_length_m':chosen['length_m'],
                             'points':solver.points,'description':'Every position cell lies strictly inside the convex hull of stations within1000m of every cell vertex. Thus every orientation receives at least one station.'}
    return solver
