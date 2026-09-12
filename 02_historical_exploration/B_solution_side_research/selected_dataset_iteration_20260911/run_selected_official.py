"""Run the frozen selected P3/P4 policies on ten official practice cases each."""
from pathlib import Path
import argparse
import json
import sys

ROOT=Path(__file__).resolve().parent
MAIN=ROOT.parents[1]/'B_solution'
sys.path.insert(0,str(MAIN/'scripts'))
import collect_official_round as collector
# The collector's optional default policy uses the main project; our injected
# policy always imports this release's frozen runtime first.
sys.path.insert(0,str(ROOT/'vendor'))
sys.path.insert(0,str(ROOT))
import experiment as e
from bsolver.protocol import RobotClient, HTTPTransport
from methods.solver import make_solver


def run_policy(case,folder,robot_id,base_url):
    client=RobotClient(robot_id=robot_id,transport=HTTPTransport(base_url),log_path=folder/'requests.jsonl')
    try:
        solver=make_solver(client,case['problem'],case['policy_spec'],decision_log=folder/'decisions.jsonl')
        result=solver.run()
    finally:
        client.close_log()
    result.update(environment='official_practice',case_id=case['case_id'],protocol='selected',
                  variant=case['variant'],policy_spec=case['policy_spec'],
                  source_total=None,directional_total=None,case_code=None)
    collector.write_json(folder/'result.json',result)
    return result


def run(out,robot_id):
    out=Path(out).resolve(); out.mkdir(parents=True,exist_ok=True)
    selection=e.read(ROOT/'campaign/frozen_selection.json')
    if e.files()!=selection['source_files']:
        raise RuntimeError('Selected policy source changed')
    cases=[]
    for problem in (3,4):
        name=selection['choices'][str(problem)]
        for index in range(1,11):
            cases.append(dict(case_id=f'best-p{problem}-{index:03d}',problem=problem,
                 protocol='selected',arm='selected',split='descriptive',stage='official_practice_10',
                 pilot=False,sequence=len(cases),environment='official_practice',
                 variant=name,policy_spec=selection['validation_specs'][name]))
    plan=dict(practice_only=True,formal_authorized=False,planned_cases=20,
              per_problem=10,selection_sha256=selection['selection_sha256'],cases=cases)
    if (out/'plan.json').exists():
        if e.read(out/'plan.json')!=plan:raise ValueError('Existing plan differs')
    else:
        collector.write_json(out/'plan.json',plan)
        (out/'baseline_freeze.json').write_bytes((MAIN/'results/round1/baseline_freeze.json').read_bytes())
        (out/'selected_input.json').write_bytes((ROOT/'campaign/frozen_selection.json').read_bytes())
        for case in cases:collector.write_json(out/'cases'/case['case_id']/'assignment.json',case)
    locks=[]
    try:
        for directory in sorted({out,MAIN/'results/round1'},key=str):
            locks.append(collector.collection_lock(directory))
        collector.start_bridge()
        collector.status(out,'running',planned_cases=20)
        for case in cases:
            folder=out/'cases'/case['case_id']
            if (folder/'post_exit_audit.json').exists():continue
            if (out/'STOP_AFTER_CASE').exists():break
            if e.files()!=selection['source_files']:raise RuntimeError('Selected policy source changed')
            collector.collect({'cases':[case]},out,robot_id,'http://127.0.0.1:2026',1,policy_runner=run_policy)
            result=e.read(folder/'post_exit_audit.json')
            if result['status']!='complete' or result['source_total_post_exit'] is None or result['cleared']!=result['source_total_post_exit']:
                raise RuntimeError('Incomplete official practice retained; inspect current case')
        collector.status(out,'stopped_normally',planned_cases=20)
    except Exception as exc:
        collector.status(out,'needs_attention',error=str(exc))
        raise
    finally:
        collector.close_bridge()
        for lock in reversed(locks):lock.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'runs/official_practice_10_each')
    parser.add_argument('--robot-id',required=True)
    args=parser.parse_args()
    run(args.out,args.robot_id)
