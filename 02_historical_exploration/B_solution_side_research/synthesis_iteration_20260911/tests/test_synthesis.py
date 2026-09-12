import copy
import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'vendor'));sys.path.insert(0,str(ROOT))
from bsolver.protocol import RobotClient
from bsolver.simulator import LocalSimulator,Source
from bsolver.refined_coverage import refined_directional_route,refined_coverage_certificate
from methods.solver import make_solver


def make(spec,problem=3,n=16):
    sources=[Source(f,(100.+f,0.),1000.,None) for f in range(1,n+1)]
    env=LocalSimulator(sources,enforce_case_size=True)
    client=RobotClient('local-team',transport=env)
    return make_solver(client,problem,spec),env


def specification(**kw):
    return dict(implementation='synthesis',limit=1,cover='bbox',ring=1150,
                schedule_options=dict(reorder=True,service_score='hull',shared=False),**kw)


def test_prior_winner_controls_are_the_actual_new_baselines():
    p3,_=make({'implementation':'champion','limit':1})
    p4,_=make({'implementation':'champion','limit':1},4)
    assert p3.spec['ring']==1150 and p3.cover_mode=='bbox'
    assert p4.points==refined_directional_route() and len(p4.points)==25
    assert p4.local_refinement_mode=='cover' and p4.config.local_measure_limit==1
    assert p4.refined_config.cell_side_m==28


def test_known_sixteen_stops_new_channel_scans_but_clears_every_source():
    s,env=make(specification(completion_aware=True))
    result=s.run();s.client.close_log()
    assert result['status']=='complete',result['error']
    assert env.summary()['all_cleared'] and result['clear_successes']==16
    assert result['confirmed16_transitions']==1
    assert result['stop_evidence']['type']=='known_upper_bound'
    # No channel without a source is queried after the 16 positive reports.
    assert all(e['knowledge']['channel']<=16 for e in s.decisions if e['event']=='measure')


def test_fifteen_successes_cannot_replace_coverage_proof():
    s,env=make(specification(completion_aware=True),n=15)
    result=s.run();s.client.close_log()
    assert result['status']=='complete',result['error']
    assert env.summary()['all_cleared'] and result['confirmed16_transitions']==0
    evidence=result['stop_evidence']
    assert evidence['type']=='per_channel_coverage'
    assert all(len(indices)==7 for indices in evidence['checked_indices'].values())


def test_refined_continuous_cover_has_positive_visibility_margin():
    proof=refined_coverage_certificate()
    assert proof['point_count']==25 and proof['exact_intersection_predicates']
    assert proof['reception_distance_upper_bound_m']<1000
    assert proof['strict_projection_lower_bound_m']>0


def test_coverage_installation_forbidden_after_enter():
    from methods.solver import install_refined_cover
    s,_=make({'implementation':'previous_selected'},4)
    s.client.enter()
    with pytest.raises(ValueError):install_refined_cover(s)
    s.client.exit();s.client.close_log()


def test_shared_measurements_consume_existing_local_budget(monkeypatch):
    from methods.previous import IterationSolver
    s,_=make(specification(shared_current=True))
    s.shared_used[1]=1
    observed=[]
    monkeypatch.setattr(IterationSolver,'_localize',lambda obj,f:observed.append(obj.config.local_measure_limit))
    s._localize(1)
    assert observed==[0] and s.config.local_measure_limit==1


def test_cost_gate_compares_direct_upper_to_measurement_lower(monkeypatch):
    import methods.synthesis as module
    from methods.previous import IterationSolver
    s,_=make(specification(cost_gate=True))
    monkeypatch.setattr(IterationSolver,'_select',lambda obj,k:({'kind':'measure','point':(500.,0.)},{}))
    monkeypatch.setattr(module,'build_cover',lambda *a:([(20.,0.)],{'worst_case_cost_s':30.}))
    action,info=s._select(s.channels[1])
    assert action is None and info['direct_cover_upper_s']<info['measure_then_clear_lower_s']
    assert s.stats['cost_gate_skips']==1
    monkeypatch.setattr(module,'build_cover',lambda *a:([(20.,0.)],{'worst_case_cost_s':200.}))
    action,info=s._select(s.channels[1])
    assert action['kind']=='measure'


def test_factory_rejects_hidden_truth():
    with pytest.raises(ValueError):make(dict(specification(),sources=[]))


@pytest.mark.parametrize('known,factor',[(7,.5),(12,1.),(15,2.)])
def test_count_conditioned_detour_is_only_a_temporary_ranking_change(monkeypatch,known,factor):
    from methods.scheduling import RouteMixin
    s,_=make(specification(detour_by_known=True))
    for f in range(1,known+1):s.channels[f].status='cleared'
    values=[]
    monkeypatch.setattr(RouteMixin,'_joint_service',lambda obj,*a:values.append(obj.config.joint_detour_m))
    original=s.config.joint_detour_m
    s._joint_service(s.points[1])
    assert values==[original*factor] and s.config.joint_detour_m==original
    assert s._complete_evidence() is None


@pytest.mark.parametrize('name',['p3_macro2','p4_finish16','p4_macro2','p4_shared'])
def test_new_mechanisms_complete_small_legal_world(name):
    specs=json.loads((ROOT/'configs/round1.json').read_text())
    spec=specs[name];problem=spec['problems'][0]
    solver,env=make(spec,problem)
    result=solver.run();solver.client.close_log()
    assert result['status']=='complete',result['error']
    assert env.summary()['all_cleared'] and abs(result['timing_residual_s'])<.001
    assert result['stop_evidence']['type']=='known_upper_bound'
