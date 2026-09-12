from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import argparse,time,statistics,json
import run,tail_policy
ROOT=Path(__file__).resolve().parent
run.make_solver=tail_policy.make_solver

def trial(case,arm,out,options):
    folder=Path(out)/'cases'/case['_world_key'][:16]/arm
    if (folder/'result.json').exists():return run.read(folder/'result.json')
    r=run.run_one(case,arm,out,None,options)
    r.update(requests_path=str(folder/'requests.jsonl'),decisions_path=str(folder/'decisions.jsonl'))
    run.write(folder/'result.json',r)
    return r

def summarize(rows,out,baseline_path=None):
    base={r['world_key']:r for r in run.read(baseline_path or ROOT/'inputs/deployed_baseline_results.json')}
    base.update({r['world_key']:r for r in rows if r['arm']=='current'})
    summary=[];groups=[]
    for p,arm,mode in sorted({(r['problem'],r['arm'],r['error_mode']) for r in rows}):
        sub=[r for r in rows if (r['problem'],r['arm'],r['error_mode'])==(p,arm,mode) and r['world_key'] in base]
        if not sub:continue
        parts=[]
        for n in range(10,17):
            g=[r for r in sub if r['source_total']==n]
            if not g:continue
            bt=statistics.mean(base[r['world_key']]['loss_s'] for r in g)
            ct=statistics.mean(r['loss_s'] for r in g)
            dif=[r['loss_s']-base[r['world_key']]['loss_s'] for r in g]
            s=dict(problem=p,arm=arm,H='H1' if mode=='correlated' else 'H2',N=n,cases=len(g),
              complete=sum(r['complete'] for r in g),baseline_T=bt,T=ct,gain_pct=100*(1-ct/bt),
              slower=sum(d>1e-5 for d in dif),max_regression_s=max(dif))
            parts.append(s);groups.append(s)
        bt=statistics.mean(s['baseline_T']/s['N'] for s in parts)
        ct=statistics.mean(s['T']/s['N'] for s in parts)
        low=[s for s in parts if s['N']<16]
        summary.append(dict(problem=p,arm=arm,H=parts[0]['H'],cases=len(sub),complete=sum(r['complete'] for r in sub),
           macro_gain_pct=100*(1-ct/bt),low_N_gain_pct=100*(1-statistics.mean(s['T']/s['N'] for s in low)/statistics.mean(s['baseline_T']/s['N'] for s in low)) if low else None,
           mean_T=statistics.mean(r['loss_s'] for r in sub),slower=sum(s['slower'] for s in parts),
           max_regression_s=max(s['max_regression_s'] for s in parts),
           station_replacements=sum(r.get('replaced_stations',0) for r in sub),
           extra_scans=sum(r.get('substitution_measures',0) for r in sub),mean_wall_s=statistics.mean(r.get('wall_s',0) for r in sub)))
    run.write(Path(out)/'results.json',rows);run.write(Path(out)/'summary.json',summary);run.write(Path(out)/'by_N.json',groups)
    return summary

def main():
    p=argparse.ArgumentParser();p.add_argument('--dataset',default='inputs/new240/cases.json');p.add_argument('--arms',required=True)
    p.add_argument('--output',required=True);p.add_argument('--per-n',type=int,default=0);p.add_argument('--workers',type=int,default=6)
    p.add_argument('--problem',type=int);p.add_argument('--options',default='{}');args=p.parse_args()
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    cases=run.load_cases(args.dataset);chosen=[];counts={}
    for c in sorted(cases,key=lambda c:c['_world_key']):
        if args.problem and c['problem']!=args.problem:continue
        key=(c['problem'],c['error_field']['mode'],len(c['sources']))
        if args.per_n and counts.get(key,0)>=args.per_n:continue
        counts[key]=counts.get(key,0)+1;chosen.append(c)
    options=json.loads(args.options);arms=args.arms.split(',')
    plan=dict(dataset=str(Path(args.dataset).resolve()),cases=chosen,arms=arms,options=options)
    if (out/'plan.json').exists() and run.read(out/'plan.json')!=plan:raise ValueError('different existing plan')
    run.write(out/'plan.json',plan);rows=[];start=time.monotonic()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        tasks=[pool.submit(trial,c,a,str(out),options) for c in chosen for a in arms]
        for f in as_completed(tasks):
            r=f.result();rows.append(r)
            if len(rows)%8==0 or len(rows)==len(tasks):
                summarize(rows,out)
                print(json.dumps(dict(done=len(rows),total=len(tasks),complete=sum(x['complete'] for x in rows),
                      elapsed_s=round(time.monotonic()-start),last={k:r.get(k) for k in ('problem','arm','source_total','total_virtual_time_s','error','replaced_stations')}),ensure_ascii=False),flush=True)
    print(json.dumps(summarize(rows,out),ensure_ascii=False),flush=True)

if __name__=='__main__':main()
