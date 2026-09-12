"""Round candidates: deployable feedback only; scenario labels never enter here."""
import copy
import json
import math
from pathlib import Path
from run import base_specs
from feedback.solver import FeedbackSolver,ARMS,install_refined_cover
from feedback import planner
from feedback.fast_cover import build_cover
from feedback.belief import hypotheses,compact
from bsolver.strategy import SolverConfig
from bsolver.knowledge import InconsistentKnowledge
from bsolver.coverage import order_route,route_length
from count_belief import CountBelief

ROOT=Path(__file__).resolve().parent
planner.build_cover=build_cover
def selection():return json.loads((ROOT/'selection.json').read_text(encoding='utf-8'))
CANDIDATES={
 'current':{},
 'fine':{'adaptive_bins':True,'angle_bin_deg':4.},
 'diverse':{'diverse_candidates':True},
 'resweep':{'resweep':True},
 'count_route':{'count_route':True},
 'count_joint':{'count_route':True,'count_joint':True,'terminal_weight':.35},
 'count_tight':{'count_route':True,'route_length_ratio':1.0},
 'count_idle':{'count_route':True,'route_length_ratio':1.0,'route_when_no_detected':True},
 'fair_roots':{'balanced_roots':True},
 'fair_fine':{'balanced_roots':True,'adaptive_bins':True,'angle_bin_deg':4.},
 'phase':{'adaptive_phase':True},
 'phase_count':{'adaptive_phase':True,'count_route':True},
 'recovery':{'mirror_recovery':True,'resweep':True},
 'local_combo':{'adaptive_bins':True,'angle_bin_deg':4.,'diverse_candidates':True,'resweep':True},
 'joint_combo':{'adaptive_bins':True,'angle_bin_deg':4.,'diverse_candidates':True,'resweep':True,'count_route':True,'count_joint':True,'terminal_weight':.35},
}


