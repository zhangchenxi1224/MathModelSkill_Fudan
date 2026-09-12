"""Apply the preregistered adoption gate; never choose a new arm from holdout."""
from pathlib import Path
import argparse
import hashlib
import random
import statistics
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'vendor'))
from experiment import read,write,digest,quantile,files

ROOT=Path(__file__).resolve().parent


def paired(rows,problem,variant):
    selected=[r for r in rows if r['problem']==problem and r['variant']==variant]
    lookup={r['case_id']:r for r in rows if r['problem']==problem and r['variant']=='champion'}
    if not selected:return None
    if len(selected)!=len(lookup):raise ValueError('missing paired cases')
    pairs=[(lookup[r['case_id']],r) for r in selected]
    differences=[r['loss_s']-b['loss_s'] for b,r in pairs]
    return dict(n=len(pairs),all_complete=all(r['audit_complete'] and b['audit_complete'] for b,r in pairs),
                base_mean_s=statistics.mean(b['loss_s'] for b,r in pairs),
                candidate_mean_s=statistics.mean(r['loss_s'] for b,r in pairs),
                mean_difference_s=statistics.mean(differences),
                ratio_improvement_percent=100*(1-statistics.mean(r['loss_s'] for b,r in pairs)/statistics.mean(b['loss_s'] for b,r in pairs)),
                regressions=sum(x>1e-5 for x in differences),worst_regression_s=max(differences),
                base_p95_s=quantile([b['loss_s'] for b,r in pairs],.95),
                candidate_p95_s=quantile([r['loss_s'] for b,r in pairs],.95),
                base_T_per_N_s=statistics.mean(b['loss_s']/b['source_total'] for b,r in pairs),
                candidate_T_per_N_s=statistics.mean(r['loss_s']/r['source_total'] for b,r in pairs),
                max_candidate_wall_s=max(r['program_real_time_s'] for b,r in pairs),
                mean_candidate_wall_s=statistics.mean(r['program_real_time_s'] for b,r in pairs),
                h_differences={h:[r['loss_s']-b['loss_s'] for b,r in pairs if r['hypothesis']==h]
                               for h in sorted({r['hypothesis'] for b,r in pairs})},
                cost_differences={key:statistics.mean(r[key]-b[key] for b,r in pairs)
                                  for key in ['walk_distance_m','measures','switches','clear_attempts','clear_successes']},
                mechanism_totals={key:sum(r.get(key,0) for b,r in pairs) for key in ['confirmed16_transitions','shared_current_measures','cost_gate_skips','macro_lookahead_selections']})


def interval(groups,registration,problem):
    rng=random.Random(registration['seed']+problem)
    if set(groups)!={'H1','H2'} or not all(groups.values()):raise ValueError('missing registered H strata')
    means=[sum(statistics.mean(rng.choices(values,k=len(values))) for values in groups.values())/2
           for _ in range(registration['resamples'])]
    tail=(1-registration['confidence_level'])/2
    return [quantile(means,tail),quantile(means,1-tail)]


