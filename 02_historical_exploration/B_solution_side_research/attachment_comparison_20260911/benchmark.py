"""Matched local scenarios only. No HTTP or official test dispatch entrypoint."""
import os
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import random
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parent
DATASET=ROOT/'dataset_input/official_composition_H1_H2_360_20260911'
sys.path.insert(0,str(DATASET))
sys.path.insert(0,str(ROOT/'vendor'))
from dataset import list_cases,make_local_session,load_scenario
from policies import make_policy,SPECS
from bsolver.geometry import contains


def write_json(path,obj):
    Path(path).write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def run_one(args):
    row,name,out=args
    started=time.monotonic()
    client,evaluator=make_local_session(row['case_id'])
    policy=make_policy(client,row['problem'],name)
    result=policy.run()
    evaluation=evaluator.summary()
    scenario=load_scenario(row['case_id'])
    truth={s['channel']:tuple(s['position']) for s in scenario['sources']}
    violations=[];checks=0
    for event in policy.decisions:
        k=event.get('knowledge')
        if k and k.get('channel') in truth and k.get('hull'):
            checks+=1
            if not contains(k['hull'],truth[k['channel']],tol=1e-6):
                violations.append({'event':event['sequence'],'channel':k['channel']})
    stats=policy.stats
    t=evaluation['virtual_time_s']
    accounted=stats['walk_distance_m']/5+stats['switches']+5*stats['measures']+3*stats['clear_attempts']+2*stats['clear_successes']
    n=evaluation['source_count']
    record={'case_id':row['case_id'],'hypothesis':row['hypothesis'],'problem':row['problem'],
            'variant':name,'scenario_sha256':scenario['scenario_sha256'],'N':n,
            'Nd':sum(s.get('direction_deg') is not None for s in scenario['sources']),
            'complete':evaluation['all_cleared'] and result['status']=='complete' and not violations,
            'status':result['status'],'error':result.get('error'),'T_s':t,'T_per_source_s':t/n,
            'cleared':evaluation['cleared_count'],'walk_m':stats['walk_distance_m'],
            'measures':stats['measures'],'switches':stats['switches'],
            'clear_attempts':stats['clear_attempts'],'failed_clears':stats['clear_attempts']-stats['clear_successes'],
            'program_s':result['program_real_time_s'],'wall_s':time.monotonic()-started,
            'timing_residual_s':t-accounted,'hull_checks':checks,'hull_violations':len(violations),
            'stats':stats,'stop_evidence':result.get('stop_evidence')}
    dest=Path(out)/'cases'/row['case_id']/name;dest.mkdir(parents=True)
    write_json(dest/'result.json',record)
    with gzip.open(dest/'decisions.jsonl.gz','wt',encoding='utf-8') as f:
        for event in policy.decisions:f.write(json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n')
    with gzip.open(dest/'requests.jsonl.gz','wt',encoding='utf-8') as f:
        for event in evaluator.public_trace():f.write(json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n')
    client.close_log()
    return record


def quantile(vals,p):
    vals=sorted(vals);i=(len(vals)-1)*p;j=int(i)
    return vals[j]+(vals[min(j+1,len(vals)-1)]-vals[j])*(i-j)


def summarize(records):
    groups=defaultdict(list)
    for r in records:groups[(r['problem'],r['hypothesis'],r['variant'])].append(r)
    out=[]
    for (p,h,v),rs in sorted(groups.items()):
        times=[r['T_s'] for r in rs if r['complete']]
        item={'problem':p,'hypothesis':h,'variant':v,'n':len(rs),'complete':sum(r['complete'] for r in rs),
              'mean_T_s':statistics.mean(times) if times else None,
              'p90_T_s':quantile(times,.9) if times else None,'max_T_s':max(times) if times else None}
        for key in ['T_per_source_s','walk_m','measures','switches','clear_attempts','failed_clears','program_s']:
            item['mean_'+key]=statistics.mean(r[key] for r in rs)
        item['mean_scope']='completed times; failures explicitly counted and excluded from winner eligibility'
        out.append(item)
    return out


def paired(records,reference):
    lookup={(r['case_id'],r['variant']):r for r in records};groups=defaultdict(list)
    for r in records:
        b=lookup.get((r['case_id'],reference))
        if b and r['variant']!=reference:groups[(r['problem'],r['hypothesis'],r['variant'])].append((b,r))
    out=[]
    for (p,h,v),pairs in sorted(groups.items()):
        valid=[(b,r) for b,r in pairs if b['complete'] and r['complete']]
        ds=[r['T_s']-b['T_s'] for b,r in valid]
        item={'problem':p,'hypothesis':h,'variant':v,'reference':reference,'n':len(pairs),
              'valid_pairs':len(ds),'candidate_failures':sum(not r['complete'] for b,r in pairs),
              'reference_failures':sum(not b['complete'] for b,r in pairs)}
        if ds:
            rng=random.Random(20260911+p);means=[]
            for _ in range(2000):means.append(sum(rng.choice(ds) for _ in ds)/len(ds))
            item.update(mean_delta_s=statistics.mean(ds),median_delta_s=statistics.median(ds),
                        ci95_s=[quantile(means,.025),quantile(means,.975)],
                        regressions=sum(d>1e-6 for d in ds),worst_regression_s=max(ds),
                        relative_mean_gain=-statistics.mean(ds)/statistics.mean(b['T_s'] for b,r in valid))
            item['worst_cases']=[{'case_id':r['case_id'],'delta_s':r['T_s']-b['T_s'],'N':r['N'],'Nd':r['Nd']}
                                 for b,r in sorted(valid,key=lambda pair:pair[1]['T_s']-pair[0]['T_s'],reverse=True)[:5]]
        out.append(item)
    return out


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True)
    ap.add_argument('--variants',nargs='+',default=['main','side','attachment','p4_transfer'])
    ap.add_argument('--per-group',type=int,default=0);ap.add_argument('--workers',type=int,default=6)
    ap.add_argument('--problem',type=int,choices=[3,4]);args=ap.parse_args()
    out=ROOT/args.output
    if out.exists():raise SystemExit('output already exists')
    out.mkdir(parents=True)
    rows=list_cases(problem=args.problem)
    if args.per_group:
        groups=defaultdict(list)
        for r in rows:groups[(r['problem'],r['hypothesis'])].append(r)
        rows=[]
        for key,rs in sorted(groups.items()):
            rows+=sorted(rs,key=lambda r:hashlib.sha256(('screen-v1:'+r['case_id']).encode()).hexdigest())[:args.per_group]
    jobs=[(r,v,str(out)) for r in rows for v in args.variants if r['problem'] in SPECS[v]['problems']]
    write_json(out/'plan.json',{'case_ids':[r['case_id'] for r in rows], 'jobs':len(jobs),
                'specs':{v:SPECS[v] for v in args.variants},'role':'development_regression',
                'policy_sha256':hashlib.sha256((ROOT/'policies.py').read_bytes()).hexdigest()})
    (out/'policies_snapshot.py').write_bytes((ROOT/'policies.py').read_bytes())
    records=[];start=time.monotonic()
    print(json.dumps({'jobs':len(jobs),'workers':args.workers,'output':str(out)}),flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        fs={pool.submit(run_one,j):j for j in jobs}
        for f in as_completed(fs):
            try:r=f.result()
            except Exception as e:
                row,v,_=fs[f]
                print(json.dumps({'error':str(e),'case_id':row['case_id'],'variant':v}),flush=True)
                raise
            records.append(r)
            with (out/'results.jsonl').open('a',encoding='utf-8') as fp:fp.write(json.dumps(r,ensure_ascii=False)+'\n')
            if len(records)%12==0 or not r['complete']:
                print(json.dumps({'done':len(records),'total':len(jobs),'failures':sum(not x['complete'] for x in records),
                      'elapsed_s':round(time.monotonic()-start,1),'latest_variant':r['variant'],'latest_T_s':round(r['T_s'],2)}),flush=True)
    records.sort(key=lambda r:(r['problem'],r['hypothesis'],r['case_id'],r['variant']))
    write_json(out/'results.json',records);write_json(out/'summary.json',summarize(records))
    write_json(out/'paired_main.json',paired(records,'main'));write_json(out/'paired_side.json',paired(records,'side'))
    print(json.dumps({'complete':len(records),'failures':sum(not r['complete'] for r in records),'elapsed_s':time.monotonic()-start}),flush=True)


if __name__=='__main__':main()
