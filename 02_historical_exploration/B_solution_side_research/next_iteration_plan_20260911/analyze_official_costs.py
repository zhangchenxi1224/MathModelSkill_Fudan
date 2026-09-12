"""Read-only diagnosis of completed official logs; never contacts the server."""
from pathlib import Path
from collections import Counter, defaultdict
import json, statistics, math

BASE = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent
ROOTS = {
    'selected_10_each': BASE/'selected_dataset_iteration_20260911/runs/official_practice_10_each/cases',
    'synthesis_5_each': BASE/'synthesis_iteration_20260911/runs/official_practice_5_each/cases',
    'refined_25_L1_20': BASE.parent/'B_solution/results/p4_refined_official/cases',
}
def read(p): return json.loads(p.read_text(encoding='utf-8-sig'))
def lines(p): return [json.loads(x) for x in p.read_text(encoding='utf-8-sig').splitlines() if x.strip()]
def mean(xs): return statistics.mean(xs) if xs else None
def delta(r): return r['state_after']['virtual_time_s']-r['state_before']['virtual_time_s']
def dist(p,q): return math.hypot(p[0]-q[0],p[1]-q[1])
rows=[]
for scheme,root in ROOTS.items():
    allowed=set(read(root.parent/'stop_decision.json')['executed_case_ids']) if scheme=='refined_25_L1_20' else None
    for p in sorted(root.iterdir()):
        if not p.is_dir(): continue
        if allowed is not None and p.name not in allowed: continue
        r=read(p/'result.json'); a=read(p/'post_exit_audit.json'); ds=lines(p/'decisions.jsonl')
        rs=[x for x in lines(p/'requests.jsonl') if x.get('outcome')=='accepted']
        successes=[x for x in rs if x.get('response',{}).get('clear_result')=='success']
        last=successes[-1]['state_after']['virtual_time_s']
        tail=[x for x in rs if x['state_before']['virtual_time_s']>=last-1e-6 and x not in successes]
        failed=[x for x in rs if x.get('path')=='/clear' and x.get('response',{}).get('clear_result')!='success']
        m=[x for x in ds if x['event']=='measure']
        local=[x for x in m if x.get('reason')!='global_coverage']
        known=set(); found16=None; cleared=set(); opportunity=0; ns=[]; channels_local=defaultdict(list)
        for x in ds:
            if x['event']=='measure':
                if x.get('response',{}).get('measure_result')=='direction': known.add(x['channel'])
                if len(known)==16 and found16 is None: found16=x['virtual_time_s']
                if x.get('reason')!='global_coverage':
                    channels_local[x['channel']].append(x)
                    if known-cleared-{x['channel']}: opportunity+=1
            elif x['event']=='clear' and x.get('response',{}).get('clear_result')=='success': cleared.add(x['channel'])
        # The first/second local measurements are paired within a channel, never across cases.
        ns_first=0; ns_second=0; ns_second_positive=0; second_cost=0.; ns_after_fail_cost=0.; ns_after_fail_count=0
        two_nosignal=[]
        second_all=0; second_all_positive=0; second_all_cost=0.
        for ch,items in channels_local.items():
            if len(items)>=2:
                second_all+=1
                second_all_positive+=items[1]['response']['measure_result']=='direction'
                second_all_cost+=items[1]['virtual_time_s']-items[0]['virtual_time_s']
            if items and items[0]['response']['measure_result']=='no_signal':
                ns_first+=1
                if len(items)>=2:
                    ns_second+=1
                    ns_second_positive+=items[1]['response']['measure_result']=='direction'
                    second_cost+=items[1]['virtual_time_s']-items[0]['virtual_time_s']
                f=[x for x in failed if x['request'].get('channel')==ch and x['state_before']['virtual_time_s']>=items[0]['virtual_time_s']-1e-6]
                ns_after_fail_cost+=sum(delta(x) for x in f); ns_after_fail_count+=len(f)
                if len(items)>=2 and items[1]['response']['measure_result']=='no_signal':
                    two_nosignal.append({'channel':ch,'failed_clear_count':len(f),'failed_clear_allocated_move_and_operation_s':sum(delta(x) for x in f)})
        service=[]
        for i,x in enumerate(ds):
            if x['event']=='cheap_joint_service_selection':
                finish=next((y for y in ds[i+1:] if y['event']=='clear' and y['channel']==x['target_channel'] and y['response']['clear_result']=='success'),None)
                if finish: service.append({'channel':x['target_channel'],'proxy_s':x['estimated_incremental_cost_s'],'actual_until_clear_s':finish['virtual_time_s']-x['virtual_time_s']})
        # A retrospective geometric hint only: distinct target-channel local measurement
        # points within 100 m and 500 virtual seconds, not a counterfactual saving.
        pairs=[]
        for i,x in enumerate(local):
            for y in local[i+1:]:
                if y['virtual_time_s']-x['virtual_time_s']>500: break
                if x['channel']!=y['channel'] and dist(x['position'],y['position'])<=100:
                    pairs.append((x['sequence'],y['sequence']))
        n=a['source_total_post_exit']; nd=a['directional_total_post_exit']
        row=dict(scheme=scheme,case_id=p.name,problem=r['problem'],case_code=a['case_code'],N=n,Nd=nd,
          complete=r['status']=='complete' and a['cleared']==n,T=r['total_virtual_time_s'],T_per_N=r['total_virtual_time_s']/n,
          walk_m=r['walk_distance_m'],move_s=r['walk_distance_m']/5,measures=r['measures'],measure_s=5*r['measures'],
          switches=r['switches'],switch_s=r['switches'],clear_successes=r['clear_successes'],clear_success_s=5*r['clear_successes'],
          clear_failures=r['clear_attempts']-r['clear_successes'],clear_failure_operation_s=3*(r['clear_attempts']-r['clear_successes']),
          failed_clear_allocated_move_and_operation_s=sum(delta(x) for x in failed),
          tail_after_last_clear_s=r['total_virtual_time_s']-last,tail_measure_count=sum(x['path']=='/measure' for x in tail),
          tail_measure_no_signal=sum(x.get('response',{}).get('measure_result')=='no_signal' for x in tail),
          tail_move_m=sum(dist(list(x['state_before']['position'].values()),list(x['state_after']['position'].values())) for x in tail),
          discovered_channels=len(known),found16_time_s=found16,
          global_measures_after_found16=sum(x.get('reason')=='global_coverage' and x['virtual_time_s']>found16+1e-6 for x in m) if found16 is not None else None,
          time_remaining_after_found16_s=r['total_virtual_time_s']-found16 if found16 is not None else None,
          confirmed16_transitions=r.get('confirmed16_transitions',0),
          local_measurements=len(local),local_no_signal=sum(x['response']['measure_result']=='no_signal' for x in local),
          local_with_other_known_unresolved_channels=opportunity,near_cross_channel_local_pairs_100m_500s=len(pairs),
          first_local_no_signal=ns_first,second_local_after_no_signal=ns_second,
          second_local_positive_after_no_signal=ns_second_positive,second_local_after_no_signal_time_s=second_cost,
          failed_clear_after_first_local_no_signal=ns_after_fail_count,failed_clear_allocated_cost_after_first_local_no_signal_s=ns_after_fail_cost,
          second_local_count=second_all,second_local_positive_count=second_all_positive,second_local_time_s=second_all_cost,
          program_real_time_s=r['program_real_time_s'],stop_type=r['stop_evidence']['type'],
          consecutive_two_local_no_signal=two_nosignal,
          service_count=len(service),service_time_s=sum(x['actual_until_clear_s'] for x in service),
          low_proxy_service_count=sum(x['proxy_s']<=10 for x in service),
          low_proxy_actual_over100s_count=sum(x['proxy_s']<=10 and x['actual_until_clear_s']>=100 for x in service),
          low_proxy_actual_over100s_service_time_s=sum(x['actual_until_clear_s'] for x in service if x['proxy_s']<=10 and x['actual_until_clear_s']>=100))
        row['accounting_residual_s']=row['T']-sum(row[k] for k in ['move_s','measure_s','switch_s','clear_success_s','clear_failure_operation_s'])
        rows.append(row)