class NextSolver(FeedbackSolver):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.count_model=None
        self.phase_fixed=False
        if self.feedback_options.get('count_route'):
            prior=json.loads((ROOT/'inputs/count_prior.json').read_text(encoding='utf-8'))['policies'][str(self.config.problem)]
            self.count_model=CountBelief(self.config.problem,prior)
        self.stats.update(resweep_checks=0,resweep_measures=0,count_route_changes=0)

    def _measure(self,channel,point,*args,**kwargs):
        response=super()._measure(channel,point,*args,**kwargs)
        if self.count_model:
            self.count_model.observe(channel,point,response)
        return response

    def _joint_service(self,next_point,force=False):
        self.feedback_options['next_goal']=next_point
        old=self.config.joint_detour_m
        if self.count_model:
            belief=self.count_model.posterior(self.channels)
            self._record('count_posterior',posterior=belief)
            if self.feedback_options.get('count_joint'):
                e=belief['expected_remaining']
                factor=1.5 if e<1 else .7 if e>3 else 1.
                self.config.joint_detour_m=old*factor
                self._record('count_conditioned_service',expected_undiscovered=e,detour_factor=factor)
        try:return super()._joint_service(next_point,force)
        finally:self.config.joint_detour_m=old

    def _route_reorder_suffix(self,start):
        if self.feedback_options.get('adaptive_phase') and self.config.problem==3 and start==1 and not self.phase_fixed:
            self.phase_fixed=True
            if any(i>=1 for k in self.channels.values() for i in k.coverage_indices):raise ValueError('phase requires unvisited outer ring')
            cloud=[]
            for k in self._detected():
                cloud.extend(compact(hypotheses(k,self.prior),8))
            if cloud:
                old=list(self.points[1:]);current=self.client.position
                def proxy(route):
                    edges=list(zip([current]+route,route))
                    return sum(s.weight*min(math.dist(a,s.position)+math.dist(s.position,b)-math.dist(a,b) for a,b in edges) for s in cloud)/5
                best=list(old);score=proxy(best);old_score=score;angle=0
                radius=float(self.spec['ring'])
                for deg in range(-30,31,5):
                    raw=[(radius*math.cos(math.radians(deg+60*i)),radius*math.sin(math.radians(deg+60*i))) for i in range(6)]
                    for first in range(6):
                        for step in (-1,1):
                            route=[raw[(first+step*i)%6] for i in range(6)]
                            value=proxy(route)
                            if value<score-1e-5:best,score,angle=route,value,deg
                self.points[1:]=best
                self.ring_proof={**self.ring_proof,'adaptive_rotation_degrees':angle,'rotation_basis':'orthogonal rotation preserves concentric circular domain and reception distances; origin fixed', 'actual_ring_points':best}
                self._record('feedback_ring_phase',phase_degrees=angle,old_service_proxy_s=old_score,new_service_proxy_s=score,points=best)
        point=super()._route_reorder_suffix(start)
        if not self.count_model or start is None or len(self.points)-start<2:return point
        if self.feedback_options.get('route_when_no_detected') and self._detected():return point
        state=self.count_model.posterior(self.channels)
        if not state['unknown']:return point
        old=list(self.points[start:]);current=self.client.position
        old_cost=self.count_model.route_cost(old,current,state)
        max_length=route_length(old,current)*self.feedback_options.get('route_length_ratio',1.08)
        def first_gain(q):
            vis=self.count_model.masks(q)
            gain=sum(state['presence'][f]*self.count_model.mass((self.count_model.alive[f][0]&vis[0],self.count_model.alive[f][1]&vis[1]))/state['likelihood'][f] for f in state['unknown'])
            return gain/(math.dist(current,q)/5+6*len(state['unknown']))
        firsts=sorted(old,key=lambda q:math.dist(current,q))[:2]+sorted(old,key=first_gain,reverse=True)[:3]
        best,best_cost=old,old_cost
        for q in dict.fromkeys(firsts):
            candidate=[q]+order_route([p for p in old if p!=q],q)
            if route_length(candidate,current)>max_length:continue
            cost=self.count_model.route_cost(candidate,current,state)
            if cost<best_cost-1e-6:best,best_cost=candidate,cost
        if best!=old:
            self.points[start:]=best;self.stats['count_route_changes']+=1
            self._record('count_route_selection',N_posterior=state['N'],expected_undiscovered=state['expected_remaining'],
                         old_proxy_s=old_cost,new_proxy_s=best_cost,remaining_points=best,
                         role='expected scan/upper-bound-16 completion proxy; all required stations retained')
        self.feedback_options['next_goal']=self.points[start]
        return self.points[start]

    def _fallback(self,k):
        if not self.feedback_options.get('resweep'):return super()._fallback(k)
        self.stats['fallback_targets']+=1
        states=compact(hypotheses(k,self.prior),40)
        points=planner.clear_plan(k,self.client.position,states,False)
        self._record('adaptive_cover',target_channel=k.channel,point_count=len(points))
        checks=0;failures=0
        while points:
            point=points.pop(0)
            if not k.clearance_possible(point):
                self.stats['feedback_pruned_clear_points']+=1;continue
            if self._clear(k.channel,point,{'type':'feedback_region_optical_attempt'}):return
            failures+=1
            if self._try_certified_clear(k):return
            if failures%6 or checks>=2 or len(points)<6:continue
            checks+=1;self.stats['resweep_checks']+=1
            states=compact(hypotheses(k,self.prior),40)
            remaining=[p for p in points if k.clearance_possible(p)]
            cost=planner.expected_cover(remaining,self.client.position,states)
            if cost<100:continue
            options={**self.feedback_options,'horizon':1,'angle_bin_deg':4.,'diverse_candidates':True,
                     'allow_probe':False,'terminal_weight':0.}
            action,info=planner.choose(k,self.client.position,self.client.current_channel,options,self.prior,1)
            use=bool(action and action['kind']=='measure' and action['expected_s']<.85*cost)
            self._record('resweep_reconsider',target_channel=k.channel,remaining_cover_s=cost,planning=info,use_measurement=use)
            if not use:continue
            self.stats['resweep_measures']+=1
            self._measure(k.channel,action['point'],'resweep_measurement')
            if k.status=='cleared' or self._try_certified_clear(k):return
            points=planner.clear_plan(k,self.client.position,compact(hypotheses(k,self.prior),40),False)
            self._record('adaptive_cover_restart',target_channel=k.channel,point_count=len(points))
        raise InconsistentKnowledge('adaptive cover exhausted')


def make_solver(client,problem,base_spec=None,arm='current',*,prior=None,options=None,decision_log=None):
    selected=selection()['policies'][str(problem)]
    opts={**ARMS[selected['arm']],**selected['options'],**CANDIDATES[arm],**(options or {})}
    spec=copy.deepcopy(base_specs()[problem])
    config=SolverConfig(problem=problem,local_measure_limit=spec.get('limit',1))
    for k,v in spec.get('config',{}).items():setattr(config,k,v)
    solver=NextSolver(client,config,decision_log,spec=spec,feedback_options=opts,prior=None)
    if spec.get('refined_global'):install_refined_cover(solver)
    return solver
