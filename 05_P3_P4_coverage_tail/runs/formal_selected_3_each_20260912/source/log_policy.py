"""Log-guided ablations against the latest official-tested count/recovery release."""
import copy, math
from previous_policy import NextSolver as PreviousSolver, selection, CANDIDATES as OLD_ARMS
from run import base_specs
from feedback.solver import ARMS, install_refined_cover
from feedback.belief import hypotheses, compact
from bsolver.strategy import SolverConfig
from feedback import planner
from bsolver.knowledge import InconsistentKnowledge
from methods.scheduling import closest_hull_point

CANDIDATES={
    'current':{},
    'center':{'expected_service':True},
    'opportunity':{'station_information':True},
    'transit':{'expected_service':True,'station_information':True,'future_service':True},
    'sweep_sense':{'sweep_information':True},
    'shared_sweep':{'station_information':True,'sweep_information':True},
    'shared_center':{'expected_service':True,'station_information':True},
    'transit_sweep':{'expected_service':True,'station_information':True,'future_service':True,'sweep_information':True},
    'bounded_transit':{'expected_service':True,'station_information':True,'future_service':True,'deferral_horizon':3},
    'target_info':{'station_information':True,'selected_target_only':True},
    'target_sweep':{'station_information':True,'selected_target_only':True,'sweep_information':True},
    'shared_stable':{'station_information':True,'stable_service_ranking':True},
    'shared_commit':{'station_information':True,'joint_detour_multiplier':1.75},
    'intersection_probe':{'intersection_probe':True},
    'shared_probe':{'station_information':True,'intersection_probe':True},
    'count_transit':{'expected_service':True,'station_information':True,'future_service':True,'deferral_horizon':3,'count_route':True},
    'late_count_transit':{'expected_service':True,'station_information':True,'future_service':True,'deferral_horizon':3,'count_route':True,'count_route_min_known':12},
}

