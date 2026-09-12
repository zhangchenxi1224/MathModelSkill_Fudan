"""Predeclared fidelity-aware selection and final model-stratified reporting."""
from pathlib import Path
import argparse
import json
import random
import statistics as st
import html
import run_pipeline as run

ROOT = Path(__file__).resolve().parent


def select():
    from bsolver.round_experiments import categorical, make_scenario
    if (ROOT/'selection.json').exists():
        raise FileExistsError('Selection is already frozen')
    rows = run.read(ROOT/'runs/development/results.json')
    assert len(rows) == 3460
    summary = run.paired_summary(rows)
    selected = {}
    for p in [3, 4]:
        eligible = [r for r in summary if r['problem'] == p and r['complete'] == r['n']
            and r['baseline_complete'] == r['n'] and r['mean_delta_s'] <= 0
            and all(v is not None and v <= 0 for v in r['H_mean_delta_s'].values())]
        if p == 3:
            eligible = [r for r in eligible if r['weighted_delta_s'] < 0]
            winner = min(eligible, key=lambda r:(r['weighted_delta_s'],r['variant'])) if eligible else None
        else:
            eligible = [r for r in eligible if r['H_mean_delta_s']['H1'] < 0]
            winner = min(eligible, key=lambda r:(r['H_mean_delta_s']['H1'],r['variant'])) if eligible else None
        selected[str(p)] = winner['variant'] if winner else 'main'
    frozen = {'selected': selected, 'summary': summary,
        'development_results_sha256': run.file_sha(ROOT/'runs/development/results.json'),
        'code_sha256': run.code_digest(), 'selection_script_sha256': run.file_sha(__file__),
        'amendment_sha256':run.file_sha(ROOT/'selection_amendment_preselection.json'),
        'fidelity_rule_sha256':run.file_sha(ROOT/'calibration/fidelity_confirmation_rule.json'),
        'model_roles_sha256':run.file_sha(ROOT/'calibration/model_roles_after_checks.json'),
        'generation_model_sha256':run.file_sha(ROOT/'calibration/generation_model.json'),
        'frozen_before_confirmation':True,
        'rank_rule':{'3':'new composition weighted delta, both H non-regression',
                     '4':'H1 mean delta, both H non-regression; weighted P4 unsuitable due low ESS/support'}}
    run.write(ROOT/'selection.json',frozen)
    models=run.read(ROOT/'calibration/generation_model.json')['problems']
    excluded=set(run.read(ROOT/'data/previous_world_hashes.json'))
    excluded.update(run.world_sha(j['case']) for j in run.read(ROOT/'data/protocol320.json'))
    for branch in ['selected_dataset_iteration_20260911','synthesis_iteration_20260911']:
        for name in ['confirmation_cases.json','stress_cases.json']:
            for row in run.read(run.SIDE/branch/'campaign'/name):excluded.add(run.world_sha(row['case']))
    fresh=[]
    for p in [3,4]:
        for h,mode in [('H1','correlated'),('H2','extreme')]:
            rng=random.Random(run.seed('official369-confirm-v1-20260912',p,h))
            for i in range(60):
                n,nd=categorical(rng,models[str(p)]['count_distribution'])
                c=make_scenario(rng.getrandbits(63),p,n,nd,pool='official369_confirmation',model='official369_selected_broad_joint',
                               error_mode=mode,index=i,partition='confirmation')
                w=run.world_sha(c)
                assert w not in excluded
                excluded.add(w)
                fresh.append({'case':c,'hypothesis':h,'world_sha256':w,'composition_weight':1.,
                              'role':'primary_fidelity_confirmation' if h=='H1' else 'sensitivity_confirmation'})
    run.write(ROOT/'data/confirmation240.json',fresh)
    print(json.dumps({'selected':selected,'fresh_worlds':len(fresh),'summary':summary},ensure_ascii=False,indent=2),flush=True)


def model_summary(rows):
    lookup={(r['world_sha256'],r['variant']):r for r in rows}
    out=[]
    for p in [3,4]:
        for h in ['H1','H2']:
            for variant in sorted({r['variant'] for r in rows if r['problem']==p and r['hypothesis']==h}):
                rs=[r for r in rows if r['problem']==p and r['hypothesis']==h and r['variant']==variant]
                ds=[r['loss_s']-lookup[(r['world_sha256'],'main')]['loss_s'] for r in rs]
                out.append({'problem':p,'model':h,'variant':variant,'n':len(rs),'complete':sum(r['complete'] for r in rs),
                    'mean_total_s':run.mean([r['loss_s'] for r in rs]),
                    'mean_delta_s':run.mean(ds),'ci95_delta_s':run.interval(ds),
                    'regressions':sum(d>1e-5 for d in ds),'worst_delta_s':max(ds),
                    'mean_per_source_s':run.mean([r.get('mean_time_per_source_s',r['loss_s']/r['n']) for r in rs]),
                    **{k:run.mean([r[k] for r in rs if k in r]) for k in ['walk_distance_m','measures','switches','clear_attempts','failed_clear_attempts','program_real_time_s']}})
    return out


