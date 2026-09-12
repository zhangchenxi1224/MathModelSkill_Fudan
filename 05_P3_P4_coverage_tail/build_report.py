from pathlib import Path
import json,statistics,html,os,hashlib,shutil
import run
from metrics import paired
from diagnose import trace_metrics
ROOT=Path(__file__).resolve().parent

def fmt(x):
    if x is None:return '—'
    if isinstance(x,bool):return '是' if x else '否'
    return f'{x:.2f}' if isinstance(x,float) else str(x)

def table(rows,columns):
    out='<div class="scroll"><table><thead><tr>'+''.join('<th>'+label+'</th>' for key,label in columns)+'</tr></thead><tbody>'
    for r in rows:
        out+='<tr>'+''.join('<td>'+html.escape(fmt(r.get(k)))+'</td>' for k,label in columns)+'</tr>'
    return out+'</tbody></table></div>'

def main():
    base={r['world_key']:r for r in run.read(ROOT/'inputs/deployed_baseline_results.json')}
    decisions={};confirmation=[];dev=[];paired_rows=[];all_runs=[];comparisons=[]
    for directory in sorted((ROOT/'runs').iterdir()):
        if not (directory/'results.json').exists():continue
        rows=run.read(directory/'results.json');all_runs.extend(rows)
        if directory.name.startswith('confirmation'):continue
        for p,arm in sorted({(r['problem'],r['arm']) for r in rows}):
            sub=[r for r in rows if r['problem']==p and r['arm']==arm]
            for mode in ['correlated','extreme']:
                m=paired(sub,base,p,mode)
                if m:comparisons.append(dict(stage=directory.name,arm=arm,**m))
    for p in (3,4):
        frozen=run.read(ROOT/f'frozen_p{p}.json');arm=frozen['candidate']
        rows=run.read(ROOT/f'runs/confirmation_p{p}/results.json')
        b={r['world_key']:r for r in rows if r['arm']=='current'}
        chosen=[r for r in rows if r['arm']==arm]
        if len(b)!=120 or len(chosen)!=120:raise ValueError('Confirmation incomplete')
        values=[paired(chosen,b,p,mode) for mode in ['correlated','extreme']]
        traces={r['world_key']:trace_metrics(r['requests_path']) for r in chosen}
        base_traces={k:trace_metrics(r['requests_path']) for k,r in b.items()}
        for m,mode in zip(values,['correlated','extreme']):
            g=[r for r in chosen if r['error_mode']==mode]
            m['movement_saved_s']=statistics.mean((b[r['world_key']]['walk_distance_m']-r['walk_distance_m'])/5 for r in g)
            m['measurement_extra_s']=statistics.mean(5*(r['measures']-b[r['world_key']]['measures']) for r in g)
            m['switch_extra_s']=statistics.mean(r['switches']-b[r['world_key']]['switches'] for r in g)
            m['clear_extra_s']=statistics.mean(3*(r['clear_attempts']-b[r['world_key']]['clear_attempts']) for r in g)
            m['opportunity_search_measures']=sum(r.get('opportunity_search_measures',0) for r in g)
            m['opportunity_search_discoveries']=sum(r.get('opportunity_search_discoveries',0) for r in g)
            m['baseline_after_discovery_s']=statistics.mean(base_traces[r['world_key']]['after_all_discovered_s'] for r in g)
            m['after_discovery_s']=statistics.mean(traces[r['world_key']]['after_all_discovered_s'] for r in g)
            m['baseline_after_last_clear_s']=statistics.mean(base_traces[r['world_key']]['after_last_clear_s'] for r in g)
            m['after_last_clear_s']=statistics.mean(traces[r['world_key']]['after_last_clear_s'] for r in g)
        confirmed=arm!='current' and all(v['complete']==60 for v in values) and values[0]['CI95_delta_T_per_N'][1]<0 and values[1]['macro_gain_pct']>=0
        decisions[str(p)]=dict(candidate=arm if confirmed else 'current',tested_candidate=arm,
            status='confirmed_on_fresh_local' if confirmed else 'retain_deployed_baseline',
            reason='Independent H1 paired interval supports improvement; H2 point estimate non-regressing.' if confirmed else 'Predeclared confirmation criteria not met; no alternative selected on confirmation.',
            baseline='opportunity' if p==3 else 'count_transit',results=f'runs/confirmation_p{p}')
        for m in values:confirmation.append(dict(arm=arm,promoted=confirmed,**m))
        if frozen['development_path']:
            dr=run.read(ROOT/frozen['development_path']/'results.json')
            dev.extend(dict(arm=arm,**paired(dr,base,p,mode)) for mode in ['correlated','extreme'])
        for r in chosen:
            br=b[r['world_key']]
            paired_rows.append(dict(problem=p,H='H1' if r['error_mode']=='correlated' else 'H2',N=r['source_total'],Nd=r['directional_total'],
                case_id=r['case_id'],world_key=r['world_key'],arm=arm,promoted=confirmed,complete=r['complete'],
                baseline_T=br['total_virtual_time_s'],T=r['total_virtual_time_s'],T_per_N=r['T_per_source_s'],
                delta_T=r['total_virtual_time_s']-br['total_virtual_time_s'],gain_pct=100*(1-r['total_virtual_time_s']/br['total_virtual_time_s']),
                distance_m=r['walk_distance_m'],measures=r['measures'],switches=r['switches'],attempts=r['clear_attempts'],
                failures=r['clear_attempts']-r['clear_successes'],requests=r['requests_path'],decisions=r['decisions_path'],
                baseline_requests=br['requests_path'],baseline_decisions=br['decisions_path'],
                search_measures=r.get('opportunity_search_measures',0),search_discoveries=r.get('opportunity_search_discoveries',0)))
    release=dict(status='fresh_local_confirmation_complete',policies=decisions,
        source='tail_policy.py',data=run.read(ROOT/'data/provenance.json'),
        official_status='No new official rounds run in this iteration. Latest20 official logs were used for diagnosis only.')
    run.write(ROOT/'release.json',release)
    for name,obj in [('confirmation_summary',confirmation),('development_summary',dev),('candidate_comparisons',comparisons),('paired_cases',paired_rows)]:
        run.write(ROOT/f'reports/{name}.json',obj)
    overview=dict(stored_runs=len(all_runs),actual_runs=sum(not r.get('reuse_origin') for r in all_runs),
         reused_runs=sum(bool(r.get('reuse_origin')) for r in all_runs),complete=sum(r['complete'] for r in all_runs),
         fresh_worlds=240,fresh_paired_runs=480,selected_policies=decisions)
    run.write(ROOT/'reports/overview.json',overview)
    for r in all_runs:
        if not r['complete']:print('INCOMPLETE',r.get('case_id'),r.get('arm'))
    fields=[('problem','题目'),('H','误差模型'),('arm','候选'),('cases','场景'),('complete','全清'),('macro_gain_pct','各N等权提速%'),('low_N_gain_pct','N10—15提速%'),('N16_gain_pct','N16提速%'),('slower','变慢数'),('max_regression_s','最大退步秒')]
    page='''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>覆盖与收尾优化 · 配对实验</title><style>
body{margin:0;background:#f3f5f8;color:#17263c;font:14px/1.65 "Segoe UI","Microsoft YaHei",sans-serif}main{max-width:1500px;margin:auto;padding:32px}h1{font-size:30px}h2{font-size:21px;margin-top:34px}.sub{color:#536176}.scroll{overflow:auto;background:white;border:1px solid #e2e8f0;border-radius:12px;margin:16px 0}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;white-space:nowrap}th,td{padding:11px 13px;border-bottom:1px solid #e8edf3;text-align:right}th{background:#edf2f9;color:#243f63}tr:hover td{background:#f5f9ff}a{color:#2465b4}select{padding:8px;margin:0 12px 10px 0}.cards{display:flex;gap:16px;flex-wrap:wrap}.card{background:#fff;padding:18px 25px;border-radius:12px;border:1px solid #e2e8f0}.num{font-size:25px;font-weight:650}img{max-width:100%;background:white;border-radius:12px}.bad{color:#b13c34}.good{color:#14735b}</style><main>
<h1>从覆盖路线到搜索收尾</h1><p class="sub">基线：最近官方20局的 P3 opportunity / P4 count_transit。旧240开发，新240冻结确认；本轮未运行旧960，也未新增官方测试。</p>'''
    page+='<div class="cards">'+''.join(f'<div class="card"><div>{label}</div><div class="num">{value}</div></div>' for label,value in [('实际场景运行',overview['actual_runs']),('新确认场景',240),('保存全清记录',f"{overview['complete']}/{overview['stored_runs']}")])+'</div>'
    page+='<h2>最终采用</h2><p>'+html.escape('；'.join(f"P{p}："+(f"保留原版 {d['baseline']}" if d['candidate']=='current' else f"采用 {d['candidate']}（双环覆盖＋顺路搜索）") for p,d in decisions.items()))+'</p>'
    page+='<h2>新240：候选与基线配对确认</h2><p class="sub">正提速表示更快。下表始终报告已冻结候选，包括未达到采用条件的候选。N只用于离线分组，算法不知道真实总数。</p>'
    page+=table(confirmation,fields+[('promoted','采用')])
    intervals=[]
    for m in confirmation:intervals.append(dict(problem=m['problem'],H=m['H'],baseline=m['baseline_mean_T'],candidate=m['mean_T'],
                  CI=f"[{m['CI95_gain_pct'][0]:.2f}%, {m['CI95_gain_pct'][1]:.2f}%]",baseline_p95=m['baseline_p95_T_per_N'],p95=m['p95_T_per_N']))
    page+=table(intervals,[('problem','题目'),('H','模型'),('baseline','基线平均总时间'),('candidate','候选平均总时间'),('CI','各N等权提速95%区间'),('baseline_p95','基线P95秒/源'),('p95','候选P95秒/源')])
    page+='<h2>收益来自哪里</h2><p class="sub">移动列正数表示节省，其余成本列正数表示额外支出；清除成功数相同，成功附加成本抵消。</p>'
    page+=table(confirmation,[('problem','题目'),('H','模型'),('movement_saved_s','平均节省移动秒'),('measurement_extra_s','额外检测秒'),('switch_extra_s','额外换频秒'),('clear_extra_s','额外清除秒'),('opportunity_search_measures','顺路搜索检测'),('opportunity_search_discoveries','顺路新发现')])
    page+='<p class="sub">收尾时间用于诊断，不单独用于选型：推迟清除也会改变末次清除时刻，最终仍比较整局总时间。</p>'
    page+=table(confirmation,[('problem','题目'),('H','模型'),('baseline_after_discovery_s','基线末次发现后秒'),('after_discovery_s','候选末次发现后秒'),('baseline_after_last_clear_s','基线末次清除后秒'),('after_last_clear_s','候选末次清除后秒')])
    page+='<h2>几何变化</h2><img src="geometry_comparison.png" alt="P4原三角网与双环覆盖路线"><p class="sub">这是无目标处理绕路的固定搜索路线；运行中仍会根据反馈重排。覆盖判据检查连续位置区域及任意定向朝向，不以离散真值样本充当证明。</p>'
    page+='<h2>新确认集：逐源数组</h2>'
    grouped=[dict(problem=m['problem'],H=m['H'],**g) for m in confirmation for g in m['by_N']]
    page+=table(grouped,[('problem','题目'),('H','模型'),('N','N'),('cases','场景'),('baseline_T','基线平均总时间'),('T','候选平均总时间'),('gain_pct','提速%')])
    page+='<h2>开发集与其它候选</h2><p class="sub">screen 是每题14场分层筛选；full 是每题120场完整比较，不能将两种规模直接排成统一榜单。</p>'
    page+=table(comparisons,[('stage','实验阶段')]+fields)
    page+='<h2>新240：每场结果与日志</h2><select id="problem"><option value="">全部题目</option><option value="3">P3</option><option value="4">P4</option></select><select id="count"><option value="">全部源数</option>'+''.join(f'<option value="{n}">{n}源</option>' for n in range(10,17))+'</select><select id="model"><option value="">H1＋H2</option><option>H1</option><option>H2</option></select>'
    page+='<div class="scroll"><table id="cases"><thead><tr>'+''.join('<th>'+s+'</th>' for s in ['题目','H','N/Nd','案例','基线T','候选T','秒/源','变化秒','移动米','检测','换频','清除尝试','失败','日志'])+'</tr></thead><tbody>'
    for r in sorted(paired_rows,key=lambda r:(r['problem'],r['H'],r['N'],r['case_id'])):
        page+=f'<tr data-p="{r["problem"]}" data-n="{r["N"]}" data-h="{r["H"]}">'
        values=[r['problem'],r['H'],f"{r['N']}/{r['Nd']}",r['case_id'],r['baseline_T'],r['T'],r['T_per_N'],r['delta_T'],r['distance_m'],r['measures'],r['switches'],r['attempts'],r['failures']]
        page+=''.join('<td>'+html.escape(fmt(v))+'</td>' for v in values)
        links=[]
        for key,label in [('requests','请求'),('decisions','决策'),('baseline_requests','基线请求'),('baseline_decisions','基线决策')]:
            rel=os.path.relpath(r[key],ROOT/'reports').replace('\\','/')
            links.append('<a href="'+html.escape(rel,quote=True)+'">'+label+'</a>')
        page+='<td>'+' · '.join(links)+'</td></tr>'
    page+='</tbody></table></div><h2>数据依据与范围</h2><p>新240沿用官方369组成比较与320次同协议反馈比较支持的代表模型，H1主要、H2敏感性；新旧240无重复。位置、接收半径、朝向及具体误差场仍未完全识别。本次增加独立样本，没有仅为匹配耗时而改动物理参数。新数据只比较事先冻结候选与基线，没有用它重选第二候选。</p><p><a href="../data/provenance.json">生成依据</a> · <a href="fidelity_behavior_check.json">最新官方与本地行为对照</a> · <a href="official_diagnosis.json">官方20局诊断</a> · <a href="../release.json">部署选择</a></p></main>'
    page+='''<script>const selects=['problem','count','model'].map(id=>document.getElementById(id));function filter(){const[p,n,h]=selects.map(x=>x.value);for(const r of document.querySelectorAll('#cases tbody tr'))r.hidden=!!((p&&r.dataset.p!==p)||(n&&r.dataset.n!==n)||(h&&r.dataset.h!==h));}selects.forEach(s=>s.addEventListener('change',filter));</script></html>'''
    (ROOT/'reports/results.html').write_text(page,encoding='utf-8')
    lines=['# 本轮结果','','基线为最近官方20局部署版本；新240为选型后独立确认。','', '|题目|H|冻结候选|全清|各N等权提速|N10—15|N16|变慢数|采用|','|---|---|---|---:|---:|---:|---:|---:|---|']
    for m in confirmation:lines.append(f"|P{m['problem']}|{m['H']}|{m['arm']}|{m['complete']}/{m['cases']}|{m['macro_gain_pct']:+.2f}%|{m['low_N_gain_pct']:+.2f}%|{m['N16_gain_pct']:+.2f}%|{m['slower']}|{m['promoted']}|")
    lines+=['','P4覆盖路线由约24.10km缩短到17.73km；这是固定搜索路线，不是整局距离。','',json.dumps(overview,ensure_ascii=False,indent=2),'','全部逐局数据见 reports/results.html。未宣称全局最优或每个新官方场景必然改善。']
    (ROOT/'reports/FINAL_REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(overview=overview,confirmation=confirmation),ensure_ascii=False))

if __name__=='__main__':main()
