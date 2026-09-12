"""Generate a verdict and concise Chinese report from completed independent audit."""
from pathlib import Path
import json
import experiment as e

ROOT=Path(__file__).resolve().parent


def main():
    audit=e.read(ROOT/'reports/independent_audit.json')
    selection=e.read(ROOT/'campaign/frozen_selection.json')
    acceptance=e.read(ROOT/'campaign/validation_acceptance.json')
    record=e.read(ROOT/'campaign/validation_freeze_record.json')
    assert audit['audit_passed'] is True and audit['issue_count']==0
    assert audit['logs_independently_audited'] is True
    assert audit['selection_sha256']==selection['selection_sha256']==record['selection_sha256']
    assert e.digest(acceptance)==record['acceptance_sha256']
    assert audit['protocol_sha256']==record['protocol_sha256']
    assert e.files()==selection['source_files']
    def row(partition,problem,h='design_mixture'):
        return next(r for r in audit['comparisons'] if (r['partition'],r['problem'],r['hypothesis'])==(partition,problem,h))
    decisions={}
    for problem in (3,4):
        c=row('confirmation',problem); s=row('stress',problem)
        retained=selection['choices'][str(problem)]=='champion'
        conditions={
            'confirmation_all_complete':c['candidate']['complete']==c['candidate']['n'],
            'stress_all_complete':s['candidate']['complete']==s['candidate']['n'],
            'both_H_mean_noninferior':all(row('confirmation',problem,h)['mean_delta_s']<=0 for h in ('H1','H2')),
            'primary_ci975_below_zero':retained or c['bootstrap']['ci975_s'][1]<0}
        passed=all(conditions.values())
        decisions[str(problem)]={'passed':passed,'conditions':conditions,
             'selected':selection['choices'][str(problem)],
             'recommended':selection['choices'][str(problem)] if passed else 'champion',
             'confirmation':c,'stress':s}
    verdict={'passed':all(d['passed'] for d in decisions.values()),'per_problem':decisions,
             'selection_sha256':selection['selection_sha256'],
             'acceptance_sha256':e.digest(acceptance),'independent_audit_sha256':e.digest(audit),
             'statement':'Synthetic validation only; no claim of global optimality or new official score.'}
    e.write(ROOT/'reports/validation_verdict.json',verdict)
    stages=['round1','round2','round3','full','confirmation','stress']
    summaries={stage:e.read(ROOT/'runs'/stage/'summary.json') for stage in stages}
    total=sum(s['runs'] for s in summaries.values()); complete=sum(s['all_complete'] for s in summaries.values())
    lines=['# 本轮算法迭代结果','','本轮针对问题3、4，结合此前官方验证、5800次历史本地迭代及几何旁支结论，重新以最新L1强冠军为对照。',
           '',f'完成三轮筛选、360世界全量选优、240新合成世界确认和48压力世界验证，共 **{total} 次全任务回放，{complete} 次审计全清**。主实验覆盖648个不同世界；实现及交付工具的fixture测试另计。',
           '',f'独立审计：通过，{audit["issue_count"]}项问题。确认采用标准：{"两题均通过" if verdict["passed"] else "有题目未通过，按下表保留原冠军"}。',
           '', '## 最终推荐参数','','| 题号 | 推荐规格 | 局部测向上限 | 七点环半径 | 绕行阈值 |','|---|---|---:|---:|---:|']
    for problem in (3,4):
        name=decisions[str(problem)]['recommended']; spec=selection['validation_specs'][name]
        ring=str(spec.get('ring',1558.8457))+' m' if problem==3 else '原定向覆盖'
        lines.append(f'| {problem} | {name} | {spec.get("limit",1)} | {ring} | {spec.get("config",{}).get("joint_detour_m",800)} m |')
    lines.extend(['','获选新方案均使用连续凸域的紧致光学覆盖，只重排尚未访问的覆盖站点，并按可行域最近点估计服务绕行。问题4保留NoSignal固定隐变量处理。概率清除排序、信息增益和双步规划未进入本轮最终推荐。',
                  '', '## 用户360场景：全量开发结果','','每题180场，H1/H2各90。以下百分比是均值之比的降低幅度。开发集参与了选优，因此不是独立确认。',
                  '', '| 题号 | 旧L5均值/s | 强冠军均值/s | 获选均值/s | 相对强冠军降低 | 相对旧L5降低 |','|---|---:|---:|---:|---:|---:|'])
    for problem in (3,4):
        r=row('development',problem); b=r['baseline']['mean_penalized_T_s']; c=r['candidate']['mean_penalized_T_s']
        legacy=next(g['mean_loss_s'] for g in summaries['full']['groups'] if (g['problem'],g['hypothesis'],g['variant'])==(problem,'design_mixture','legacy_L5'))
        lines.append(f'| {problem} | {legacy:.2f} | {b:.2f} | {c:.2f} | {r["ratio_of_means_reduction_pct"]:.2f}% | {100*(1-c/legacy):.2f}% |')
    lines.extend(['','## 240个新合成场景：冻结后的确认结果','','每题120场，H1/H2各60；代码与每题一个参数组合在运行前冻结。区间来自独立10000次整场配对bootstrap，两题主要比较采用97.5%区间。',
                  '', '| 题号 | 强冠军均值/s | 获选均值/s | 降低幅度 | 配对差97.5%区间/s | 强冠军→获选T/N | 获选全清 |','|---|---:|---:|---:|---|---|---|'])
    for problem in (3,4):
        r=row('confirmation',problem); b,c=r['baseline'],r['candidate']; ci=r['bootstrap']['ci975_s']
        lines.append(f'| {problem} | {b["mean_penalized_T_s"]:.2f} | {c["mean_penalized_T_s"]:.2f} | {r["ratio_of_means_reduction_pct"]:.2f}% | [{ci[0]:.2f}, {ci[1]:.2f}] | {b["mean_penalized_T_per_N_s"]:.2f}→{c["mean_penalized_T_per_N_s"]:.2f} | {c["complete"]}/{c["n"]} |')
    lines.extend(['','| 题号 | H1平均差/s | H2平均差/s | P95：冠军→获选/s | 退化局数 | 最大单场退化/s |','|---|---:|---:|---|---:|---:|'])
    for problem in (3,4):
        r=row('confirmation',problem); b,c=r['baseline'],r['candidate']
        lines.append(f'| {problem} | {row("confirmation",problem,"H1")["mean_delta_s"]:.2f} | {row("confirmation",problem,"H2")["mean_delta_s"]:.2f} | {b["p95_success_T_s"]:.2f}→{c["p95_success_T_s"]:.2f} | {r["regressions"]}/{c["n"]} | {r["worst_regression_s"]:.2f} |')
    lines.extend(['','## 48个新压力场景','','覆盖边界朝外/切向、最后隐藏源、聚簇、中心与边界、格点邻域，混合半径端点和固定误差机制。该人为压力混合的均值不代表正式题目分布。',
                  '', '| 题号 | 强冠军均值/s | 获选均值/s | 均值降低幅度 | 获选全清 | P95：冠军→获选/s | 退化局数 | 最大单场退化/s |','|---|---:|---:|---:|---|---|---:|---:|'])
    for problem in (3,4):
        r=row('stress',problem); b,c=r['baseline'],r['candidate']
        lines.append(f'| {problem} | {b["mean_penalized_T_s"]:.2f} | {c["mean_penalized_T_s"]:.2f} | {r["ratio_of_means_reduction_pct"]:.2f}% | {c["complete"]}/{c["n"]} | {b["p95_success_T_s"]:.2f}→{c["p95_success_T_s"]:.2f} | {r["regressions"]}/{c["n"]} | {r["worst_regression_s"]:.2f} |')
    lines.extend(['','压力集问题3的P95高于原冠军：平均收益并不意味着尾部全面占优。故本轮推荐针对登记平均成本目标，不宣称每一局或最坏情形耗时都更优。'])
    lines.extend(['','## 为什么有效','','目标函数是移动距离/5、测量、换频和清除收费之和，而非定位误差本身。问题3在连续覆盖证明成立的范围内缩短巡访环；两题依据当前可行域构造更紧的清除覆盖，并减少服务顺序和覆盖路线之间的额外绕行。问题4的第二次测向可用新增测量成本换更少光学尝试。收益由完整任务回放判定，不能简单相加单模块改善。',
                  '', '数学与安全边界见 [第一性原理推导](docs/first_principles.md)；逐项成本与退化轨迹见 [独立审计](reports/independent_audit.md) 及同名JSON；三轮开发筛选见 [筛选记录](reports/screen_summary.md)。',
                  '', '## 使用与结果边界','','直接运行、360场景批测及下一轮迭代命令见 [README.md](README.md)。代码和参数已冻结，单场景入口为 `solve_local.py`；修改算法后应另建campaign并生成新确认世界。',
                  '', '这是本轮候选集合与登记分布下的经验最优，未证明全局最优。输入360世界及新增世界都是根据官方观测拟合的合成场景，不等同于新增官方测试成绩。平均改善不表示每场都更快；上表保留全部单场退化。此前已使用的“confirmation”标签本轮全部按开发数据处理，新的288世界与历史1800世界不重叠。',
                  '', '最终只读审计覆盖full、confirmation和stress共2556次执行的来源、请求/决策轨迹、固定误差反馈、虚拟计费及停止账本；前三轮4020次开发运行另由每场运行器审计。',
                  '', '来源：`campaign/protocol.json`、`frozen_selection.json`、`validation_acceptance.json`、`validation_freeze_record.json`、`reports/independent_audit.json`、`validation_verdict.json`。'])
    (ROOT/'RESULTS.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'passed':verdict['passed'],'runs':total,'complete':complete,'recommended':{p:d['recommended'] for p,d in decisions.items()}}))


if __name__=='__main__':main()
