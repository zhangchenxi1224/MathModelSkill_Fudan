from pathlib import Path
import json,statistics,collections,math
import run
ROOT=Path(__file__).resolve().parent
PREV=ROOT.parent/'log_guided_iteration_20260912'

def trace_metrics(path):
    rows=[json.loads(s) for s in Path(path).read_text(encoding='utf-8').splitlines() if s.strip()]
    seen=set();last_new=0;first={};positive=collections.Counter();no=0
    for r in rows:
        if r.get('outcome')!='accepted':continue
        res=r.get('response',{});req=r.get('request',{});f=req.get('channel')
        if r.get('path')=='/measure':
            if res.get('measure_result') in ('near','direction'):
                positive[f]+=1
                if f not in seen:
                    seen.add(f);last_new=res['virtual_time_s'];first[f]=last_new
            else:no+=1
    end=rows[-1].get('response',{}).get('virtual_time_s')
    last_clear=max((r['response']['virtual_time_s'] for r in rows if r.get('response',{}).get('clear_result')=='success'),default=0)
    return dict(first_detected_sources=len(seen),last_discovery_s=last_new,after_all_discovered_s=end-last_new,
                after_last_clear_s=end-last_clear,positive_per_source=statistics.mean(positive.values()),
                no_signal_count=no,positive_count=sum(positive.values()))

def main():
    off=run.read(ROOT/'reports/official_diagnosis.json')
    for r in off:
        r.update(trace_metrics(PREV/'runs/official_log_guided_10_each_20260912/cases'/r['case_id']/'requests.jsonl'))
    run.write(ROOT/'reports/official_diagnosis.json',off)
    baseline=run.read(ROOT/'inputs/deployed_baseline_results.json')
    for r in baseline:r.update(trace_metrics(r['requests_path']))
    run.write(ROOT/'reports/local_baseline_behavior.json',baseline)
    groups=[]
    for p in (3,4):
        a=[r for r in off if r['problem']==p]
        for h,mode in [('H1','correlated'),('H2','extreme')]:
            # Compare same policy and N/Nd compositions where locally supported.
            matched=[];official_matched=[]
            for r in a:
                g=[x for x in baseline if x['problem']==p and x['error_mode']==mode and x['source_total']==r['N'] and x['directional_total']==r['Nd']]
                if g:matched.append(g);official_matched.append(r)
            keys=['positive_per_source','no_signal_count','after_all_discovered_s','after_last_clear_s']
            groups.append(dict(problem=p,H=h,official_cases_with_exact_composition_support=len(matched),
                metrics={k:dict(official=statistics.mean(r[k] for r in official_matched),
                  local=statistics.mean(statistics.mean(x[k] for x in g) for g in matched)) for k in keys} if matched else {},
                interpretation='Behavioral comparison of the same deployed policy, matched N/Nd; sparse support and different hidden layouts. Not a physical parameter identification.'))
    run.write(ROOT/'reports/fidelity_behavior_check.json',groups)
    for p in (3,4):
        a=[r for r in off if r['problem']==p]
        print(json.dumps(dict(problem=p,mean_after_discovery_s=statistics.mean(r['after_all_discovered_s'] for r in a),
               mean_positive_per_source=statistics.mean(r['positive_per_source'] for r in a)),ensure_ascii=False))

if __name__=='__main__':main()
