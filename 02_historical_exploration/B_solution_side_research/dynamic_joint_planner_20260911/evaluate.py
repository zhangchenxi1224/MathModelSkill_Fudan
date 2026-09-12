"""Offline evaluator. Only this module receives scenario truth."""
from concurrent.futures import ProcessPoolExecutor, as_completed
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import time

from dynamic_joint import make_solver
from dynamic_joint._core.protocol import RobotClient
from dynamic_joint._core.simulator import LocalSimulator, Source, FixedErrorField
from dynamic_joint._core.strategy import Solver, SolverConfig
from dynamic_joint._core.nosignal_sensing import NoSignalSolver
from dynamic_joint._core.experiments import generate_case
from dynamic_joint._core.geometry import contains

ROOT=Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, data):
    Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def digest(data):
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def source_files():
    files=list((ROOT/'dynamic_joint').rglob('*.py'))+[ROOT/'evaluate.py',ROOT/'run.py',ROOT/'export_prior.py']
    return {str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}


def load_case(path):
    case=read(path)
    if type(case.get('problem')) is not int or case['problem'] not in (3,4):
        raise ValueError('case.problem must be 3 or 4')
    if not isinstance(case.get('sources'),list) or not 10<=len(case['sources'])<=16:
        raise ValueError('external case must contain 10..16 sources')
    if case['problem']==3 and any(s.get('direction_deg') is not None for s in case['sources']):
        raise ValueError('problem 3 cannot contain directional sources')
    actual=digest({k:v for k,v in case.items() if k!='scenario_sha256'})
    if 'scenario_sha256' in case and case['scenario_sha256']!=actual:
        raise ValueError('scenario_sha256 mismatch')
    field=case.get('error_field')
    if field is None:
        if type(case.get('seed')) is not int or 'error_mode' not in case:
            raise ValueError('explicit error_field or legacy seed/error_mode is required')
        field=dict(seed=case['seed'],mode=case['error_mode'],correlation_length_m=150.)
    if type(field.get('seed')) is not int: raise ValueError('invalid error seed')
    # Constructor validates mode and length; use the ERROR seed, not geometry seed.
    error=FixedErrorField(field['seed'],field['mode'],field['correlation_length_m'])
    return case,actual,field,error


def solve_case(case_path, output, policy='dynamic', config_path=None):
    output=Path(output).resolve()
    if output.exists(): raise FileExistsError('choose a new output directory')
    if policy not in ('dynamic','champion'): raise ValueError('unsupported policy')
    case,case_hash,error_spec,error=load_case(case_path)
    spec=read(config_path or ROOT/'configs/dynamic_h2.json') if policy=='dynamic' else dict(name='champion',limit=1)
    frozen=source_files()
    env=LocalSimulator([Source(**s) for s in case['sources']],robot_id='local-team',
                       enforce_case_size=True,error_field=error)
    output.mkdir(parents=True,exist_ok=False)
    write(output/'case.json',case)
    client=RobotClient('local-team',transport=env,log_path=output/'requests.jsonl')
    try:
        if policy=='dynamic':
            solver=make_solver(client,case['problem'],spec,decision_log=output/'decisions.jsonl')
        else:
            cls=Solver if case['problem']==3 else NoSignalSolver
            solver=cls(client,SolverConfig(problem=case['problem'],local_measure_limit=1),
                       decision_log=output/'decisions.jsonl')
        result=solver.run()
        truth=env.summary()
        positions={s['channel']:s['position'] for s in case['sources']}
        violations=[]
        for event in solver.decisions:
            k=event.get('knowledge')
            if k and k['channel'] in positions and not contains(k['hull'],positions[k['channel']],tol=1e-4):
                violations.append([event['sequence'],k['channel']])
        valid=source_files()==frozen
        audit=(result['status']=='complete' and truth['all_cleared'] and not violations and
               bool(result['stop_evidence']) and abs(result['timing_residual_s'])<.001 and valid)
        decisions=[e for e in solver.decisions if e['event']=='dynamic_decision']
        completed=[e for e in decisions if 'evaluations' in e['planner']]
        # Count ROOT plans that explicitly consider switching channel on step two.
        planned_switches=sum(any(b['second_action'] is not None and
                                 b['second_action']['channel']!=evaluation['action']['channel']
                                 for evaluation in e['planner']['evaluations'] for b in evaluation['branches'])
                             for e in completed)
        conditional_plans=0
        for e in completed:
            if any(len({json.dumps(b['second_action'],sort_keys=True) for b in evaluation['branches']})>1
                   for evaluation in e['planner']['evaluations']): conditional_plans+=1
        result.update(policy=spec.get('name',policy),spec=spec,case_id=case['case_id'],
                      scenario_sha256=case_hash,source_files=frozen,source_valid=valid,
                      effective_error_field=error_spec,environment='frozen_local_simulator',official_actions=0,
                      source_total=len(positions),clear_fraction=truth['clearance_ratio'],environment_summary=truth,
                      hull_invariant_violations=violations,audit_complete=audit,
                      total_time_per_source_s=result['total_virtual_time_s']/len(positions),
                      plans_with_cross_channel_second_step=planned_switches,
                      plans_with_outcome_dependent_second_step=conditional_plans)
        write(output/'result.json',result)
    finally:
        client.close_log()
    for name in ('requests.jsonl','decisions.jsonl'):
        path=output/name
        with path.open('rb') as source,gzip.open(str(path)+'.gz','wb') as target:
            for block in iter(lambda:source.read(1<<20),b''): target.write(block)
        path.unlink()
    return result


