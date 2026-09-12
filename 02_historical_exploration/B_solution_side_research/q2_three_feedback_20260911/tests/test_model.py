import math
from model import FirstContext,common_quantized_report
from geometry import Region,Circle,Halfplane,enclosing_circle,verify_circle_cover,dist
from evaluator import evaluate


def test_shared_radius_no_signal_projection():
    context=FirstContext()
    q=(850.,600.)
    no=context.posterior(q,'no_signal')
    assert no.actual_witness((10.,0.))
    assert not no.contains((100.,0.))
    # d2>1000 alone is insufficient for a far source whose first observation
    # already implies R>1000.
    q2=(300.,600.)
    g=(1400.,0.)
    assert dist(g,q2)>1000
    assert not context.posterior(q2,'no_signal').contains(g)


def test_quantized_common_report_rejects_continuous_only_intersection():
    assert common_quantized_report([.009,2.001]) is None
    report=common_quantized_report([359.5,.5])
    assert report is not None
    assert all(abs((report-a+180)%360-180)<=1 for a in [359.5,.5])


def test_arc_circle_not_polygon_and_hole():
    region=Region([Circle((0.,0.),2.),Circle((0.,0.),1.,False,True)],(-2.,2.,-2.,2.))
    result=enclosing_circle(region)
    assert 1.999<result['lower_m']<=2.000001
    assert 2<=result['upper_m']<2.001
    check=verify_circle_cover(region,result['center'],2.01)
    assert check['requested_circle_verified']


def test_no_signal_small_cap_and_repeat():
    context=FirstContext()
    result=enclosing_circle(context.posterior((850.,600.),'no_signal'))
    assert 22<result['lower_m']<=result['upper_m']<23
    repeated=evaluate(context,(0.,0.))
    assert repeated['repeat_first']
    assert repeated['upper_m']>700


def test_global_local_transform_preserves_target_circle():
    context=FirstContext((2200.,0.),180.)
    for p in [(700.,0.),(1500.,5.)]:
        assert dist(context.to_global(context.to_local(p)),p)<1e-9
    prior=context.prior()
    assert prior.actual_witness((600.,0.))
    assert not prior.contains((100.,0.))
