"""Regenerate comparison tables from completed matched-case local runs."""
import collections
import html
import json
from pathlib import Path
import statistics
from benchmark import paired,quantile,write_json

ROOT=Path(__file__).resolve().parent
LABELS={
 'main':'当前主线部署版', 'side':'侧线已选组合',
 'attachment':'附件规则接入版',
 'attachment_cap2':'附件＋限制共享复测',
 'attachment_active':'上一版＋成本型首测',
 'attachment_cost':'限制共享复测＋成本型补站',
 'attachment_active_cost':'附件迭代组合',
 'attachment_selective':'附件＋选择性扫描',
 'attachment_slim':'附件＋限制共享＋选择性扫描',
 'p4_transfer':'25点＋条件顺路补测',
 'p4_route':'25点＋服务排序/重排',
 'p4_route_rescue':'25点＋调度＋条件补测',
}


def average(rs,k):return statistics.mean(r[k] for r in rs)


def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|']+
                     ['| '+' | '.join(map(str,r))+' |' for r in rows])


def main():
    full=ROOT/'runs/full360'
    data=json.loads((full/'results.json').read_text(encoding='utf-8'))
    report=ROOT/'report';report.mkdir(exist_ok=True)
    groups=collections.defaultdict(list)
    for r in data:groups[(r['problem'],r['variant'])].append(r)
    lookup={(r['case_id'],r['variant']):r for r in data}
    summaries=[]
    for (p,v),rs in sorted(groups.items()):
        ds=[r['T_s']-lookup[(r['case_id'],'main')]['T_s'] for r in rs]
        item={'problem':p,'variant':v,'label':LABELS[v],'n':len(rs),
              'complete':sum(r['complete'] for r in rs),
              'mean_T_s':average(rs,'T_s'),'mean_T_per_source_s':average(rs,'T_per_source_s'),
              'mean_walk_m':average(rs,'walk_m'),'mean_measures':average(rs,'measures'),
              'mean_switches':average(rs,'switches'),'mean_clear_attempts':average(rs,'clear_attempts'),
              'mean_failed_clears':average(rs,'failed_clears'),'mean_program_s':average(rs,'program_s'),
              'p90_T_s':quantile([r['T_s'] for r in rs],.9),'max_T_s':max(r['T_s'] for r in rs),
              'delta_main_s':statistics.mean(ds),'regressions_main':sum(d>1e-6 for d in ds),
              'worst_regression_main_s':max(ds),
              'H1_T_s':average([r for r in rs if r['hypothesis']=='H1'],'T_s'),
              'H2_T_s':average([r for r in rs if r['hypothesis']=='H2'],'T_s')}
        summaries.append(item)
    changes=[('attachment','attachment_cap2'),('attachment_cap2','attachment_active'),
             ('attachment_cap2','attachment_cost'),('attachment_active','attachment_active_cost'),
             ('main','p4_transfer'),('main','p4_route'),('p4_route','p4_route_rescue')]
    increments=[]
    for before,after in changes:
        pairs=[(lookup[(r['case_id'],before)],r) for r in data if r['variant']==after]
        for h in ['H1','H2','all']:
            ps=[(b,r) for b,r in pairs if h=='all' or r['hypothesis']==h]
            ds=[r['T_s']-b['T_s'] for b,r in ps]
            increments.append({'before':before,'after':after,'hypothesis':h,'n':len(ps),
              'delta_s':statistics.mean(ds),'regressions':sum(d>1e-6 for d in ds),'worst_s':max(ds),
              'movement_delta_s':statistics.mean((r['walk_m']-b['walk_m'])/5 for b,r in ps),
              'measure_delta_s':statistics.mean(5*(r['measures']-b['measures']) for b,r in ps),
              'switch_delta_s':statistics.mean(r['switches']-b['switches'] for b,r in ps),
              'clear_delta_s':statistics.mean(3*(r['clear_attempts']-b['clear_attempts']) for b,r in ps)})
    # Selection is descriptive development selection, NOT fresh confirmation.
    recommendations={}
    for p in (3,4):
        candidates=[x for x in summaries if x['problem']==p and x['complete']==x['n']]
        recommendations[str(p)]=min(candidates,key=lambda x:x['mean_T_s'])['variant']
    regression_rows=[]
    for p,v in recommendations.items():
        p=int(p)
        for ref in ['main','side']:
            ps=[(lookup[(r['case_id'],ref)],r) for r in data if r['problem']==p and r['variant']==v]
            for b,r in sorted(ps,key=lambda x:x[1]['T_s']-x[0]['T_s'],reverse=True)[:5]:
                regression_rows.append({'problem':p,'variant':v,'reference':ref,
                         'case_id':r['case_id'],'hypothesis':r['hypothesis'],'N':r['N'],'Nd':r['Nd'],
                         'delta_T_s':r['T_s']-b['T_s'],'baseline_T_s':b['T_s'],'candidate_T_s':r['T_s'],
                         'movement_delta_s':(r['walk_m']-b['walk_m'])/5,
                         'measure_delta_s':5*(r['measures']-b['measures']),
                         'switch_delta_s':r['switches']-b['switches'],
                         'clear_delta_s':3*(r['clear_attempts']-b['clear_attempts'])})
    stages={}
    for name in ['screen_v1','screen_v2','screen_v3','full360']:
        rs=json.loads((ROOT/'runs'/name/'results.json').read_text(encoding='utf-8'))
        stages[name]={'runs':len(rs),'worlds':len(set(r['case_id'] for r in rs)),
                      'complete':sum(r['complete'] for r in rs),'hull_violations':sum(r['hull_violations'] for r in rs)}
    payload={'scope':'all supplied 360 scenarios; development only; no official actions',
             'summary':summaries,'incremental_comparisons':increments,
             'recommendations':recommendations,'worst_cases':regression_rows,'stages':stages,
             'paired_main':paired(data,'main'),'paired_side':paired(data,'side')}
    write_json(report/'comparison.json',payload)
    indexed={(x['problem'],x['variant']):x for x in summaries}
    p3_main=indexed[(3,'main')];p3_att=indexed[(3,'attachment')]
    p3_iter=indexed[(3,'attachment_active_cost')];p3_side=indexed[(3,'side')]
    p4_main=indexed[(4,'main')];p4_new=indexed[(4,'p4_route_rescue')]
    selected={'role':'development_selected_not_officially_validated','policies':recommendations,
              'source':'report/comparison.json','dataset_worlds':360,
              'selection_note':'Lowest full-clear mean on the supplied equal-H development mixture; retain per-H results and regressions.',
              'p4_settings':{'coverage_points':25,'local_base_limit':1,'joint_detour_m':800,
                             'service_score':'hull','reorder_unvisited':True,
                             'max_extra_measures':1,'cover_cell_threshold':12,'extra_first_clear_proxy_gate_s':60,
                             'approach_standoff_m':200}}
    write_json(report/'selected.json',selected)
    lines=['# 附件方案与当前部署方案：360场景配对比较','',
           '本轮使用用户提供的 H1/H2 筛选数据集：P3/P4 各180世界，每题H1/H2各90。所有策略恢复相同目标和固定误差场。它们是历史合成开发场景，结果不是官方实测，也不是新的独立确认。', '',
           '**附件本身没有整局控制程序，只有P3模型、几何函数和标定脚本。** “附件规则接入版”是本轮将其共享测量、抵近/正交测点、惰性补站与小区域直接铺盖接成可运行流程后的结果；不能称为原作者完整程序成绩。P4对应项是本轮迁移实验。', '',
           '## 结论','',
           f"P3保留侧线已选组合：平均{p3_side['mean_T_s']:.2f}秒，比主线少{-p3_side['delta_main_s']:.2f}秒。附件接入版{p3_att['mean_T_s']:.2f}秒，经三项改动降至{p3_iter['mean_T_s']:.2f}秒，但仍比侧线慢{p3_iter['mean_T_s']-p3_side['mean_T_s']:.2f}秒。没有因为来自新附件而强行选它。",'',
           f"P4保留25点并合并服务调度与有限条件补测：平均{p4_main['mean_T_s']:.2f}→{p4_new['mean_T_s']:.2f}秒，下降{-p4_new['delta_main_s']/p4_main['mean_T_s']*100:.2f}%。H1与H2均值均改善；180例中{p4_new['regressions_main']}例退步，最大多花{p4_new['worst_regression_main_s']:.2f}秒。",'',
           '## 附件接入的明确实现选择','',
           '附件未给出可执行的整局控制器或完整信息收益权重，本轮按其核心规则作如下具体化，不冒称唯一忠实实现：原点全扫；按当前可行区域中心距离选择已发现目标；正交第二测点与200米抵近候选；当前认证覆盖不超过3格时直接清除，否则局部最多追加2测；每次清除后扫描未知/已知未清频道；从采样的最深未覆盖点回拉800米补站；连续域证明未通过则继续，最多12个惰性站后转1260米7站保证覆盖。', '',
           '迭代依次限制已知频道获得两次阳性后不再无差别共享复测；首次局部测点改用现有移动/收缩成本排序；补站改为从当前机器人出发，比较新增覆盖采样数与移动/扫描秒数。采样只提议动作，退出仍由连续覆盖证书决定。选择性跳过扫描在小批次增加绕路，因此未纳入全量优选组合。', '',
           'P4只迁移有限抵近补测：在基线L1后当前清除计划大于12格，且“经补测点到原首个清除点”的额外费用小于60秒时，最多再测一次。这个门槛是本轮固定启发式，不是完整分支价值函数，也不声称最优阈值。全向惰性排除规则没有用于P4。','',
           '## 全量比较','']
    for p in (3,4):
        ss=[x for x in summaries if x['problem']==p]
        lines+=['### P'+str(p),'',table(['策略','全清','H1均T','H2均T','合并均T','相对主线秒','退步/180'],[
          [x['label'],f"{x['complete']}/{x['n']}",f"{x['H1_T_s']:.2f}",f"{x['H2_T_s']:.2f}",
           f"{x['mean_T_s']:.2f}",f"{x['delta_main_s']:+.2f}",str(x['regressions_main'])] for x in ss]),'']
        lines+=[table(['策略','平均T/N','移动米','检测','换频','清除尝试','失败','程序秒'],[
           [x['label'],f"{x['mean_T_per_source_s']:.2f}",f"{x['mean_walk_m']:.2f}",f"{x['mean_measures']:.2f}",
            f"{x['mean_switches']:.2f}",f"{x['mean_clear_attempts']:.2f}",f"{x['mean_failed_clears']:.2f}",f"{x['mean_program_s']:.3f}"] for x in ss]),'']
    lines+=['## 单项增量与成本来源','',
      '负数表示后者更快。这里同世界成功数相同，成功附加费用抵消；移动、检测、换频、清除项之和等于总差。', '',
      table(['前者→后者','均差秒','移动','检测','换频','清除','退步/180'],[
        [LABELS[x['before']]+'→'+LABELS[x['after']],f"{x['delta_s']:+.2f}",f"{x['movement_delta_s']:+.2f}",
         f"{x['measure_delta_s']:+.2f}",f"{x['switch_delta_s']:+.2f}",f"{x['clear_delta_s']:+.2f}",x['regressions']]
        for x in increments if x['hypothesis']=='all']),'',
      '## 退步案例','',table(['题/候选','参考','案例','H','N/Nd','多用秒','移动差','检测差','清除差'],[
        [f"P{x['problem']} {LABELS[x['variant']]}",LABELS[x['reference']],x['case_id'],x['hypothesis'],
         f"{x['N']}/{x['Nd']}",f"{x['delta_T_s']:+.2f}",f"{x['movement_delta_s']:+.2f}",
         f"{x['measure_delta_s']:+.2f}",f"{x['clear_delta_s']:+.2f}"] for x in regression_rows]),'',
      '## 复现','',
      '源代码 `policies.py`、`benchmark.py`；附件原件解压在 `attachment/solution`，数据与原固定模拟器在 `dataset_input`，使用的部署代码副本在 `vendor`。主工程、官方服务、原ZIP均未修改。', '',
      '```powershell',
      "& 'D:/st_python/python.exe' -B -X utf8 benchmark.py --output runs/reproduce_new --workers 6 --variants main side attachment attachment_cap2 attachment_active attachment_cost attachment_active_cost p4_transfer p4_route p4_route_rescue",
      '```','',
      '全量逐局数据位于 `runs/full360/results.json`；每例有原请求与决策压缩JSONL。各阶段保留参数、策略源码快照、场景哈希。任何失败都不参与候选胜出；当前均值不代表官方总体期望。', '',
      '## 第一性原理与解释边界','',
      '- 一个测点访问只免掉部分共同移动，不免除逐频道检测和换频。已经有两次阳性后仍无差别复测，可能只增加费用。',
      '- 附件标定脚本中使用的已知目标巡游不包括在线获知坐标的全部代价；2500—3000秒估算不能作为实际完整求解成绩。',
      '- 最深未覆盖点是覆盖启发，未必离当前机器人近；补站应同时比较新增覆盖与新增秒数。采样仅用于提议测点，退出使用连续区域覆盖证书。',
      '- P4不沿用全向无信号的排除圆规则。保留25点定向覆盖集合，迁移只改变服务顺序与有限补测。',
      '- 附件内接圆域及1度角界没有用于安全外包；各候选共用主线的保守误差界与实际清除证书，避免把缩小错误可行集当效率。',
      '- 这些场景都已用于开发。本轮只得出所给数据集上的候选结论，之后可用新的场景与官方演练检查迁移。','']
    lines+=['## 下一步针对什么继续迭代','',
      'P3附件迭代版比侧线少走约1747米，却多约103.5次检测；额外检测费用超过移动节省。下一步应只复用能替代未来必需访问的测量，而非继续扩大无差别扫描；保留侧线作为强对照。', '',
      'P4本轮535.47秒平均节省中，468.55秒来自服务排序/重排组合，追加条件补测在其上再省66.92秒。主改进是路线与服务关系；下一步是让服务评分预测实际测量—清除—下一站的费用，并对会丢信号的服务位置加以区分。', '',
      '最大退步例 stage2-round-calibrated-p4-0056-739f6a942526e1a5 多花1093.22秒。频道6从原来的局部direction变成no_signal，清除尝试10→75；全局移动也增加约706.07秒。补测和路线不是互相独立的收益，不能把它们的单项百分比相加。第二大退步例增加了全局扫描次数与局部移动，说明短后缀也可能延迟某些源的发现。详见 diagnostics.json。','']
    (report/'comparison.md').write_text('\n'.join(lines),encoding='utf-8')
    # A compact standalone table view, no network resources or workspace controls.
    sections=[]
    for p in (3,4):
        ss=[x for x in summaries if x['problem']==p]
        sections.append('<h2>P'+str(p)+' · 180个相同场景</h2><table><thead><tr>'+''.join('<th>'+s+'</th>' for s in ['策略','全清','H1 / 秒','H2 / 秒','平均总时间','相比主线','退步局数'])+'</tr></thead><tbody>')
        for x in ss:
            vals=[x['label'],f"{x['complete']}/{x['n']}",f"{x['H1_T_s']:.2f}",f"{x['H2_T_s']:.2f}",f"{x['mean_T_s']:.2f}",f"{x['delta_main_s']:+.2f}",str(x['regressions_main'])]
            sections.append('<tr>'+''.join('<td>'+html.escape(v)+'</td>' for v in vals)+'</tr>')
        sections.append('</tbody></table>')
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>附件方案对照实验</title><style>body{font-family:system-ui,sans-serif;max-width:1200px;margin:40px auto;padding:0 24px;color:#172435;background:#fafbfc}h1{font-size:26px}h2{margin-top:36px;font-size:20px}table{border-collapse:collapse;width:100%;background:white}th,td{padding:12px;border-bottom:1px solid #e1e6ed;text-align:right;font-variant-numeric:tabular-nums}th:first-child,td:first-child{text-align:left}p{line-height:1.7;color:#526174}@media(max-width:750px){body{padding:10px}table{font-size:12px}th,td{padding:7px}}</style><h1>附件方案与当前部署方案：配对实验</h1><p>同一批360个合成世界 · 同一固定误差场 · H1/H2分开报告<br>附件规则接入版为本轮实现；表中是本地开发结果，未执行官方测试。</p>'+''.join(sections)+'</html>'
    (report/'comparison.html').write_text(page,encoding='utf-8')
    print(json.dumps({'stages':stages,'recommendations':recommendations,'summary':summaries},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