def finalize():
    run.report()
    dev=run.read(ROOT/'runs/development/results.json')
    con=run.read(ROOT/'runs/confirmation/results.json')
    selected=run.read(ROOT/'selection.json')['selected']
    devsum=model_summary(dev); cons=model_summary(con)
    aggregate=run.read(ROOT/'report/summary.json')['verdict']
    verdict={}
    for p in [3,4]:
        target=selected[str(p)]
        a=next((r for r in cons if r['problem']==p and r['model']=='H1' and r['variant']==target),None)
        b=next((r for r in cons if r['problem']==p and r['model']=='H2' and r['variant']==target),None)
        good=bool(target!='main' and aggregate[str(p)]['accepted'] and a and b
                  and a['complete']==a['n'] and b['complete']==b['n'] and a['ci95_delta_s'][1]<0 and b['mean_delta_s']<=0)
        verdict[str(p)]={'selected_candidate':target,'accepted_fidelity_confirmation':good,
            'recommended_variant':target if good else 'main','H1_primary':a,'H2_sensitivity':b,
            'official_transfer_status':'not tested in new official cases'}
    run.write(ROOT/'report/fidelity_summary.json',{'development':devsum,'confirmation':cons,'verdict':verdict})
    run.write(ROOT/'release.json',{'recommended':{p:x['recommended_variant'] for p,x in verdict.items()},
              'selection_sha256':run.file_sha(ROOT/'selection.json'),'code_sha256':run.code_digest(),
              'confirmation_results_sha256':run.file_sha(ROOT/'runs/confirmation/results.json'),
              'meaning':'Local confirmation only; official interface execution remains a separate user action.'})
    heads=['题','模型','方案','全清','总虚拟秒','平均秒/源','移动米','检测','换频','清除尝试','失败','程序秒','相对主线秒']
    def tab(rows):
        return [[r['problem'],r['model'],r['variant'],f"{r['complete']}/{r['n']}"]+
            [round(r[k],2) if r[k] is not None else None for k in ['mean_total_s','mean_per_source_s','walk_distance_m',
                'measures','switches','clear_attempts','failed_clear_attempts','program_real_time_s','mean_delta_s']] for r in rows]
    def mdtable(rows):
        return ['| '+' | '.join(heads)+' |','|'+'|'.join(['---']*len(heads))+'|']+['| '+' | '.join(map(str,r))+' |' for r in tab(rows)]
    md=['# 369→960→240：实际完成的主线实验','',
        'H1是经本轮反馈比较后更接近官方的代表模型；H2保留为敏感性场景。二者均为合成环境，不是官方隐藏案例复刻。', '',
        '## 新240场景确认', '']+mdtable(cons)+['','## 旧960场景配对开发','']+mdtable(devsum)
    md+=['','## 数据更新结论','',
        '369局官方组成模型已重新拟合。用旧120局之外的49局做开发校准检验后，再以全部369局拟合同一个固定平滑模型族。',
        '同协议320次本地对照全部完成。H2在两题、两协议中均出现过于激进的定位区域收缩，因此不能与H1混称官方预期表现。',
        '旧P4开发集每种H只覆盖更新组成概率约56%，重加权有效样本量约20—22；P4据此采用H1未加权开发排名，要求H2不退步，之后再用新组成确认。',
        '原始开发集没有改写。240个新场景在候选、规则与组成模型冻结后生成，全部与已有世界不重复；组成采用官方保留数据中表现更好的宽联合模型，不强行使用经验频率。', '',
        '## 最终保留', '',json.dumps({p:{'recommended':v['recommended_variant'],'accepted':v['accepted_fidelity_confirmation']} for p,v in verdict.items()},ensure_ascii=False), '',
        '本轮没有新增官方演练。是否迁移到官方，还需要新的官方案例；本地数据不能代填官方成绩。', '',
        '完整逐局表见results.html；分模型统计见fidelity_summary.json；推荐配置见../release.json。']
    (ROOT/'report/mainline_results.md').write_text('\n'.join(md),encoding='utf-8')
    def ht(rows):
        return '<table><thead><tr>'+''.join('<th>'+html.escape(str(x))+'</th>' for x in heads)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in r)+'</tr>' for r in tab(rows))+'</tbody></table>'
    page='<!doctype html><meta charset="utf-8"><title>369→960→240 主线实验</title><style>body{font:15px system-ui;color:#172a42;margin:36px}table{border-collapse:collapse;width:100%;margin:20px 0}th,td{padding:10px;border-bottom:1px solid #dae2ed;text-align:right}th{background:#edf3fa}p{line-height:1.8}</style><h1>369 → 960 → 240 主线实验</h1><p>369局官方数据更新模型，960个原样场景配对开发，240个新场景确认。H1为主要拟真参考，H2为敏感性对照。下列结果均来自本地实际执行。</p><h2>新场景确认</h2>'+ht(cons)+'<h2>开发集比较</h2>'+ht(devsum)+'<p><a href="results.html">查看完整逐局表</a> · <a href="fidelity_summary.json">机器可读结果</a> · <a href="../release.json">推荐配置</a></p><p>本轮未新增官方演练，未恢复唯一官方生成器。失败局保留且不能用短退出时间改善评价。</p>'
    (ROOT/'report/mainline_results.html').write_text(page,encoding='utf-8')
    print(json.dumps(verdict,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['select','finalize']);a=p.parse_args()
    select() if a.command=='select' else finalize()
