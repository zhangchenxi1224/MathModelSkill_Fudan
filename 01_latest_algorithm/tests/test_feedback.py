import math
import random
import pytest
from feedback.region import FeedbackKnowledge
from feedback.belief import State,hypotheses,orientation_arcs
from feedback.planner import posterior,expected_cover,proposals
from bsolver.geometry import bearing_deg,distance

def observe(k,p,kind,angle=0):
    response={'accepted':True,'measure_result':kind}
    if kind=='direction':response['svd_deg']=angle
    k.observe(p,response)

def test_retroactive_shared_radius_bisector():
    k=FeedbackKnowledge(1,3,depth=4)
    observe(k,(2200.,0.),'no_signal')
    assert k.region_contains((1150.,0.))
    observe(k,(0.,0.),'direction')
    assert not k.region_contains((1150.,0.))
    assert k.region_contains((1050.,0.))
    assert k.region_stats['negative_pairs']==1

def test_p4_no_signal_preserves_backside_source():
    k=FeedbackKnowledge(1,4,depth=5)
    observe(k,(0.,0.),'direction')
    observe(k,(700.,0.),'no_signal')
    assert k.region_contains((500.,0.))
    assert k.compatible_hidden_state((500.,0.),1000.,180.)
    assert not k.compatible_hidden_state((500.,0.),1000.,None)
    assert k.region_stats['directed_cells']>0

def test_negative_and_clear_failure_holes_are_retained():
    k=FeedbackKnowledge(1,3,depth=6)
    observe(k,(0.,0.),'direction')
    k.record_clear((750.,0.),{'clear_result':'no_target_in_range'})
    assert k.region_contains((1000.,0.))
    assert all(distance(s.position,(750.,0.))>20. for s in hypotheses(k))

def test_fixed_orientation_cannot_change_between_feedbacks():
    k=FeedbackKnowledge(1,4)
    observe(k,(0.,0.),'direction',0.)
    observe(k,(1000.,0.),'direction',180.)
    k.observations.append(type(k.observations[0])((500.,0.),'no_signal'))
    assert orientation_arcs(k,(500.,200.),1000.)==[]

def test_no_signal_planning_branch_updates_geometry():
    k=FeedbackKnowledge(1,3,depth=4)
    observe(k,(0.,0.),'direction')
    child=posterior(k,(2200.,0.),'no_signal',None,8.)
    assert max(p[0] for p in child.hull)<1100.01
    assert max(p[0] for p in k.hull)>1400.

def test_optical_expected_cost_includes_failed_attempt_and_movement():
    states=[State((0.,0.),1000.,None,.75),State((100.,0.),1000.,None,.25)]
    assert expected_cover([(0.,0.),(100.,0.)],(0.,0.),states)==pytest.approx(10.75)

def test_proposals_include_open_seed_and_no_repeated_point():
    k=FeedbackKnowledge(1,3)
    observe(k,(0.,0.),'direction')
    points=proposals(k,(0.,0.),12)
    assert (0.,0.) not in points
    assert any(abs(p[1])>=500 for p in points)

@pytest.mark.parametrize('problem',[3,4])
def test_random_legal_histories_keep_true_position(problem):
    rng=random.Random(20260912077+problem)
    for _ in range(12):
        g=(rng.uniform(-900,900),rng.uniform(-900,900))
        r=rng.uniform(1000,1500);theta=rng.uniform(0,math.tau)
        k=FeedbackKnowledge(1,problem,depth=4)
        p=(g[0]+300*math.cos(theta),g[1]+300*math.sin(theta))
        qs=[p]+[(rng.uniform(-2000,2000),rng.uniform(-2000,2000)) for _ in range(7)]
        for q in qs:
            d=distance(q,g)
            visible=d<=r and (problem==3 or (q[0]-g[0])*math.cos(theta)+(q[1]-g[1])*math.sin(theta)>=0)
            kind='no_signal' if not visible else 'near' if d<=5 else 'direction'
            observe(k,q,kind,bearing_deg(q,g)+.5 if kind=='direction' else 0.)
            assert k.region_contains(g)


def test_long_thin_region_after_many_failed_clears():
    from bsolver.geometry import contains
    k=FeedbackKnowledge(18,3,depth=5)
    observe(k,(0.,0.),'direction',168.16)
    g=(-1369.4428666072079,270.6668430715187)
    for j in [0,1,4,6,7,9,11,13,14,15]:
        p=(-632.0268623438494-j*29.2605783893116,130.62423276950992+j*6.67206578087978)
        k.record_clear(p,{'clear_result':'no_target_in_range'})
        assert k.region_contains(g)
        assert contains(k.hull,g,tol=1e-4)
        assert all(contains(k.hull,v,tol=1e-4) for cell in k.cells for v in cell['poly'])

def test_core960_reported_cell_membership_failures():
    import json
    from pathlib import Path
    from bsolver.geometry import contains
    fixtures=json.loads((Path(__file__).parent/'data/core960_thin_cell_histories.json').read_text(encoding='utf-8'))
    for fixture in fixtures:
        k=FeedbackKnowledge(fixture['channel'],fixture['problem'],depth=5)
        for event in fixture['history']:
            if event['kind']=='measure':k.observe(event['position'],event['response'])
            else:k.record_clear(event['position'],event['response'])
            assert contains(k.hull,fixture['true_position'],tol=1e-4)
            assert k.region_contains(fixture['true_position'])
