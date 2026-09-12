import copy
from dataclasses import replace
import itertools
import math
from pathlib import Path
import time

import pytest

from dynamic_joint import make_solver
from dynamic_joint.counts import CountPrior, posterior
from dynamic_joint.model import Node, Target, Action, transitions, choose, candidate_actions, BeliefCache
from dynamic_joint.solver import validated_options
from dynamic_joint.working_belief import Particle, predict_response, build_belief, PlanningBudgetExceeded
from dynamic_joint._core.protocol import RobotClient
from dynamic_joint._core.simulator import LocalSimulator, Source
from dynamic_joint._core.knowledge import ChannelKnowledge


def square(x,y,r): return ((x-r,y-r),(x+r,y-r),(x+r,y+r),(x-r,y+r))


def node_with(targets, position=(0.,0.), current=1):
    all_targets={f:Target(f,'cleared' if f<=10 else 'unknown',square(0,0,1800)) for f in range(1,21)}
    all_targets.update({t.channel:t for t in targets})
    return Node(position,current,all_targets)


def client(): return RobotClient('local-team',transport=LocalSimulator([]))


@pytest.mark.parametrize('known,minimum,maximum',[(0,10,16),(7,3,9),(15,0,1),(16,0,0)])
def test_count_constraints_and_marginal_sum(known,minimum,maximum):
    p=posterior(CountPrior.from_config(3),known,{f:.1+f/100 for f in range(known+1,21)})
    assert (p['unseen_min'],p['unseen_max'])==(minimum,maximum)
    assert sum(p['channel_presence'].values())==pytest.approx(p['expected_unseen'])


def test_initial_count_posterior_equals_prior():
    prior=CountPrior.from_config(3)
    p=posterior(prior,0,{f:1. for f in range(1,21)})
    assert p['count_distribution']==pytest.approx(prior.probabilities)


def test_count_dp_matches_full_enumeration():
    prior=CountPrior.from_config(4); ls={11:.1,12:.35,13:.7,14:.9}
    worlds=[]
    for bits in itertools.product([0,1],repeat=4):
        n=10+sum(bits)
        weight=prior.probabilities[n]/math.comb(20,n)*math.prod(v for b,v in zip(bits,ls.values()) if b)
        worlds.append((bits,n,weight))
    total=sum(w for _,_,w in worlds)
    p=posterior(prior,10,ls)
    for i,f in enumerate(ls):
        assert p['channel_presence'][f]==pytest.approx(sum(w for bits,_,w in worlds if bits[i])/total)
    for n in range(10,15):
        assert p['count_distribution'][n]==pytest.approx(sum(w for _,size,w in worlds if size==n)/total)


@pytest.mark.parametrize('known,ls',[(17,{}),(10,{f:0. for f in range(11,22)}),(9,{})])
def test_impossible_count_evidence_is_not_silently_reset(known,ls):
    with pytest.raises(ValueError): posterior(CountPrior.from_config(3),known,ls)


def test_count_prior_changes_ranking_information_not_completion():
    low=CountPrior.from_config(3,dict(count_probabilities={'10':1.},broad_weight=.1))
    high=CountPrior.from_config(3,dict(count_probabilities={'16':1.},broad_weight=.1))
    ls={f:1. for f in range(1,21)}
    assert posterior(high,0,ls)['expected_unseen']>posterior(low,0,ls)['expected_unseen']
    solver=make_solver(client(),3,{'prior':dict(count_probabilities={'10':1.},broad_weight=.01)})
    for f in range(1,11): solver.channels[f].status='cleared'
    assert solver._complete_evidence() is None
    for f in range(11,17): solver.channels[f].status='cleared'
    assert solver._complete_evidence()['type']=='known_upper_bound'


def test_sixteen_detected_skips_search_but_does_not_finish():
    solver=make_solver(client(),3)
    for f in range(1,16): solver.channels[f].status='cleared'
    solver.channels[16].observe((0,0),dict(measure_result='direction',svd_deg=0))
    solver._refresh_proofs(); node,_=solver._snapshot(with_beliefs=False)
    actions=candidate_actions(node,solver.points,solver.prior,solver.options)
    assert actions and all(a.channel==16 for a in actions)
    assert solver._complete_evidence() is None


def test_absence_requires_every_coverage_index():
    solver=make_solver(client(),4)
    k=solver.channels[1]
    k.coverage_indices=set(range(len(solver.points)-1))
    solver._refresh_proofs(); assert k.status=='unknown'
    k.coverage_indices.add(len(solver.points)-1)
    solver._refresh_proofs(); assert k.status=='absent'


def test_clear_does_not_switch_channel_and_costs_are_exact():
    target=Target(11,'detected',square(100,0,1),(Particle((100.,0.),1000,None,1.),))
    node=node_with([target],current=4)
    action=Action('clear',11,(100.,0.),'certified_clear',certified=True)
    branch=transitions(node,action,[(0.,0.)],CountPrior.from_config(3),validated_options())[0]
    probability,child,cost,_=branch
    assert probability==1 and child.current_channel==4 and cost==25
    assert child.targets[11].status=='cleared'


