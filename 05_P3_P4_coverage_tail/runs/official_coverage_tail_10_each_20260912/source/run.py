"""Paired local experiments. Accept JSON worlds, directories, or a dataset ZIP."""
from pathlib import Path
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
import hashlib
import json
import statistics
import sys
import time
import traceback
import zipfile

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'vendor'))
sys.path.insert(0,str(ROOT/'runtime'))
from feedback.solver import make_solver,ARMS


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def worlds(value):
    if isinstance(value,list):
        for item in value:yield from worlds(item)
    elif isinstance(value,dict):
        if value.get('problem') in (3,4) and isinstance(value.get('sources'),list) and 'error_field' in value:
            yield value
        else:
            for key in ('case','scenarios','cases'):
                if key in value:yield from worlds(value[key])


def load_cases(path):
    path=Path(path);found=[]
    if path.suffix.lower()=='.zip':
        with zipfile.ZipFile(path) as z:
            for name in sorted(z.namelist()):
                if name.lower().endswith('.json'):
                    try:found.extend(worlds(json.loads(z.read(name).decode('utf-8-sig'))))
                    except (UnicodeError,json.JSONDecodeError):continue
    else:
        for file in sorted(path.rglob('*.json')) if path.is_dir() else [path]:
            try:found.extend(worlds(read(file)))
            except (UnicodeError,json.JSONDecodeError):continue
    unique={}
    for case in found:
        key=hashlib.sha256(json.dumps({k:case[k] for k in ('problem','sources','error_field')},sort_keys=True).encode()).hexdigest()
        case=dict(case);case['_world_key']=key
        case.setdefault('case_id',key[:16])
        unique.setdefault(key,case)
    if not unique:raise ValueError('No P3/P4 worlds with sources and error_field were found')
    return list(unique.values())


def base_specs():
    old=read(ROOT/'inputs/previous_selection.json')
    return {p:old['validation_specs'][old['choices'][str(p)]] for p in (3,4)}


def run_one(case,arm,out,prior,options):
    from bsolver_frozen.protocol import RobotClient
    from bsolver_frozen.simulator import LocalSimulator,Source,FixedErrorField
    from bsolver.geometry import contains
    out=Path(out)/'cases'/case['_world_key'][:16]/arm
    out.mkdir(parents=True,exist_ok=False)
    definition=case['error_field']
    environment=LocalSimulator([Source(**s) for s in case['sources']],robot_id='local-experiment',
         enforce_case_size=True,error_field=FixedErrorField(definition['seed'],definition['mode'],
                                                          definition['correlation_length_m']))
    client=RobotClient('local-experiment',transport=environment,log_path=out/'requests.jsonl')
    started=time.monotonic();solver=None
    try:
        solver=make_solver(client,case['problem'],base_specs()[case['problem']],arm,
                           prior=prior,options=options,decision_log=out/'decisions.jsonl')
        result=solver.run()
    except Exception as exc:
        result=dict(status='error',error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc(),
                    total_virtual_time_s=client.virtual_time)
    finally:client.close_log()
    truth=environment.summary()
    truth_positions={s['channel']:s['position'] for s in case['sources']}
    violations=[]
    for event in solver.decisions if solver else []:
        k=event.get('knowledge')
        if not k or k['channel'] not in truth_positions:continue
        point=truth_positions[k['channel']]
        if not contains(k['hull'],point,tol=1e-4):violations.append(event['sequence'])
        cells=k.get('region_cells')
        if cells is not None and k['status']!='cleared' and not any(contains(c,point,tol=1e-4) for c in cells):
            violations.append(event['sequence'])
    n=len(case['sources']);cleared=result.get('clear_successes',0)
    complete=(result['status']=='complete' and truth['all_cleared'] and not violations)
    result.update(case_id=case['case_id'],world_key=case['_world_key'],problem=case['problem'],arm=arm,
                  error_mode=definition['mode'],source_total=n,
                  directional_total=sum(s.get('direction_deg') is not None for s in case['sources']),
                  true_all_clear=truth['all_cleared'],complete=complete,clear_fraction=truth['clearance_ratio'],
                  T_per_cleared_s=result['total_virtual_time_s']/cleared if cleared else None,
                  T_per_source_s=result['total_virtual_time_s']/n,
                  loss_s=result['total_virtual_time_s'] if complete else 360000.,
                  region_violations=violations,wall_s=time.monotonic()-started,environment='frozen_local')
    write(out/'result.json',result)
    return result


