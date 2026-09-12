from pathlib import Path
import argparse,json,hashlib,datetime
import run
from metrics import paired
ROOT=Path(__file__).resolve().parent

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--problem',type=int,required=True);args=ap.parse_args();p=args.problem
    target=ROOT/f'frozen_p{p}.json'
    if target.exists():raise ValueError('Candidate selection already frozen')
    baseline={r['world_key']:r for r in run.read(ROOT/'inputs/deployed_baseline_results.json')}
    paths=['full_p3'] if p==3 else ['full_ring_cover','full_ring_search']
    candidates=[]
    for name in paths:
        rows=run.read(ROOT/'runs'/name/'results.json')
        if len(rows)!=120:raise ValueError(f'{name} has not completed120 cases')
        arm=rows[0]['arm']
        h1=paired(rows,baseline,p,'correlated');h2=paired(rows,baseline,p,'extreme')
        candidates.append(dict(arm=arm,path=f'runs/{name}',H1=h1,H2=h2))
    eligible=[c for c in candidates if all(c[h]['complete']==60 for h in ['H1','H2']) and c['H1']['macro_gain_pct']>0 and c['H2']['macro_gain_pct']>=0]
    best=max(eligible,key=lambda c:c['H1']['macro_gain_pct']) if eligible else None
    decision=dict(problem=p,candidate=best['arm'] if best else 'current',development_path=best['path'] if best else None,
                  candidates=candidates,rule='Full clear, H1 macro improvement, H2 non-regression; best H1 among eligible. Fresh confirmation must independently support H1 gain.',
                  frozen_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  fresh_dataset_sha256=run.read(ROOT/'data/provenance.json')['dataset_sha256'],
                  policy_sha256=hashlib.sha256((ROOT/'tail_policy.py').read_bytes()).hexdigest())
    run.write(target,decision)
    print(json.dumps(decision,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
