"""Run paired three-feedback Q2 experiments without official API calls."""
from __future__ import annotations
from pathlib import Path
import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import math
import statistics
import time

from model import FirstContext
from evaluator import evaluate
from geometry import enclosing_circle,dist
from search import optimize,compact

ROOT=Path(__file__).resolve().parent


def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path,value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def contexts(official_count=0):
    cases=[{'id':'center','station':[0.,0.],'report_deg':0.},
           {'id':'edge','station':[1600.,0.],'report_deg':180.},
           {'id':'outside','station':[2200.,0.],'report_deg':180.}]
    seen=set()
    for c in read(ROOT/'inputs/official_first_contexts.json')['contexts']:
        if len(seen)>=official_count: break
        if c['case_id'] in seen: continue
        seen.add(c['case_id'])
        cases.append({'id':c['context_id'],'station':c['first']['position'],
                      'report_deg':c['first']['report_deg'],'official_case_id':c['case_id']})
    return cases


def baselines(context):
    found=None
    for r in read(ROOT/'inputs/baseline_synthetic_selections.json').values():
        if dist(r['first_position'],context.station)<1e-7 and abs(r['first_report_deg']-context.report_deg)<1e-7:
            found=r['selections']; break
    if found is None:
        for r in read(ROOT/'inputs/baseline_official_contexts.json'):
            if dist(r['first']['position'],context.station)<1e-7 and abs(r['first']['report_deg']-context.report_deg)<1e-7:
                found=r['selection']; break
    if found is None: raise ValueError('matching frozen baseline not found')
    return {mode:context.to_local(row['point']) for mode,row in found.items()}


def run_case(task):
    case,budget,output=task
    context=FirstContext(tuple(case['station']),case['report_deg'])
    old=baselines(context)
    searches=optimize(context,old,budget=budget)
    points={'old_fixed':old['fixed'],'old_precision':old['precision'],
            'open_seed':(850.,600.),'open_search':tuple(searches['best']['point_local'])}
    comparisons={name:evaluate(context,q,tolerance=.2,max_splits=450) for name,q in points.items()}
    result={'context':case,'comparisons':comparisons,'search':searches,
            'selection_used_truth':False,'selection_used_future_observation':False}
    write(Path(output)/'cases'/f"{case['id']}.json",result)
    return result


def synthetic_feedback(results):
    """Paired physical boundary cases; policies are frozen before truth is used."""
    rows=[]
    for result in results:
        item=result['context']
        if item['id'] not in ('center','edge','outside'): continue
        context=FirstContext(tuple(item['station']),item['report_deg'])
        ranges=(100.,300.,600.,1000.,1250.,1499.99) if item['id']!='outside' else (600.,1000.,1250.,1499.99)
        for radius in ranges:
            for angle in (-.9999,0.,.9999):
                a=math.radians(angle); source=(radius*math.cos(a),radius*math.sin(a))
                if not context.prior().actual_witness(source): continue
                for reception in sorted({max(1000.,radius),1500.}):
                    for error in (-1.,-.5,0.,.5,1.):
                        world=f"{item['id']}-r{radius}-a{angle}-R{reception}-e{error}"
                        for mode,choice in result['comparisons'].items():
                            q=tuple(choice['point_local'])
                            response=context.response(source,reception,q,error)
                            posterior=context.posterior(q,response['kind'],response.get('report_deg'))
                            # Boundary outcomes are allowed; tolerance here is
                            # only a diagnostic, never an input to policy choice.
                            assert posterior.contains(source,True,-1e-6),(world,mode,response)
                            circle=enclosing_circle(posterior,tolerance=.002,need_lower=False)
                            assert not circle['empty_relaxation']
                            assert dist(source,circle['center'])<=circle['upper_m']+1e-5
                            assert circle['upper_m']<=choice['upper_m']+.01
                            rows.append({'world_id':world,'context_id':item['id'],'mode':mode,
                                'source_local':source,'reception_radius_m':reception,'feedback':response,
                                'point_local':q,'radius_upper_m':circle['upper_m'],
                                'estimate_global':context.to_global(circle['center']),
                                'actual_center_error_m':dist(source,circle['center']),
                                'truth_covered':True})
    return rows


def summarize(results,rows):
    out={'contexts':len(results),'worlds':len({r['world_id'] for r in rows}),'paired_rows':len(rows),
         'new_official_calls':0,'official_results_scope':'public first-observation model bounds only',
         'primary_metric':'worst legal feedback MEC radius interval, same evaluator for every method',
         'global_optimality_claim':False,'context_comparison':[],'synthetic_secondary':{}}
    for result in results:
        out['context_comparison'].append({'context_id':result['context']['id'],
            'modes':{mode:compact(value) for mode,value in result['comparisons'].items()},
            'search_evaluations':result['search']['coarse_evaluations']})
    for mode in sorted({r['mode'] for r in rows}):
        rs=[r for r in rows if r['mode']==mode]
        out['synthetic_secondary'][mode]={'n':len(rs),'mean_radius_upper_m':statistics.mean(r['radius_upper_m'] for r in rs),
            'sample_max_radius_upper_m':max(r['radius_upper_m'] for r in rs),
            'no_signal_count':sum(r['feedback']['kind']=='no_signal' for r in rs),
            'all_truths_covered':all(r['truth_covered'] for r in rs)}
    return out


def source_hashes():
    paths=sorted(ROOT.glob('*.py'))+sorted((ROOT/'vendor').glob('*.py'))+sorted((ROOT/'inputs').glob('*.json'))
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--budget',type=int,default=80)
    p.add_argument('--official-cases',type=int,default=2)
    p.add_argument('--workers',type=int,default=2)
    p.add_argument('--canonical-only',action='store_true')
    args=p.parse_args()
    if args.output.exists() and any(args.output.iterdir()): p.error('use a fresh output directory')
    if args.budget<20 or args.workers<1: p.error('budget>=20 and workers>=1 required')
    cases=contexts(args.official_cases)
    if args.canonical_only: cases=cases[:1]
    hashes=source_hashes(); started=time.monotonic()
    write(args.output/'manifest.json',{'source_files':hashes,'settings':vars(args)|{'output':str(args.output)},
         'contexts':cases,'report_error_deg':1.,'report_grid_deg':.01})
    results=[]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        tasks=[(c,args.budget,str(args.output)) for c in cases]
        for result in pool.map(run_case,tasks):
            results.append(result)
            print(json.dumps({'done':result['context']['id'],'bounds':{k:[v['lower_m'],v['upper_m']] for k,v in result['comparisons'].items()}}),flush=True)
    rows=synthetic_feedback(results)
    write(args.output/'feedback_rows.json',rows)
    write(args.output/'summary.json',summarize(results,rows))
    if hashes!=source_hashes(): raise RuntimeError('source changed during experiment')
    write(args.output/'completion.json',{'status':'complete','elapsed_s':time.monotonic()-started})
    print(json.dumps({'status':'complete','elapsed_s':time.monotonic()-started,'output':str(args.output)}),flush=True)


if __name__=='__main__': main()
