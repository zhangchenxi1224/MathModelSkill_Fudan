"""Frozen previous winners and isolated synthesis candidates. Public inputs only."""
from dataclasses import replace
from bsolver.strategy import SolverConfig
from bsolver.refined_local import RefinedLocalSolver
from bsolver.refined_coverage import refined_directional_route,refined_coverage_certificate
from .previous import make_solver as previous_factory, IterationSolver
from .scheduling import RouteMixin


P3_PREVIOUS=dict(limit=1,cover='bbox',reorder=True,ring=1150,
                 schedule_options=dict(reorder=True,service_score='hull',shared=False))
P4_PREVIOUS=dict(limit=2,cover='bbox',reorder=True,config={'joint_detour_m':1200},
                 schedule_options=dict(reorder=True,service_score='hull',shared=False))


class RefinedEvidenceMixin:
    def _complete_evidence(self):
        evidence=super()._complete_evidence()
        if evidence and evidence['type']=='per_channel_coverage' and getattr(self,'refined_proof',None):
            evidence.update(construction='refined_directional_triangular',
                            refined_coverage_certificate=self.refined_proof,
                            route_permuted_only=True)
        return evidence

    def _record(self,event,**data):
        if event in ('start','finish') and getattr(self,'refined_proof',None):
            data['refined_coverage_certificate']=self.refined_proof
        super()._record(event,**data)


class PriorP4(RefinedEvidenceMixin,RefinedLocalSolver):
    pass


def install_refined_cover(solver):
    if solver.config.problem!=4 or solver.client.entered or any(k.observations for k in solver.channels.values()):
        raise ValueError('refined cover must be installed before the first observation in Q4')
    solver.points=refined_directional_route()
    solver.refined_proof=refined_coverage_certificate()
    return solver


def make_solver(client,problem,spec,decision_log=None):
    implementation=spec.get('implementation','synthesis')
    if problem not in spec.get('problems',[3,4]): raise ValueError('wrong public problem for policy')
    if implementation=='champion':
        # Strongest previously confirmed family for each public problem.
        if problem==3: return previous_factory(client,3,P3_PREVIOUS,decision_log)
        return install_refined_cover(PriorP4(client,SolverConfig(problem=4,local_measure_limit=1),
                                             decision_log=decision_log,mode='cover'))
    if implementation=='previous_selected':
        return previous_factory(client,problem,P3_PREVIOUS if problem==3 else P4_PREVIOUS,decision_log)
    if implementation=='official_l1':
        return previous_factory(client,problem,dict(implementation='champion',limit=1),decision_log)
    if implementation=='dynamic':
        from dynamic_joint import make_solver as dynamic_factory
        return dynamic_factory(client,problem,dict(implementation='dynamic_joint',limit=2,
                               planner=spec.get('planner',{'horizon':2})),decision_log)
    if implementation=='prior_ablation':
        return previous_factory(client,problem,{k:v for k,v in spec.items() if k!='implementation'},decision_log)
    if implementation!='synthesis': raise ValueError('unknown implementation')
    from .synthesis import SynthesisMixin
    class SynthesisSolver(RefinedEvidenceMixin,SynthesisMixin,RouteMixin,IterationSolver):
        pass
    allowed={'implementation','problems','limit','cover','reorder','schedule_options','ring','config',
             'mode','nosignal','refined_global','completion_aware','shared_current','shared_radius_ratio',
             'cost_gate','route_depth','planner','cover_order'}
    if set(spec)-allowed: raise ValueError('unknown or hidden policy input')
    config=SolverConfig(problem=problem,local_measure_limit=spec.get('limit',1))
    for key,value in spec.get('config',{}).items():
        if key not in config.__dataclass_fields__ or key=='problem': raise ValueError('invalid config')
        setattr(config,key,value)
    policy=SynthesisSolver(client,config,decision_log,spec=spec)
    if spec.get('refined_global'): install_refined_cover(policy)
    return policy
