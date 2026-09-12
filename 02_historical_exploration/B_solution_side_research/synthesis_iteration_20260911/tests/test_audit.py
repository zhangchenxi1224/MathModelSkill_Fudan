from pathlib import Path
import gzip
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'vendor'))
from audit import error_at,audit_run
from bsolver.simulator import FixedErrorField,LocalSimulator,Source
from bsolver.protocol import RobotClient
from methods.solver import make_solver


@pytest.mark.parametrize('mode',['correlated','extreme','deterministic','positive','negative','zero'])
def test_independent_fixed_field_formula(mode):
    field=FixedErrorField(771,mode,87.)
    definition=dict(seed=771,mode=mode,correlation_length_m=87.)
    for f in (1,11,20):
        for p in ((0.,0.),(1800.,.001),(-123.75,-2300.)):
            assert error_at(definition,f,p)==pytest.approx(field(f,p),abs=1e-14)


def test_auditor_rejects_tampered_result(tmp_path):
    case=dict(problem=3,sources=[dict(channel=f,position=(0.,0.),radius=1000.,direction_deg=None) for f in range(1,17)],
              error_field=dict(seed=12,mode='correlated',correlation_length_m=150.))
    env=LocalSimulator([Source(**s) for s in case['sources']],enforce_case_size=True)
    c=RobotClient('local-team',transport=env,log_path=tmp_path/'requests.jsonl')
    solver=make_solver(c,3,{'implementation':'champion','limit':1},tmp_path/'decisions.jsonl')
    result=solver.run();c.close_log()
    for name in ['requests.jsonl','decisions.jsonl']:
        with gzip.open(tmp_path/(name+'.gz'),'wb') as fp:fp.write((tmp_path/name).read_bytes())
    assert audit_run(case,tmp_path,result)['issues']==[]
    result['total_virtual_time_s']+=1
    assert 'result time mismatch' in audit_run(case,tmp_path,result)['issues']
