from model import FirstContext
from evaluator import evaluate
from geometry import enclosing_circle,dist


def test_same_source_different_shared_r_changes_feedback_legally():
    c=FirstContext()
    g=(10.,0.); q=(850.,600.)
    for radius,kind in [(1000.,'no_signal'),(1500.,'direction')]:
        response=c.response(g,radius,q,error=1.)
        assert response['kind']==kind
        p=c.posterior(q,kind,response.get('report_deg'))
        assert p.contains(g,True)
        circle=enclosing_circle(p)
        assert dist(g,circle['center'])<=circle['upper_m']
    response=c.response((12.,0.),1000.,(10.,0.))
    assert response['kind']=='near'


def test_legal_report_partition_complete_and_witness_compatible():
    c=FirstContext(); q=(850.,600.)
    result=evaluate(c,q,tolerance=3.,max_splits=80)
    partition=result['report_partition']
    assert partition[0]['lo_code']==0 and partition[-1]['hi_code']==35999
    assert sum(r['hi_code']-r['lo_code']+1 for r in partition)==36000
    w=result['worst_witness']
    posterior=c.posterior(q,w['kind'],w.get('report_deg'))
    assert all(posterior.actual_witness(g) for g in w['witness'])
    assert 0<result['lower_m']<=result['upper_m']
