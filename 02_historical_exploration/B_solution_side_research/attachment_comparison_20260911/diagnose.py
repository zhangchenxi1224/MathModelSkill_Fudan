"""Describe observed cost changes; no counterfactual oracle in any policy."""
from pathlib import Path
import collections
import gzip
import json
import math
import statistics
from benchmark import ROOT,write_json


def describe(case_id,variant):
    path=ROOT/'runs/full360/cases'/case_id/variant/'decisions.jsonl.gz'
    phases=collections.defaultdict(lambda:collections.defaultdict(float))
    targets=collections.defaultdict(lambda:collections.defaultdict(float))
    position=(0.,0.);channel=1
    with gzip.open(path,'rt',encoding='utf-8') as f:
        for line in f:
            e=json.loads(line)
            if e['event'] not in ('measure','clear'):continue
            target=e['knowledge']['channel'];q=e['position']
            move=math.dist(position,q)/5;position=q
            if e['event']=='measure':
                reason=e['reason'];d=phases[reason]
                d['movement_s']+=move;d['measurement_s']+=5;d['switch_s']+=int(channel!=target)
                channel=target;outcome=e['response']['measure_result'];d[outcome]+=1
                if reason!='global_coverage':
                    targets[target]['local_movement_s']+=move
                    targets[target]['measurements']+=1
                    targets[target][outcome]+=1
            else:
                success=e['response']['clear_result']=='success'
                d=phases['clear'];d['movement_s']+=move;d['clear_s']+=5 if success else 3
                targets[target]['local_movement_s']+=move;targets[target]['clear_attempts']+=1
                targets[target]['failed_clears']+=int(not success)
    return {'phases':dict(phases),'targets':dict(targets)}


def main():
    report=ROOT/'report'
    result=json.loads((report/'comparison.json').read_text(encoding='utf-8'))
    worst=[r for r in result['worst_cases'] if r['problem']==4 and r['reference']=='main'][:2]
    cases=[]
    for row in worst:
        cases.append({**row,'baseline_trace_costs':describe(row['case_id'],'main'),
                      'candidate_trace_costs':describe(row['case_id'],'p4_route_rescue')})
    write_json(report/'diagnostics.json',cases)
    for case in cases:
        a=case['baseline_trace_costs'];b=case['candidate_trace_costs']
        print(case['case_id'],'delta',case['delta_T_s'])
        print('phases',a['phases'],b['phases'])
        changes=[]
        for k in set(a['targets'])|set(b['targets']):
            x=a['targets'].get(k,{});y=b['targets'].get(k,{})
            changes.append((y.get('local_movement_s',0)-x.get('local_movement_s',0),k,x,y))
        print('largest target movement changes',sorted(changes,reverse=True)[:3])


if __name__=='__main__':main()
