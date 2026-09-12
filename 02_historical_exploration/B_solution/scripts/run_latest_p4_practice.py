"""Run only the user's requested latest P4 policy, retaining the frozen policy."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import run_p4_refined_official as frozen
import collect_official_round as collector


def run(out,robot_id,maximum=30):
    checked=frozen.preflight(out)
    out=checked['out']
    cases=[c for c in checked['plan']['cases'] if c['stage']=='validation' and c['arm']=='combined_cover']
    if len(cases)!=30: raise ValueError('Expected 30 frozen latest-policy cases')
    scope_path=out/'scope_update.json'
    scope={'scope':'latest P4 combined_cover only; single-arm descriptive practice results',
        'user_instruction':'不用加那么多工程审查啊，就跟这个输出表格一样，然后帮我测试最新方案的结果就可以',
        'selected_case_ids':[c['case_id'] for c in cases], 'planned_cases':30,
        'cancelled_case_ids':[c['case_id'] for c in checked['plan']['cases'] if c not in cases],
        'cancelled_cases_are_failures':False,'comparison_not_executed':True,
        'official_freeze_sha256':checked['frozen']['freeze_sha256'],
        'dispatcher_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'formal_tests':0,'policy_changed':False}
    if scope_path.exists():
        if frozen.read(scope_path)!=scope: raise ValueError('Scope has changed; preserve and inspect')
    else:
        collector.write_json(scope_path,scope)
    locks=[]
    done=0
    try:
        for directory in sorted({out,ROOT/'results/round1',ROOT/'results/round2'},key=str):
            locks.append(collector.collection_lock(directory))
        collector.start_bridge()
        collector.status(out,'running',scope='latest_only_30')
        for case in cases:
            folder=out/'cases'/case['case_id']
            if (folder/'post_exit_audit.json').exists(): continue
            if done>=maximum or (out/'STOP_AFTER_CASE').exists(): break
            frozen.preflight(out)
            collector.collect({'cases':[case]},out,robot_id,'http://127.0.0.1:2026',1,
                              policy_runner=frozen.run_policy)
            a=frozen.read(folder/'post_exit_audit.json')
            done+=1
            if a['status']!='complete' or a['source_total_post_exit'] is None or a['cleared']!=a['source_total_post_exit']:
                raise RuntimeError('Incomplete practice case retained; inspect before sending more cases')
        collector.status(out,'stopped_normally',scope='latest_only_30',new_cases=done)
    except Exception as exc:
        collector.status(out,'needs_attention',scope='latest_only_30',error=str(exc))
        raise
    finally:
        collector.close_bridge()
        for lock in reversed(locks): lock.close()
    print(json.dumps({'latest_cases_completed_this_call':done,'planned_latest_cases':30}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=ROOT/'results/p4_refined_official')
    p.add_argument('--robot-id',required=True)
    p.add_argument('--max-new',type=int,default=30)
    a=p.parse_args()
    run(a.out,a.robot_id,a.max_new)