class LogSolver(PreviousSolver):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self._cloud_cache={}; self._shared_count={}
        self._routing_before_info={}
        self._intersection_tried=set()
        self.stats.update(station_information_measures=0,future_service_deferrals=0)
        self.stats.update(sweep_information_measures=0)
        self.stats.update(intersection_probes=0,intersection_successes=0)

    def _cloud(self,k):
        item=self._cloud_cache.get(k.channel)
        if item is None or item[0]!=k.revision:
            item=(k.revision,compact(hypotheses(k,self.prior),24));self._cloud_cache[k.channel]=item
        return item[1]

    def _expected_position(self,k):
        cloud=self._cloud(k)
        return tuple(sum(s.weight*s.position[i] for s in cloud) for i in (0,1)) if cloud else self._route_geometry(k)[0]

    def _route_reorder_suffix(self,start):
        if self._known_count()<self.feedback_options.get('count_route_min_known',0):
            model=self.count_model;self.count_model=None
            try:return super()._route_reorder_suffix(start)
            finally:self.count_model=model
        return super()._route_reorder_suffix(start)

    def _route_destination(self,k,score):
        if self.feedback_options.get('stable_service_ranking') and k.channel in self._routing_before_info:
            # Supplemental information drives localization, while the established
            # routing heuristic retains its original proxy until this target is
            # selected. This proxy is never used as a clearance certificate.
            hull=self._routing_before_info[k.channel]
            return closest_hull_point(hull,self.client.position),5.+int(self.client.current_channel!=k.channel),'pre_information_route_proxy'
        point,cost,kind=super()._route_destination(k,score)
        if self.feedback_options.get('expected_service') and kind!='certified_clear_destination':
            return self._expected_position(k),cost,'posterior_mean_service_proxy'
        return point,cost,kind

    def _station_information(self,target=None):
        if not self.feedback_options.get('station_information'):return
        if self.feedback_options.get('selected_target_only') and target is None:return
        q=self.client.position
        for k in sorted(self._detected(),key=lambda k:k.channel):
            if self.feedback_options.get('selected_target_only') and k.channel!=target:continue
            if self._shared_count.get(k.channel,0)>=2 or not k.first_direction:continue
            if any(math.dist(q,o.position)<80 for o in k.observations):continue
            center,radius=self._route_geometry(k)
            if radius<30:continue
            useful=0.;vis=0.
            for s in self._cloud(k):
                d=math.dist(q,s.position)
                visible=d<s.radius
                if s.orientation is not None:
                    t=math.radians(s.orientation)
                    visible=visible and (q[0]-s.position[0])*math.cos(t)+(q[1]-s.position[1])*math.sin(t)>0
                if not visible:continue
                vis+=s.weight
                p=k.first_direction.position
                a=(p[0]-s.position[0],p[1]-s.position[1]);b=(q[0]-s.position[0],q[1]-s.position[1])
                cross=abs(a[0]*b[1]-a[1]*b[0])/max(1.,math.hypot(*a)*d)
                uncertainty=math.radians(k.epsilon_deg)*max(d,math.hypot(*a))/max(.01,cross)
                if cross>.20 and uncertainty<.60*radius:useful+=s.weight
            if useful<.65:continue
            self._routing_before_info.setdefault(k.channel,list(k.hull))
            self._shared_count[k.channel]=self._shared_count.get(k.channel,0)+1
            self.stats['station_information_measures']+=1
            self._record('station_information_selection',target_channel=k.channel,visible_working_mass=vis,
                         useful_working_mass=useful,prior_radius_m=radius,extra_movement_m=0.)
            self._measure(k.channel,q,'station_information')

    def _localize(self,channel):
        self._station_information(channel)
        if self.channels[channel].status=='detected':return super()._localize(channel)

    def _fallback(self,k):
        if self.feedback_options.get('intersection_probe') and k.channel not in self._intersection_tried:
            center,radius=self._route_geometry(k)
            readings=[o for o in k.observations if o.result=='direction']
            if 20<radius<100 and len(readings)>=2:
                q=center
                for _ in range(3):
                    aa=ab=bb=bx=by=0.
                    for o in readings:
                        angle=math.radians(o.angle);nx=-math.sin(angle);ny=math.cos(angle)
                        w=1/max(20.,math.dist(q,o.position))**2
                        z=nx*o.position[0]+ny*o.position[1]
                        aa+=w*nx*nx;ab+=w*nx*ny;bb+=w*ny*ny;bx+=w*nx*z;by+=w*ny*z
                    det=aa*bb-ab*ab
                    if det<=1e-20:break
                    q=((bx*bb-by*ab)/det,(by*aa-bx*ab)/det)
                if k.region_contains(q):
                    cloud=self._cloud(k);points=planner.clear_plan(k,self.client.position,cloud,False)
                    mass=sum(s.weight for s in cloud if math.dist(s.position,q)<=20)
                    old=planner.expected_cover(points,self.client.position,cloud)
                    new=planner.expected_cover([q]+points,self.client.position,cloud)
                    if mass>=.25 and new<old-1:
                        self._intersection_tried.add(k.channel);self.stats['intersection_probes']+=1
                        self._record('intersection_probe_selection',target_channel=k.channel,working_success_mass=mass,
                                     baseline_cover_s=old,probe_cover_s=new,point=q)
                        if self._clear(k.channel,q,{'type':'bounded_intersection_probe'}):
                            self.stats['intersection_successes']+=1;return
                        if self._try_certified_clear(k):return
        if not self.feedback_options.get('sweep_information'):return super()._fallback(k)
        self.stats['fallback_targets']+=1
        points=planner.clear_plan(k,self.client.position,self._cloud(k),False)
        self._record('information_cover',target_channel=k.channel,point_count=len(points))
        failed=0;refreshes=0
        while points:
            q=points.pop(0)
            if not k.clearance_possible(q):
                self.stats['feedback_pruned_clear_points']+=1;continue
            if self._clear(k.channel,q,{'type':'feedback_region_optical_attempt'}):return
            failed+=1
            if self._try_certified_clear(k):return
            if failed%3 or refreshes>=2 or len(points)<4:continue
            if any(math.dist(q,o.position)<25 for o in k.observations):continue
            cloud=self._cloud(k)
            # A reading costs 5 s with no movement/switch. Require substantial
            # probability of reception before paying that cost during a sweep.
            visible=0.
            for s in cloud:
                if math.dist(q,s.position)>s.radius:continue
                if s.orientation is not None:
                    t=math.radians(s.orientation)
                    if (q[0]-s.position[0])*math.cos(t)+(q[1]-s.position[1])*math.sin(t)<0:continue
                visible+=s.weight
            if visible<.4:continue
            refreshes+=1;self.stats['sweep_information_measures']+=1
            self._record('sweep_information_selection',target_channel=k.channel,visible_working_mass=visible,
                         remaining_points=len(points),extra_movement_m=0.)
            self._measure(k.channel,q,'sweep_information')
            if k.status=='cleared' or self._try_certified_clear(k):return
            points=planner.clear_plan(k,self.client.position,self._cloud(k),False)
            self._record('information_cover_restart',target_channel=k.channel,point_count=len(points))
        raise InconsistentKnowledge('information cover exhausted')

    def _joint_service(self,next_point,force=False):
        self._station_information()
        if not self.feedback_options.get('future_service'):return super()._joint_service(next_point,force)
        if self.count_model:self._record('count_posterior',posterior=self.count_model.posterior(self.channels))
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
                detour=math.dist(q,p)
                if next_point is not None:detour+=math.dist(p,next_point)-math.dist(q,next_point)
                defer=False;future=None
                if not force and suffix is not None and detour>300:
                    rest=self.points[suffix:]
                    horizon=self.feedback_options.get('deferral_horizon')
                    if horizon is not None:
                        # Only credit nearby opportunities. Distant stations can
                        # disappear when the public upper bound of 16 is reached.
                        horizon=min(horizon,2) if self._known_count()>=14 else horizon
                        rest=rest[:horizon]
                    future=min([math.dist(a,p)+math.dist(p,b)-math.dist(a,b) for a,b in zip(rest,rest[1:])]+[math.dist(rest[-1],p)])
                    defer=detour>future+200
                if defer:
                    self.stats['future_service_deferrals']+=1
                    self._record('defer_to_future_route',target_channel=k.channel,now_detour_m=detour,future_detour_m=future)
                    continue
                choices.append((detour/5+cost,k.channel,detour,kind))
            if not choices:return
            cost,c,detour,kind=min(choices)
            if not force and detour>self.config.joint_detour_m:return
            self._record('transit_service_selection',target_channel=c,estimated_incremental_cost_s=cost,proxy_kind=kind)
            serviced.add(c);self._localize(c)
            if self._complete_evidence():return
            if self._final_phase():next_point=None;force=True;suffix=None
            if suffix is not None:next_point=self._route_reorder_suffix(suffix)

def make_solver(client,problem,base_spec=None,arm='current',*,prior=None,options=None,decision_log=None):
    selected=selection()['policies'][str(problem)]
    old=OLD_ARMS['count_route' if problem==3 else 'recovery']
    opts={**ARMS[selected['arm']],**selected['options'],**old,**CANDIDATES[arm],**(options or {})}
    spec=copy.deepcopy(base_specs()[problem])
    config=SolverConfig(problem=problem,local_measure_limit=spec.get('limit',1))
    for k,v in spec.get('config',{}).items():setattr(config,k,v)
    config.joint_detour_m*=opts.get('joint_detour_multiplier',1.)
    solver=LogSolver(client,config,decision_log,spec=spec,feedback_options=opts,prior=None)
    if spec.get('refined_global'):install_refined_cover(solver)
    return solver