groups=[]
for scheme in ROOTS:
    for problem in (3,4):
        rr=[r for r in rows if r['scheme']==scheme and r['problem']==problem]
        if not rr: continue
        g={'scheme':scheme,'problem':problem,'n':len(rr),'complete':sum(r['complete'] for r in rr)}
        for k in rr[0]:
            if any(isinstance(r[k],(int,float)) for r in rr) and k not in ['problem','complete']:
                vals=[r[k] for r in rr if r[k] is not None]
                g['mean_'+k]=mean(vals); g['sum_'+k]=sum(vals)
        g['N16_cases']=[r['case_id'] for r in rr if r['N']==16]
        g['found16_cases']=[r['case_id'] for r in rr if r['found16_time_s'] is not None]
        g['move_share']=sum(r['move_s'] for r in rr)/sum(r['T'] for r in rr)
        g['tail_share']=sum(r['tail_after_last_clear_s'] for r in rr)/sum(r['T'] for r in rr)
        g['tail_max_s']=max(r['tail_after_last_clear_s'] for r in rr)
        groups.append(g)
data={'note':'50 different official cases: selected 20, synthesis 10, refined intermediate 20 explicitly listed in stop_decision.json. Descriptive nonpaired comparison. All case rows retained. Post-final-clear tail is an oracle diagnosis, never a policy stop rule. Move costs allocated to failed clears are not entirely avoidable waste.','groups':groups,'cases':rows}
(OUT/'official_costs.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(groups,ensure_ascii=False,indent=2))