def finalize():
    registration=read(ROOT/'campaign/validation_acceptance.json')
    selection=read(ROOT/'campaign/frozen_selection.json')
    if selection['source_files']!=files():raise ValueError('frozen source drift')
    if selection['selection_sha256']!=digest({k:v for k,v in selection.items() if k!='selection_sha256'}):raise ValueError('selection changed')
    audit=read(ROOT/'reports/audit_final.json')
    if not audit['passed']:raise ValueError('independent audit has issues')
    stages={stage:read(ROOT/f'runs/{stage}/results.json') for stage in ['full','confirmation','stress']}
    conclusions={};adopted={}
    for problem in (3,4):
        name=selection['choices'][str(problem)]
        info={stage:paired(rows,problem,name) for stage,rows in stages.items()}
        hold=info['confirmation'];ci=interval(hold['h_differences'],registration,problem)
        passed=(name!='champion' and hold['all_complete'] and info['stress']['all_complete'] and
                all(statistics.mean(v)<=0 for v in hold['h_differences'].values()) and ci[1]<0)
        adopted[str(problem)]=name if passed else 'champion'
        conclusions[str(problem)]=dict(frozen_candidate=name,adopted=adopted[str(problem)],adoption_passed=passed,
                                      confirmation_ci975_s=ci,**info)
    report=dict(conclusions=conclusions,selection_sha256=selection['selection_sha256'],
                acceptance_sha256=digest(registration),audit_sha256=digest(audit),official_actions=0,
                scope='Empirical best among this campaign; supplied360 are development; fresh160 plus stress48 are synthetic.')
    write(ROOT/'reports/final_metrics.json',report)
    deployment=dict(selection)
    deployment.pop('selection_sha256')
    deployment.update(choices=adopted,status='validated_deployment_with_champion_fallback',
                      original_frozen_selection_sha256=selection['selection_sha256'],validation_report_sha256=digest(report))
    deployment['selection_sha256']=digest(deployment)
    destination=ROOT/'campaign/deployment_selection.json'
    if destination.exists():raise ValueError('deployment already exists')
    write(destination,deployment)
    lines=['# 综合迭代结果','',
           '本轮在用户提供的360个固定世界上开发，先后筛选机制和邻域，再冻结每题一个候选，在160个新合成世界和48个压力世界上验证。独立轨迹复核通过；不代表新增官方成绩或全局最优证明。','',
           '主要基线：Q3为上一轮p3_hull_r1150；Q4为经过先前独立合成确认的25站+RefinedLocal L1组合。','',
           '|问题|冻结候选|最终采用|开发均值：冠军→候选/s|新确认均值：冠军→候选/s|确认降幅|确认差97.5%区间/s|确认退步局数|',
           '|---|---|---|---:|---:|---:|---|---:|']
    for problem,d in conclusions.items():
        a,b=d['full'],d['confirmation'];ci=d['confirmation_ci975_s']
        lines.append(f"|{problem}|{d['frozen_candidate']}|{d['adopted']}|{a['base_mean_s']:.2f}→{a['candidate_mean_s']:.2f}|{b['base_mean_s']:.2f}→{b['candidate_mean_s']:.2f}|{b['ratio_improvement_percent']:.2f}%|[{ci[0]:.2f},{ci[1]:.2f}]|{b['regressions']}/{b['n']}|")
    lines+=['','## 执行与审计','',f"独立复核 {audit['counts']['runs']} 次运行、{audit['counts']['requests']} 条请求、{audit['counts']['hulls']} 次包络；问题数0。详见 reports/audit_final.json。",'',
            '报告保留压力集、P95、最坏退步、T/N、各项费用和机制触发数，完整数据见 reports/final_metrics.json。配置以 campaign/deployment_selection.json 为最终部署选择；campaign/frozen_selection.json 保留确认前冻结选择。']
    for problem,d in conclusions.items():
        b=d['confirmation'];s=d['stress']
        lines += ['',f'## 问题{problem}的范围与风险','',
                  f"确认H1/H2平均差分别为 {statistics.mean(b['h_differences']['H1']):.2f} / {statistics.mean(b['h_differences']['H2']):.2f} 秒；P95 {b['base_p95_s']:.2f}→{b['candidate_p95_s']:.2f} 秒；最坏单场退步 {b['worst_regression_s']:.2f} 秒。",
                  f"压力世界 {s['n']} 对：均值 {s['base_mean_s']:.2f}→{s['candidate_mean_s']:.2f} 秒，最坏单场退步 {s['worst_regression_s']:.2f} 秒。压力混合不当成官方概率分布。",
                  f"确认程序平均实际耗时 {b['mean_candidate_wall_s']:.2f} 秒，最大 {b['max_candidate_wall_s']:.2f} 秒。"]
    (ROOT/'RESULTS.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(adopted)


if __name__=='__main__':finalize()