def test_near_measurement_includes_automatic_clear_cost():
    target=Target(11,'detected',square(100,0,1),(Particle((100.,0.),1000,None,1.),))
    node=node_with([target],current=4)
    action=Action('measure',11,(100.,0.),'local_measure')
    _,child,cost,label=transitions(node,action,[(0.,0.)],CountPrior.from_config(3),validated_options())[0]
    assert label['kind']=='near' and child.targets[11].status=='cleared'
    assert child.current_channel==11 and cost==31


def test_negative_measurement_keeps_directional_occlusion_possible():
    k=ChannelKnowledge(1,4)
    k.observe((0,0),dict(measure_result='direction',svd_deg=0))
    k.observe((600,0),dict(measure_result='no_signal'))
    assert k.compatible_hidden_state((500,0),1000,180)
    assert not k.compatible_hidden_state((500,0),1000,None)
    cfg=dict(seed=8,particle_count=32,max_position_proposals=128,min_particles=1,directional_prior=.5)
    bank,info=build_belief(k,cfg)
    assert bank
    assert all(k.compatible_hidden_state(p.position,p.radius,p.direction_deg) for p in bank)
    assert all(predict_response(p,(0,0))==('direction',0.) for p in bank)
    assert all(predict_response(p,(600,0))[0]=='no_signal' for p in bank)


def test_fixed_particle_response_is_shared_across_future_branches():
    p=Particle((500.,20.),1200,None,1.,error_seed=99)
    q=(100.,90.)
    first=predict_response(p,q)
    predict_response(p,(20.,300.))
    assert predict_response(p,q)==first


def test_planning_is_pure_and_evaluates_cross_channel_second_actions():
    a=Target(11,'detected',square(100,0,60),
             (Particle((40.,0.),1000,None,.5),Particle((150.,30.),1000,None,.5)),
             observed=((0.,0.),),fallback_points=((40.,0.),(150.,30.)))
    b=Target(12,'detected',square(250,20,3),(Particle((250.,20.),1000,None,1.),))
    n=node_with([a,b]); saved=copy.deepcopy(n)
    options=validated_options({'horizon':2,'node_limit':1000})
    action,info=choose(n,[(0.,0.)],CountPrior.from_config(3),options,None)
    assert n==saved
    assert len(info['evaluations'])>1
    assert any(branch['second_action'] and branch['second_action']['channel']!=evaluation['action']['channel']
               for evaluation in info['evaluations'] for branch in evaluation['branches'])
    assert any(len({str(branch['second_action']) for branch in evaluation['branches']})>1
               for evaluation in info['evaluations'])
    for evaluation in info['evaluations']:
        assert sum(b['probability'] for b in evaluation['branches'])==pytest.approx(1.)


def test_partial_plan_is_discarded_on_node_limit():
    n=node_with([Target(11,'detected',square(100,0,40),fallback_points=((100.,0.),))])
    with pytest.raises(PlanningBudgetExceeded):
        choose(n,[(0.,0.)],CountPrior.from_config(3),validated_options({'node_limit':1}),None)


def test_root_certificate_never_uses_particle_certainty():
    solver=make_solver(client(),3)
    solver.channels[1].observe((0,0),dict(measure_result='direction',svd_deg=0))
    with pytest.raises(ValueError,match='real geometric certificate'):
        solver._execute(Action('clear',1,(100.,0.),'certified_clear',certified=True))


@pytest.mark.parametrize('spec',[{'sources':[]},{'planner':{'horizon':4}},
                                {'limit':1,'planner':{'local_limit':2}},
                                {'implementation':'champion'},{'planner':{'angle_bin_deg':7}}])
def test_factory_rejects_truth_or_invalid_settings(spec):
    with pytest.raises(ValueError): make_solver(client(),3,spec)


def test_local_noise_seed_is_independent_of_geometry_seed(tmp_path):
    from evaluate import load_case,write
    from dynamic_joint._core.experiments import generate_case
    case=generate_case(2,3,10)
    case['error_field']=dict(seed=902,mode='correlated',correlation_length_m=67.)
    path=tmp_path/'case.json';write(path,case)
    _,_,field,error=load_case(path)
    from dynamic_joint._core.simulator import FixedErrorField
    expected=FixedErrorField(902,'correlated',67.)
    assert field['seed']==902
    assert error(1,(20,30))==expected(1,(20,30))


def test_output_collision_is_refused(tmp_path):
    from evaluate import solve_case
    with pytest.raises(FileExistsError): solve_case('does-not-exist',tmp_path)


def test_forced_planning_timeout_still_completes_real_feedback_loop(monkeypatch):
    import dynamic_joint.solver as module
    def timeout(*args,**kwargs): raise PlanningBudgetExceeded('test forced timeout')
    monkeypatch.setattr(module,'choose',timeout)
    sources=[Source(f,(100.+f*2.,20.),1000,None) for f in range(1,17)]
    env=LocalSimulator(sources,enforce_case_size=True,error_mode='extreme')
    c=RobotClient('local-team',transport=env)
    solver=make_solver(c,3,{'limit':0})
    r=solver.run(); c.close_log()
    assert r['status']=='complete',r['error']
    assert env.summary()['all_cleared'] and r['budget_fallbacks']>0
    assert r['completed_plans']==0 and abs(r['timing_residual_s'])<.001
    assert r['stop_evidence']['type']=='known_upper_bound'