def _job(arguments):
    return solve_case(*arguments)


def smoke(output, workers=2, include_h1=False):
    """Eight NEW synthetic worlds, distinct from the main development/holdout."""
    output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    (output/'cases').mkdir()
    specifications=[(3,10,'random','mixed','deterministic'),(3,16,'boundary','min','extreme'),
                    (3,13,'clustered','min','correlated'),(3,10,'center_and_boundary','mixed','positive'),
                    (4,10,'random','mixed','deterministic'),(4,16,'boundary','min','extreme'),
                    (4,13,'clustered','min','correlated'),(4,15,'hidden_outward_last','min','negative')]
    specs=[('dynamic','dynamic',ROOT/'configs/dynamic_h2.json'),('champion','champion',None)]
    if include_h1: specs.append(('h1','dynamic',ROOT/'configs/dynamic_h1.json'))
    registered=source_files()
    write(output/'manifest.json',dict(kind='engineering_smoke_not_holdout',created_unix=time.time(),
          source_files=registered,specifications=specifications,
          policies={name:read(config) if config else {'name':'champion','limit':1} for name,_,config in specs},
          note='Fresh synthetic seed stream; not a performance selection or official evaluation.'))
    jobs=[]
    for i,(problem,n,layout,radius,mode) in enumerate(specifications):
        case=generate_case(88123010+i,problem,n,layout,radius,mode)
        case['error_field']=dict(seed=77213010+i,mode=mode,correlation_length_m=150.)
        case['scenario_sha256']=digest(case)
        path=output/'cases'/f'{i:02d}.json'; write(path,case)
        for name,policy,config in specs:
            jobs.append((path,output/'runs'/f'{i:02d}'/name,policy,config))
    results=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(_job,job):job for job in jobs}
        for future in as_completed(futures):
            result=future.result(); results.append(result)
            print(json.dumps(dict(case=result['case_id'],policy=result['policy'],audit=result['audit_complete'],
                                  T_s=result['total_virtual_time_s'],wall_s=result['program_real_time_s']),ensure_ascii=False),flush=True)
    summary=summarize(results)
    summary['source_valid']=source_files()==registered and all(r['source_valid'] for r in results)
    write(output/'summary.json',summary)
    return summary


def summarize(results):
    groups={}
    for r in results: groups.setdefault(r['policy'],[]).append(r)
    summary={'runs':len(results),'all_audited_complete':all(r['audit_complete'] for r in results),'by_policy':{}}
    champion={r['case_id']:r for r in groups.get('champion',[])}
    for name,rows in groups.items():
        changes=[(r['total_virtual_time_s']/champion[r['case_id']]['total_virtual_time_s']-1)*100
                 for r in rows if r['case_id'] in champion]
        summary['by_policy'][name]=dict(runs=len(rows),complete=sum(r['audit_complete'] for r in rows),
            mean_T_s=statistics.mean(r['total_virtual_time_s'] for r in rows),
            mean_T_per_N_s=statistics.mean(r['total_time_per_source_s'] for r in rows),
            median_wall_s=statistics.median(r['program_real_time_s'] for r in rows),
            max_wall_s=max(r['program_real_time_s'] for r in rows),
            completed_plans=sum(r.get('completed_plans',0) for r in rows),
            budget_fallbacks=sum(r.get('budget_fallbacks',0) for r in rows),
            plans_with_cross_channel_second_step=sum(r['plans_with_cross_channel_second_step'] for r in rows),
            plans_with_outcome_dependent_second_step=sum(r['plans_with_outcome_dependent_second_step'] for r in rows),
            paired_mean_change_percent=statistics.mean(changes) if changes else None,
            paired_worst_change_percent=max(changes) if changes else None)
    return summary
