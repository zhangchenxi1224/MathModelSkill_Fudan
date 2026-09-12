import math

from study import (EPSILON, first_knowledge, response, old_candidate_certificate,
                   indistinguishable_pair)
from bsolver.geometry import bearing_deg, distance, minimum_enclosing_circle, contains, wrap_angle_deg
from bsolver.sensing import direction_outcome_bound
from q2_geometry import generate_candidates


def test_analytic_indistinguishability_counterexample_exceeds_twenty():
    sine=math.sin(math.radians(1.))
    center=(1500/(1+sine),0.)
    # Deflate the test fixture below the exact tangent circle by 3e-11m;
    # the theorem itself uses the exact expression, not rounded coordinates.
    rho=1500*sine/(1+sine)*(1-1e-12)
    assert rho>25.7
    for q in [(0.,0.),(700.,400.),(800.,500.),(200.,300.)]:
        d=distance(center,q)
        unit=((center[0]-q[0])/d,(center[1]-q[1])/d)
        targets=[(center[0]+sign*rho*unit[0],center[1]+sign*rho*unit[1]) for sign in (-1,1)]
        angles=[bearing_deg(q,g) for g in targets]
        report=round(angles[0],2)%360
        assert distance(*targets)>51.4
        for g,angle in zip(targets,angles):
            assert math.hypot(*g)<=1500+1e-9
            assert abs(wrap_angle_deg(bearing_deg((0.,0.),g)))<=1+1e-9
            assert distance(q,g)<=1500 and distance(q,g)>5
            assert abs(wrap_angle_deg(report-angle))<.006


def test_conservative_response_bound_survives_error_extremes_and_wrapping():
    for first_angle in (0.,359.99):
        k=first_knowledge((0.,0.),first_angle)
        q=(800.,500.)
        assert old_candidate_certificate(k,q)['certified']
        bound=direction_outcome_bound(k.hull,q,EPSILON,.5)
        for radius in (5.01,300.,1000.,1500.):
            for error in (-1.,0.,1.):
                angle=math.radians(first_angle+error)
                source=(radius*math.cos(angle),radius*math.sin(angle))
                for error2 in (-1.,-.37,0.,.81,1.):
                    fresh=first_knowledge((0.,0.),first_angle)
                    second=response(source,max(1000.,radius),q,error2)
                    assert second['measure_result']!='no_signal'
                    if second['measure_result']=='direction':
                        assert abs(wrap_angle_deg(second['svd_deg']-bearing_deg(q,source)))<=1.
                    fresh.observe(q,second)
                    assert contains(fresh.hull,source,tol=1e-5)
                    assert minimum_enclosing_circle(fresh.hull)[1]<=bound+1e-5


def test_numeric_lower_witness_is_two_legal_hidden_worlds():
    first=(0.,0.)
    k=first_knowledge(first,0.)
    q=(870.,500.)
    witness=indistinguishable_pair(k,q)
    assert witness and witness['mec_radius_lower_m']>20
    for source,radius in zip(witness['sources'],witness['radii']):
        assert 1000<=radius<=1500
        assert 5<distance(first,source)<=radius
        assert 5<distance(q,source)<=radius
        assert abs(wrap_angle_deg(bearing_deg(first,source)-witness['same_first_report']))<=1.
        assert abs(wrap_angle_deg(bearing_deg(q,source)-witness['same_second_report']))<=1.
