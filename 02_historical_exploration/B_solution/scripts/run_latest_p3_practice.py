"""Fifteen official P3 practice cases using the existing frozen P3 candidate."""
import argparse
import hashlib
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import collect_official_round as collector
import run_official_validation as prior


def run(out,robot_id):
    out=Path(out).resolve()
    candidate=ROOT/'results/round2/candidate.json'
    if prior.sha256(candidate)!='3e305f7f13a9e22d8b37a6e3d5ab3b1321357a3a8d4c418364ca8adae5424816':
        raise ValueError('Frozen P3 candidate differs')
    prior.verify_baseline(ROOT/'results/round1/baseline_freeze.json')
    spec=prior.normalize_spec(prior.read_object(candidate)['problems']['3'],3)
    cases=[dict(case_id=f'p3-latest-{i:03d}',problem=3,arm='candidate',protocol='candidate',
        stage='latest',split='latest',pilot=False,sequence=i-1,environment='official_practice',
        policy_spec=spec) for i in range(1,16)]
    plan=dict(problem=3,practice_only=True,formal_authorized=False,cases=cases,
        planned_cases=15,candidate_file_sha256=prior.sha256(candidate),
        runtime_sha256={n:prior.sha256(ROOT/n) for n in ('scripts/run_latest_p3_practice.py',
            'scripts/run_official_validation.py','scripts/collect_official_round.py','scripts/round_ui.ps1')})
    if (out/'plan.json').exists():
        if prior.read_object(out/'plan.json')!=plan: raise ValueError('Existing P3 plan differs')
    else:
        collector.write_json(out/'plan.json',plan)
        (out/'baseline_freeze.json').write_bytes((ROOT/'results/round1/baseline_freeze.json').read_bytes())
        (out/'candidate_input.json').write_bytes(candidate.read_bytes())
        for c in cases: collector.write_json(out/'cases'/c['case_id']/'assignment.json',c)
    locks=[]
    try:
        for d in sorted({out,ROOT/'results/round1',ROOT/'results/round2',ROOT/'results/p4_refined_official'},key=str):
            locks.append(collector.collection_lock(d))
        collector.start_bridge()
        collector.status(out,'running',problem=3,planned=15)
        for case in cases:
            f=out/'cases'/case['case_id']
            if (f/'post_exit_audit.json').exists(): continue
            if (out/'STOP_AFTER_CASE').exists(): break
            for name,digest in plan['runtime_sha256'].items():
                if prior.sha256(ROOT/name)!=digest: raise ValueError('Runtime changed during collection')
            collector.collect({'cases':[case]},out,robot_id,'http://127.0.0.1:2026',1,
                              policy_runner=prior.run_policy)
            a=prior.read_object(f/'post_exit_audit.json')
            if a['status']!='complete' or a['source_total_post_exit'] is None or a['cleared']!=a['source_total_post_exit']:
                raise RuntimeError('Incomplete P3 case retained')
        collector.status(out,'stopped_normally',problem=3,planned=15)
    except Exception as exc:
        collector.status(out,'needs_attention',problem=3,error=str(exc))
        raise
    finally:
        collector.close_bridge()
        for lock in reversed(locks): lock.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=ROOT/'results/p3_latest_official')
    p.add_argument('--robot-id',required=True)
    a=p.parse_args()
    run(a.out,a.robot_id)
