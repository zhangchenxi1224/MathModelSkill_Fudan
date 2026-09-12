"""Ablations over the last deployed P3/P4 algorithms."""
import copy
from bsolver.strategy import SolverConfig
from bsolver.knowledge import InconsistentKnowledge
from bsolver.geometry import distance
from methods.solver import make_solver as previous_factory,RefinedEvidenceMixin,install_refined_cover
from methods.synthesis import SynthesisMixin
from methods.scheduling import RouteMixin
from methods.previous import IterationSolver
from .region import FeedbackKnowledge
from .belief import hypotheses,compact
from .planner import choose,clear_plan,expected_cover

ARMS={
    'baseline':{},
    'geometry':{},
    'three_feedback':{'planning':True},
    'three_feedback_fast':{'planning':True,'planning_region_depth':0},
    'dp_geometric':{'planning':True,'planning_region_depth':0,'horizon':2,'extra_measure_allowance':1},
    'probability':{'planning':True,'probability_order':True,'use_prior':True},
    'lookahead':{'planning':True,'probability_order':True,'use_prior':True,
                 'horizon':2,'allow_probe':True,'extra_measure_allowance':1,'macro_depth':2},
    'risk_cut':{'planning':True,'probability_order':True,'use_prior':True,
                'horizon':2,'allow_probe':True,'extra_measure_allowance':1,'macro_depth':2,
                'nominal_case_risk':.01},
}


class FeedbackMixin:
    def __init__(self,*args,feedback_options=None,prior=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.feedback_options=feedback_options or {}
        self.prior=prior if self.feedback_options.get('use_prior') else None
        self.channels={f:FeedbackKnowledge(f,self.config.problem,self.config.epsilon_deg,
                       depth=self.feedback_options.get('region_depth',5),
                       sectors=self.feedback_options.get('orientation_sectors',16)) for f in self.channels}
        self.risk_deferred=set();self.probed=set()
        self.stats.update(feedback_pruned_clear_points=0,feedback_absent_channels=0,
                          feedback_plan_calls=0,feedback_no_signal=0,probability_probes=0,
                          nominal_risk_spent=0.,risk_deferred_sources=0)

    def _measure(self,*args,**kwargs):
        response=super()._measure(*args,**kwargs)
        self.stats['feedback_no_signal']+=int(response['measure_result']=='no_signal')
        self.stats['feedback_absent_channels']=sum(k.absence_proof is not None for k in self.channels.values())
        return response

    def _detected(self):
        return [k for k in super()._detected() if k.channel not in self.risk_deferred]

    def _complete_evidence(self):
        if self.risk_deferred:return None
        result=super()._complete_evidence()
        if result:
            result['feedback_absence']={str(f):k.absence_proof for f,k in self.channels.items() if k.absence_proof}
        return result

    def _localize(self,channel):
        if not self.feedback_options.get('planning'):
            return super()._localize(channel)
        k=self.channels[channel]
        if self._try_certified_clear(k):return
        remaining=self.config.local_measure_limit+self.feedback_options.get('extra_measure_allowance',0)
        while remaining>0:
            options={**self.feedback_options,'allow_probe':self.feedback_options.get('allow_probe',False) and channel not in self.probed}
            self.stats['feedback_plan_calls']+=1
            action,info=choose(k,self.client.position,self.client.current_channel,options,self.prior,remaining)
            self._record('all_feedback_plan',target_channel=channel,planning=info)
            if action is None:
                # A sampled model can be empty without proving geometric absence.
                action,_=super()._select(k)
            if action is None or action['kind']=='cover':break
            if action['kind']=='clear':
                self.probed.add(channel);self.stats['probability_probes']+=1
                if self._clear(channel,action['point'],{'type':'bounded_probability_probe'}):return
            else:
                remaining-=1
                self._measure(channel,action['point'],'all_feedback_local_measurement')
            if k.status=='cleared' or self._try_certified_clear(k):return
        self._fallback(k)

    def _fallback(self,k):
        self.stats['fallback_targets']+=1
        states=compact(hypotheses(k,self.prior),40)
        points=clear_plan(k,self.client.position,states,self.feedback_options.get('probability_order',False))
        if not points:raise InconsistentKnowledge('nonempty detected region has no optical cover')
        self._record('all_feedback_cover',target_channel=k.channel,point_count=len(points),
                     expected_s=expected_cover(points,self.client.position,states),
                     probability_status=(self.prior or {}).get('status','uncalibrated_working_model'))
        attempted=[]
        budget=self.feedback_options.get('nominal_case_risk',0.)/16
        for point in points:
            if not k.clearance_possible(point):
                self.stats['feedback_pruned_clear_points']+=1
                continue
            if self._clear(k.channel,point,{'type':'feedback_region_optical_attempt'}):return
            attempted.append(point)
            if budget>0 and states:
                mass=sum(s.weight for s in states if all(distance(s.position,p)>20. for p in attempted))
                if mass<=budget:
                    self.risk_deferred.add(k.channel)
                    self.stats['nominal_risk_spent']+=mass
                    self.stats['risk_deferred_sources']=len(self.risk_deferred)
                    self._record('nominal_risk_cut',target_channel=k.channel,remaining_working_mass=mass,
                                 status='NOT a calibrated risk guarantee; source remains uncleared')
                    return
        raise InconsistentKnowledge('feedback optical cover exhausted')

    def _route_destination(self,k,score):
        destination,cost,kind=super()._route_destination(k,score)
        if self.feedback_options.get('macro_depth',1)<2 or kind=='certified_clear_destination':
            return destination,cost,kind
        states=compact(hypotheses(k,self.prior),16)
        points=clear_plan(k,destination,states,True)
        return destination,expected_cover(points,destination,states),'expected_feedback_service_proxy'

    def run(self):
        result=super().run()
        if self.risk_deferred:
            result['status']='incomplete_risk_cut'
            result['error']='explicit experiment: unresolved sources were deferred by nominal mass'
        result['feedback_options']=self.feedback_options
        result['probability_status']=(self.prior or {}).get('status','uncalibrated_working_model')
        return result


class FeedbackSolver(RefinedEvidenceMixin,FeedbackMixin,SynthesisMixin,RouteMixin,IterationSolver):
    pass


def make_solver(client,problem,base_spec,arm='geometry',*,prior=None,options=None,decision_log=None):
    if arm not in ARMS:raise ValueError('unknown experiment arm')
    if arm=='baseline':return previous_factory(client,problem,base_spec,decision_log)
    opts={**ARMS[arm],**(options or {})}
    risk=opts.get('nominal_case_risk',0.)
    if not 0<=risk<1:raise ValueError('nominal risk budget must be in [0,1)')
    if arm!='risk_cut' and risk:raise ValueError('risk cuts are isolated to risk_cut arm')
    spec=copy.deepcopy(base_spec)
    if opts.get('macro_depth')==2:spec['route_depth']=2
    config=SolverConfig(problem=problem,local_measure_limit=spec.get('limit',1))
    for key,value in spec.get('config',{}).items():setattr(config,key,value)
    solver=FeedbackSolver(client,config,decision_log,spec=spec,feedback_options=opts,prior=prior)
    if spec.get('refined_global'):install_refined_cover(solver)
    return solver
