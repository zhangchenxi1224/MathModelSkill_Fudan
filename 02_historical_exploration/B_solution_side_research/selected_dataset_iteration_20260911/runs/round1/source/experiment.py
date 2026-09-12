"""Replay exact user worlds; policies see public observations only.

Every stage executes a private source snapshot. Dataset cases are development
regression only; fresh confirmation is generated before screening and gated by
a frozen selection. No official service is contacted.
"""
from pathlib import Path
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import Counter
import gzip
import hashlib
import json
import math
import random
import shutil
import statistics
import subprocess
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parent


def canonical(data):
    return json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(data):
    return hashlib.sha256(canonical(data).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path,data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temp.replace(path)


def files():
    selected=[ROOT/'run.py',ROOT/'experiment.py']
    for folder in ('methods','vendor/bsolver','vendor/diverse'):
        selected.extend(sorted((ROOT/folder).glob('*.py')))
    return {str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in selected}


def world_digest(case):
    return digest({'sources':case['sources'],'error_field':case['error_field']})


def input_cases(dataset):
    root=Path(dataset)
    result=[]
    for row in read(root/'index.json'):
        case=read(root/row['file'])
        if digest({k:v for k,v in case.items() if k!='scenario_sha256'})!=case['scenario_sha256']:
            raise ValueError('input scenario hash changed')
        result.append({'case':case,'hypothesis':row['hypothesis'],'role':'development_regression'})
    if len(result)!=360 or len({world_digest(r['case']) for r in result})!=360:
        raise ValueError('expected 360 unique original worlds')
    return result


def prepare_campaign(args):
    from bsolver.round_experiments import make_scenario,model_distribution,categorical,seed_for
    output=Path(args.output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('campaign requires a new empty directory')
    dataset=Path(args.dataset).resolve()
    original=input_cases(dataset)
    calibration=read(args.calibration)
    prior_worlds={world_digest(r['case']) for r in original}
    for path in args.exclude_manifest:
        source=read(path)
        for case in source.get('scenarios',source.get('cases',[])):
            if 'sources' in case and 'error_field' in case:
                prior_worlds.add(world_digest(case))
    fresh=[]
    for problem in (3,4):
        distribution=model_distribution(calibration,problem,'smoothed_joint')
        for h,mode in [('H1','correlated'),('H2','extreme')]:
            for index in range(args.fresh_per_group):
                seed=seed_for(args.seed,'fresh_confirmation',problem,h,index)
                n,nd=categorical(random.Random(seed_for(seed,'composition')),distribution)
                case=make_scenario(seed,problem,n,nd,
                      pool='new_confirmation',model='smoothed_joint',error_mode=mode,
                      correlation_length=150.,index=index,partition='confirmation')
                if world_digest(case) in prior_worlds:
                    raise ValueError('fresh confirmation overlaps an earlier world')
                prior_worlds.add(world_digest(case))
                fresh.append({'case':case,'hypothesis':h,'role':'fresh_synthetic_confirmation'})
    stress=[]
    for problem in (3,4):
        for index in range(24):
            seed=seed_for(args.seed,'stress',problem,index)
            n=10 if index%2==0 else 16
            nd=n if problem==4 else 0
            layout=('boundary_outward','boundary_tangent','hidden_outward_last',
                    'clustered','center_and_boundary','near_lattice_vertices')[index%6]
            mode=('extreme','correlated')[(index//12)%2]
            case=make_scenario(seed,problem,n,nd,pool='new_stress',layout=layout,
                              radius_mode='min' if (index//6)%2==0 else 'max',error_mode=mode,index=index)
            if world_digest(case) in prior_worlds:
                raise ValueError('new stress overlaps old world')
            prior_worlds.add(world_digest(case))
            stress.append({'case':case,'hypothesis':'stress','role':'fresh_synthetic_stress'})
    write(output/'development_cases.json',original)
    write(output/'confirmation_cases.json',fresh)
    write(output/'stress_cases.json',stress)
    runtime_hashes={}
    for relative,expected in read(dataset/'manifest.json')['runtime_files_sha256'].items():
        if hashlib.sha256((dataset/relative).read_bytes()).hexdigest()!=expected:
            raise ValueError('dataset runtime changed')
        runtime_hashes[str(Path(relative).relative_to('runtime')).replace('\\','/')]=expected
    policy={'objective':'Complete clearance first; then mean total virtual seconds on 50/50 H1/H2, per problem.',
            'secondary':'Mean T/N, p95, maximum regression, failures, wall time, movement and calls.',
            'development_role':'All 360 supplied worlds are reused development/regression, including old confirmation labels.',
            'selection':'Require all development runs audited complete; both H means no worse than champion; minimize equal-H mean T. Select at most one policy per problem, never use H or case_id in policy.',
            'confirmation':'Freeze policy and code before running new synthetic confirmation. No retuning after seeing it. Negative outcome retains champion as validated recommendation.',
            'failure_loss_seconds':360000,'fresh_per_group':args.fresh_per_group,
            'confirmatory_ci':'Paired case bootstrap; two problem-level primary contrasts, 97.5% intervals (Bonferroni 5% family). H-specific 95% intervals descriptive.',
            'dataset_runtime':str(dataset/'runtime'),'dataset_sha256':digest(read(dataset/'manifest.json')),
            'dataset_runtime_hashes':runtime_hashes,
            'case_file_hashes':{'development':digest(original),'confirmation':digest(fresh),'stress':digest(stress)},
            'calibration_sha256':digest(calibration),'campaign_seed':args.seed,
            'source_at_registration':files()}
    write(output/'protocol.json',policy)
    print(json.dumps({'campaign':str(output),'development':len(original),'fresh_confirmation':len(fresh),'stress':len(stress)}))


def make_stage(args):
    output=Path(args.output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('use a new stage directory')
    campaign=Path(args.campaign).resolve()
    cases=read(campaign/f'{args.partition}_cases.json')
    protocol=read(campaign/'protocol.json')
    if digest(cases)!=protocol['case_file_hashes'][args.partition]:
        raise ValueError('registered campaign worlds changed')
    for name,expected in protocol['dataset_runtime_hashes'].items():
        if hashlib.sha256((Path(protocol['dataset_runtime'])/name).read_bytes()).hexdigest()!=expected:
            raise ValueError('registered dataset runtime changed')
    specs=read(args.specs)
    if 'champion' not in specs:
        raise ValueError('strong champion control required')
    selection=None
    if args.partition!='development':
        selection=read(campaign/'frozen_selection.json')
        if selection['selection_sha256']!=digest({k:v for k,v in selection.items() if k!='selection_sha256'}):
            raise ValueError('frozen selection modified')
        if specs!=selection['validation_specs']:
            raise ValueError('validation must run the exact frozen candidate specifications')
        if files()!=selection['source_files']:
            raise ValueError('source changed after selection')
    if args.group_limit:
        if args.partition!='development':
            raise ValueError('cannot truncate confirmation after registration')
        groups={}
        for row in cases:
            groups.setdefault((row['case']['problem'],row['hypothesis']),[]).append(row)
        cases=[]
        for group,rows in sorted(groups.items()):
            rows.sort(key=lambda r:digest(['screen-subset-v1',r['case']['case_id']]))
            cases.extend(rows[:args.group_limit])
    output.mkdir(parents=True)
    source=files()
    for relative in source:
        target=output/'source'/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((ROOT/relative).read_bytes())
    for relative in protocol['dataset_runtime_hashes']:
        target=output/'runtime'/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((Path(protocol['dataset_runtime'])/relative).read_bytes())
    plan={'partition':args.partition,'cases':cases,'specs':specs,'source_files':source,
          'protocol_sha256':digest(protocol),
          'dataset_runtime':str(output/'runtime'),
          'dataset_runtime_hashes':protocol['dataset_runtime_hashes'],
          'output':str(output),'case_file_sha256':digest(read(campaign/f'{args.partition}_cases.json')),
          'selection_sha256':selection['selection_sha256'] if selection else None}
    plan['plan_sha256']=digest(plan)
    write(output/'plan.json',plan)
    print(json.dumps({'stage':str(output),'cases':len(cases),'specs':list(specs)}),flush=True)
    result=subprocess.run([sys.executable,str(output/'source/run.py'),'execute','--plan',str(output/'plan.json'),'--workers',str(args.workers)])
    if result.returncode:
        raise RuntimeError(f'stage execution failed: {result.returncode}')


def runtime_valid(plan):
    return all(hashlib.sha256((Path(plan['dataset_runtime'])/name).read_bytes()).hexdigest()==expected
               for name,expected in plan['dataset_runtime_hashes'].items())


def worker(job):
    row,name,spec,plan=job
    case=row['case']; dest=Path(plan['output'])/'cases'/case['case_id']/name
    started=time.monotonic(); solver=None; client=None
    try:
        if files()!=plan['source_files'] or not runtime_valid(plan):
            raise ValueError('snapshot source drift')
        dest.mkdir(parents=True,exist_ok=False)
        sys.path.insert(0,plan['dataset_runtime'])
        from bsolver_frozen.protocol import RobotClient
        from bsolver_frozen.simulator import LocalSimulator,Source,FixedErrorField
        from bsolver.geometry import contains
        from methods.solver import make_solver
        definition=case['error_field']
        env=LocalSimulator([Source(**s) for s in case['sources']],robot_id='local-team',enforce_case_size=True,
                   error_field=FixedErrorField(definition['seed'],definition['mode'],definition['correlation_length_m']))
        client=RobotClient('local-team',transport=env,log_path=dest/'requests.jsonl')
        solver=make_solver(client,case['problem'],spec,decision_log=dest/'decisions.jsonl')
        result=solver.run()
        client.close_log()
        truth=env.summary()
        truth_by_channel={s['channel']:s['position'] for s in case['sources']}
        violations=[]
        counts=Counter()
        for event in solver.decisions:
            state=event.get('knowledge')
            if state and state['channel'] in truth_by_channel and not contains(state['hull'],truth_by_channel[state['channel']],tol=1e-4):
                violations.append([event['sequence'],state['channel']])
            if event['event']=='planning_fallback': counts['planning_fallbacks']+=1
            if event['event']=='measure': counts['measure_'+event.get('reason','unknown')]+=1
            if event['event']=='clear': counts['clear_'+event['certificate']['type']]+=1
        complete=(result['status']=='complete' and truth['all_cleared'] and not violations
                  and bool(result['stop_evidence']) and abs(result['timing_residual_s'])<.001)
        result.update(audit_complete=complete,clear_fraction=truth['clearance_ratio'],source_total=len(case['sources']),
                      hull_invariant_violations=violations,environment_summary=truth,diagnostic_counts=dict(counts),
                      loss_s=result['total_virtual_time_s'] if complete else 360000.)
    except Exception as exc:
        result={'status':'harness_failed','error':f'{type(exc).__name__}: {exc}','traceback':traceback.format_exc(),
                'audit_complete':False,'loss_s':360000.,'total_virtual_time_s':None,'source_total':len(case['sources']),
                'hull_invariant_violations':[],'program_real_time_s':time.monotonic()-started}
    finally:
        if client: client.close_log()
    result.update(variant=name,spec=spec,case_id=case['case_id'],problem=case['problem'],hypothesis=row['hypothesis'],
                  scenario_sha256=case['scenario_sha256'],world_sha256=world_digest(case),role=row['role'],
                  plan_sha256=plan['plan_sha256'],source_valid=files()==plan['source_files'] and runtime_valid(plan))
    if not result['source_valid']:
        result.update(audit_complete=False,loss_s=360000.,status='invalid_source')
    write(dest/'result.json',result)
    for name in ('requests.jsonl','decisions.jsonl'):
        p=dest/name
        if p.exists():
            with p.open('rb') as src,gzip.open(str(p)+'.gz','wb') as dst:
                shutil.copyfileobj(src,dst)
            p.unlink()
    return result


def execute(args):
    plan=read(args.plan); output=Path(plan['output'])
    if (output/'INVALID.json').exists():
        raise ValueError('this stage was invalidated; create a new stage')
    if plan['plan_sha256']!=digest({k:v for k,v in plan.items() if k!='plan_sha256'}):
        raise ValueError('plan edited')
    if files()!=plan['source_files']:
        raise ValueError('source drift')
    for name,expected in plan['dataset_runtime_hashes'].items():
        if hashlib.sha256((Path(plan['dataset_runtime'])/name).read_bytes()).hexdigest()!=expected:
            raise ValueError('dataset environment drift')
    lock=output/'RUNNING.lock'
    with lock.open('x') as fp: fp.write(str(time.time()))
    started=time.monotonic();results=[]
    try:
        jobs=[]
        for row in plan['cases']:
            names=[n for n,s in plan['specs'].items() if row['case']['problem'] in s.get('problems',[3,4])]
            random.Random(row['case']['seed']^8712).shuffle(names)
            for name in names:
                path=output/'cases'/row['case']['case_id']/name/'result.json'
                if path.exists():
                    result=read(path)
                    if (result['plan_sha256']!=plan['plan_sha256'] or result['scenario_sha256']!=row['case']['scenario_sha256']
                          or result['spec']!=plan['specs'][name] or not result['source_valid']):
                        raise ValueError('resume provenance mismatch')
                    results.append(result)
                else:
                    if path.parent.exists():
                        archived=output/'interrupted'/f'{row["case"]["case_id"]}-{name}-{time.time_ns()}'
                        if not path.parent.resolve().is_relative_to(output.resolve()) or not archived.resolve().is_relative_to(output.resolve()):
                            raise ValueError('unsafe interrupted paths')
                        archived.parent.mkdir(parents=True,exist_ok=True)
                        path.parent.rename(archived)
                    jobs.append((row,name,plan['specs'][name],plan))
        expected=len(jobs)+len(results)
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            pending=[pool.submit(worker,job) for job in jobs]
            for future in as_completed(pending):
                results.append(future.result())
                if len(results)%120==0 or len(results)==expected:
                    print(json.dumps({'done':len(results),'expected':expected,'complete':sum(r['audit_complete'] for r in results),'elapsed_s':round(time.monotonic()-started,1)}),flush=True)
        if files()!=plan['source_files'] or not runtime_valid(plan) or not all(r['source_valid'] for r in results):
            write(output/'INVALID.json',{'reason':'source drift'})
            raise ValueError('invalid stage')
        results.sort(key=lambda r:(r['case_id'],r['variant']))
        write(output/'results.json',results)
        summary=summarize(results)
        summary.update(partition=plan['partition'],expected_runs=expected,wall_s=time.monotonic()-started,workers=args.workers,plan_sha256=plan['plan_sha256'])
        write(output/'summary.json',summary)
    finally:
        lock.unlink(missing_ok=True)


def quantile(values,p):
    values=sorted(values)
    if not values:return None
    x=(len(values)-1)*p;return values[int(x)]*(1-x%1)+values[math.ceil(x)]*(x%1)


def interval(values,level=.95):
    if len(values)<2:return None
    rng=random.Random(764321)
    means=[statistics.mean(rng.choices(values,k=len(values))) for _ in range(2000)]
    return [quantile(means,(1-level)/2),quantile(means,1-(1-level)/2)]


def summarize(results):
    lookup={(r['case_id'],r['variant']):r for r in results}
    summaries=[]
    groupkeys={(r['problem'],r['hypothesis'],r['variant']) for r in results}
    groupkeys|={(r['problem'],'design_mixture',r['variant']) for r in results}
    for problem,hypothesis,name in sorted(groupkeys):
        rows=[r for r in results if r['problem']==problem and r['variant']==name and (hypothesis=='design_mixture' or r['hypothesis']==hypothesis)]
        pairs=[(lookup[(r['case_id'],'champion')],r) for r in rows]
        differences=[r['loss_s']-b['loss_s'] for b,r in pairs]
        pct=[100*(r['loss_s']/b['loss_s']-1) for b,r in pairs]
        successes=[r for r in rows if r['audit_complete']]
        summaries.append({'problem':problem,'hypothesis':hypothesis,'variant':name,'n':len(rows),
                 'complete':len(successes),'new_failures':sum(b['audit_complete'] and not r['audit_complete'] for b,r in pairs),
                 'mean_loss_s':statistics.mean(r['loss_s'] for r in rows),
                 'mean_T_per_N_s':statistics.mean(r['loss_s']/r['source_total'] for r in rows),
                 'p95_success_s':quantile([r['total_virtual_time_s'] for r in successes],.95),
                 'mean_difference_s':statistics.mean(differences),'paired_ci95_s':interval(differences),
                 'paired_ci975_s':interval(differences,.975) if hypothesis=='design_mixture' else None,
                 'mean_pair_pct':statistics.mean(pct),'regressions':sum(v>1e-5 for v in differences),
                 'worst_regression_s':max(differences),'worst_regression_pct':max(pct),
                 'max_program_real_s':max(r['program_real_time_s'] for r in rows),
                 'mean_walk_m':statistics.mean(r['walk_distance_m'] for r in rows if r.get('walk_distance_m') is not None)
                        if any(r.get('walk_distance_m') is not None for r in rows) else None,
                 'walk_observed_n':sum(r.get('walk_distance_m') is not None for r in rows)})
    return {'runs':len(results),'all_complete':sum(r['audit_complete'] for r in results),
            'hull_violations':sum(len(r['hull_invariant_violations']) for r in results),'groups':summaries,
            'interpretation':'Synthetic development/regression or separately frozen fresh synthetic validation; not official scores.'}


def freeze(args):
    campaign=Path(args.campaign).resolve(); output=campaign/'frozen_selection.json'
    if output.exists():raise ValueError('selection already frozen')
    stage=Path(args.stage).resolve(); plan=read(stage/'plan.json'); results=read(stage/'results.json')
    protocol=read(campaign/'protocol.json')
    if (plan['plan_sha256']!=digest({k:v for k,v in plan.items() if k!='plan_sha256'})
            or plan['protocol_sha256']!=digest(protocol)
            or plan['case_file_sha256']!=protocol['case_file_hashes']['development']
            or digest(plan['cases'])!=protocol['case_file_hashes']['development']):
        raise ValueError('selection must use the registered original 360 worlds')
    if plan['partition']!='development' or len(plan['cases'])!=360:
        raise ValueError('final selection requires all 360 user worlds')
    expected={(r['case']['case_id'],n) for r in plan['cases'] for n,s in plan['specs'].items() if r['case']['problem'] in s.get('problems',[3,4])}
    if len(results)!=len(expected) or {(r['case_id'],r['variant']) for r in results}!=expected:
        raise ValueError('incomplete full-dataset stage')
    if files()!=plan['source_files']:
        raise ValueError('root source differs from tested stage')
    for r in results:
        if not r['source_valid'] or r['plan_sha256']!=plan['plan_sha256'] or r['spec']!=plan['specs'][r['variant']]:
            raise ValueError('invalid result provenance')
    summary=summarize(results);choices={};validation={'champion':plan['specs']['champion']}
    for problem in (3,4):
        candidates=[]
        for name in plan['specs']:
            rows=[g for g in summary['groups'] if g['problem']==problem and g['variant']==name]
            h=[g for g in rows if g['hypothesis'] in ('H1','H2')]
            combined=next((g for g in rows if g['hypothesis']=='design_mixture'),None)
            if len(h)==2 and all(g['complete']==g['n'] and g['mean_difference_s']<=1e-6 for g in h):
                candidates.append((combined['mean_loss_s'],name))
        choice=min(candidates)[1] if candidates else 'champion'
        choices[str(problem)]=choice
        if choice!='champion':
            spec=dict(plan['specs'][choice]); spec['problems']=sorted(set(spec.get('problems',[]))|{problem})
            if choice in validation:spec['problems']=sorted(set(validation[choice]['problems'])|{problem})
            validation[choice]=spec
    selection={'choices':choices,'validation_specs':validation,'source_files':files(),
               'development_stage':str(stage),'development_results_sha256':digest(results),
               'status':'frozen_candidate_not_yet_validated','rule':read(campaign/'protocol.json')['selection']}
    selection['selection_sha256']=digest(selection)
    write(output,selection);write(campaign/'validation_specs.json',validation)
    print(json.dumps(selection['choices']))


def main():
    parser=argparse.ArgumentParser(description=__doc__); commands=parser.add_subparsers(dest='cmd',required=True)
    p=commands.add_parser('prepare-campaign');p.add_argument('--dataset',required=True);p.add_argument('--calibration',required=True)
    p.add_argument('--output',required=True);p.add_argument('--fresh-per-group',type=int,default=60)
    p.add_argument('--seed',type=int,default=202609112101);p.add_argument('--exclude-manifest',action='append',default=[]);p.set_defaults(func=prepare_campaign)
    p=commands.add_parser('stage');p.add_argument('--campaign',required=True);p.add_argument('--partition',choices=['development','confirmation','stress'],default='development')
    p.add_argument('--specs',required=True);p.add_argument('--output',required=True);p.add_argument('--group-limit',type=int,default=0);p.add_argument('--workers',type=int,default=8);p.set_defaults(func=make_stage)
    p=commands.add_parser('execute');p.add_argument('--plan',required=True);p.add_argument('--workers',type=int,default=8);p.set_defaults(func=execute)
    p=commands.add_parser('freeze');p.add_argument('--campaign',required=True);p.add_argument('--stage',required=True);p.set_defaults(func=freeze)
    args=parser.parse_args();args.func(args)
