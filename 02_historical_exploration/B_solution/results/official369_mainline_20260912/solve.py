"""Run the released policy on one local case, or explicitly selected HTTP mode."""
from pathlib import Path
import argparse
import json
import sys
import time
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'vendor'))
sys.path.insert(0,str(ROOT))
from bsolver.protocol import RobotClient,HTTPTransport
from bsolver.simulator import LocalSimulator,Source,FixedErrorField
from policies import make_policy


def main():
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--local-case',type=Path)
    mode.add_argument('--http',action='store_true')
    p.add_argument('--case-index',type=int,default=0)
    p.add_argument('--problem',type=int,choices=[3,4])
    p.add_argument('--robot-id')
    p.add_argument('--arena-id',choices=['default'],default='default')
    p.add_argument('--base-url',default='http://127.0.0.1:2026')
    p.add_argument('--variant')
    p.add_argument('--output',type=Path)
    a=p.parse_args()
    env=None
    if a.local_case:
        data=json.loads(a.local_case.read_text(encoding='utf-8-sig'))
        if isinstance(data,list):data=data[a.case_index]
        case=data.get('case',data)
        problem=case['problem'];f=case['error_field'];robot=a.robot_id or 'local-team'
        env=LocalSimulator([Source(**s) for s in case['sources']],robot_id=robot,enforce_case_size=True,
            error_field=FixedErrorField(f['seed'],f['mode'],f['correlation_length_m']))
        transport=env
    else:
        if a.problem is None or not a.robot_id:
            p.error('--http requires --problem and --robot-id; open the intended practice arena first')
        problem=a.problem;robot=a.robot_id;transport=HTTPTransport(a.base_url)
    release=json.loads((ROOT/'release.json').read_text(encoding='utf-8'))
    variant=a.variant or release['recommended'][str(problem)]
    dest=a.output or ROOT/'user_runs'/time.strftime('%Y%m%d_%H%M%S')
    dest.mkdir(parents=True,exist_ok=False)
    client=RobotClient(robot_id=robot,transport=transport,log_path=dest/'requests.jsonl')
    solver=make_policy(client,problem,variant,dest/'decisions.jsonl')
    try:result=solver.run()
    finally:client.close_log()
    result.update(variant=variant,environment='local' if env else 'official_http')
    if env:result['local_evaluation']=env.summary()
    (dest/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':result['status'],'variant':variant,'total_virtual_time_s':result['total_virtual_time_s'],
                      'clear_successes':result['clear_successes'],'output':str(dest)},ensure_ascii=False))
    return 0 if result['status']=='complete' else 1


if __name__=='__main__':raise SystemExit(main())