def report(rows,out):
    groups=[]
    baseline={r['world_key']:r for r in rows if r['arm']=='baseline'}
    for problem in (3,4):
        for arm in ARMS:
            group=[r for r in rows if r['problem']==problem and r['arm']==arm]
            if not group:continue
            successful=[r for r in group if r['complete']]
            differences=[r['loss_s']-baseline[r['world_key']]['loss_s'] for r in group if r['world_key'] in baseline]
            groups.append(dict(problem=problem,arm=arm,runs=len(group),complete=len(successful),
                mean_clear_fraction=statistics.mean(r['clear_fraction'] for r in group),
                mean_T_success_s=statistics.mean(r['total_virtual_time_s'] for r in successful) if successful else None,
                mean_T_per_source_success_s=statistics.mean(r['T_per_source_s'] for r in successful) if successful else None,
                mean_loss_s=statistics.mean(r['loss_s'] for r in group),
                mean_paired_loss_difference_s=statistics.mean(differences) if differences else None,
                max_wall_s=max(r['wall_s'] for r in group)))
    write(Path(out)/'results.json',rows)
    write(Path(out)/'summary.json',dict(groups=groups,runs=len(rows),
         note='360000 is an explicit incomplete-run evaluation penalty, not an official score or completion time.'))
    lines=['# 全反馈实验结果','','|题目|策略|完整清除|平均清除比例|成功局平均时间/s|配对损失差/s|',
           '|---|---|---:|---:|---:|---:|']
    for g in groups:
        t=g['mean_T_success_s'];d=g['mean_paired_loss_difference_s']
        lines.append(f"|P{g['problem']}|{g['arm']}|{g['complete']}/{g['runs']}|{g['mean_clear_fraction']:.3f}|{t:.2f}|{d:.2f}|" if t is not None and d is not None else
                     f"|P{g['problem']}|{g['arm']}|{g['complete']}/{g['runs']}|{g['mean_clear_fraction']:.3f}|{t}|{d}|")
    lines+=['','失败局保留并计入损失；成功局平均时间不能单独用于选优。风险截断不宣称完整清除。']
    (Path(out)/'RESULTS.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return groups


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--arms',default='baseline,geometry,three_feedback,probability,lookahead')
    parser.add_argument('--prior',help='Prior fitted on a separate development/training dataset')
    parser.add_argument('--options',help='JSON numerical/planning controls, not scene truth')
    parser.add_argument('--per-group',type=int,default=0,help='Hash-fixed limit within problem/error-mode')
    parser.add_argument('--workers',type=int,default=2)
    args=parser.parse_args();out=Path(args.output).resolve()
    if out.exists():raise ValueError('Choose a new output directory')
    arms=args.arms.split(',')
    if len(set(arms))!=len(arms) or any(a not in ARMS for a in arms):raise ValueError('Invalid arms')
    cases=load_cases(args.dataset)
    if args.per_group:
        counters={};selected=[]
        for c in sorted(cases,key=lambda x:x['_world_key']):
            key=(c['problem'],c['error_field']['mode'])
            if counters.get(key,0)<args.per_group:selected.append(c);counters[key]=counters.get(key,0)+1
        cases=selected
    prior=read(args.prior) if args.prior else None
    options=read(args.options) if args.options else {}
    out.mkdir(parents=True)
    write(out/'plan.json',dict(arms=arms,cases=cases,prior=prior,options=options,
          purpose='paired experiment; no default winner promotion; no official API calls'))
    rows=[]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(run_one,c,a,str(out),prior,options) for c in cases for a in arms]
        for f in as_completed(futures):
            r=f.result();rows.append(r)
            print(json.dumps({k:r[k] for k in ('problem','arm','complete','total_virtual_time_s','wall_s')},ensure_ascii=False),flush=True)
    report(rows,out)
    print(json.dumps(dict(runs=len(rows),complete=sum(r['complete'] for r in rows),output=str(out)),ensure_ascii=False))


if __name__=='__main__':main()
