from pathlib import Path
import json,random,hashlib,statistics,math
import run
from bsolver.round_experiments import make_scenario

ROOT=Path(__file__).resolve().parent
PREV=ROOT.parent/'log_guided_iteration_20260912'
CAL=ROOT.parents[1]/'B_solution/results/official369_mainline_20260912/calibration'

def main():
    baseline=[]
    for row in run.read(PREV/'reports/paired_cases.json'):
        path=(PREV/'reports'/row['requests']).resolve().parent/'result.json'
        r=run.read(path);r['arm']='current';r['reuse_origin']=str(path)
        r['requests_path']=str(path.parent/'requests.jsonl');r['decisions_path']=str(path.parent/'decisions.jsonl')
        baseline.append(r)
    run.write(ROOT/'inputs/deployed_baseline_results.json',baseline)
    official=[]
    for section in run.read(PREV/'runs/official_log_guided_10_each_20260912/report/summary.json')['sections']:
        for row in section['cases']:
            folder=PREV/'runs/official_log_guided_10_each_20260912/cases'/row['case_id']
            d=run.read(folder/'diagnostics.json');r=run.read(folder/'result.json')
            official.append({**row,**d,'move_fraction':d['costs']['move_s']/row['total_virtual_time_s'],
                             'tail_fraction':d['time_after_last_successful_clear_s']/row['total_virtual_time_s']})
    run.write(ROOT/'reports/official_diagnosis.json',official)
    excluded={c['_world_key'] for c in run.load_cases(ROOT/'inputs/new240/cases.json')}
    fresh=[]
    for p in (3,4):
        for hi,mode in enumerate(('correlated','extreme')):
            rng=random.Random(202609125001+p*100+hi)
            ns=list(range(10,17))*8+[10,12,14,16]
            rng.shuffle(ns)
            for i,n in enumerate(ns):
                nd=rng.randrange(n+1) if p==4 else 0
                c=make_scenario(rng.getrandbits(63),p,n,nd,pool='tail_fresh_confirmation',
                    model='official369_supported_broad_joint_stratified',error_mode=mode,index=i,
                    partition='sealed_confirmation',mechanism_status='official_supported_representative_not_identified_truth')
                fresh.append(c)
    run.write(ROOT/'data/fresh_confirmation240.json',fresh)
    new=run.load_cases(ROOT/'data/fresh_confirmation240.json')
    assert len(new)==240 and not excluded.intersection(c['_world_key'] for c in new)
    provenance={'fresh_cases':240,'old_overlap':0,'purpose':'untouched confirmation after development selection',
       'composition':'Each P/H: N10..16 has 8 or9 cases (near uniform); P4 Nd conditional uniform0..N.',
       'generation_model_source':str(CAL/'generation_model.json'),
       'physical_models':{'H1':'correlated150m, primary','H2':'extreme150m, sensitivity'},
       'unidentified':['source position distribution','reception radius distribution','orientation distribution','unique error field'],
       'physical_parameters_updated':False,
       'reason':'Official369 composition family and 320 matched protocol simulations support retained representative. Twenty new task-time observations alone cannot identify physical parameters.',
       'dataset_sha256':hashlib.sha256((ROOT/'data/fresh_confirmation240.json').read_bytes()).hexdigest()}
    run.write(ROOT/'data/provenance.json',provenance)
    for p in (3,4):
        g=[r for r in official if r['problem']==p]
        print(json.dumps({'problem':p,'mean_move_fraction':statistics.mean(r['move_fraction'] for r in g),
              'mean_tail_s':statistics.mean(r['time_after_last_successful_clear_s'] for r in g)},ensure_ascii=False))
    print(json.dumps({'baseline':len(baseline),'fresh_confirmation':len(new)},ensure_ascii=False))

if __name__=='__main__':main()
