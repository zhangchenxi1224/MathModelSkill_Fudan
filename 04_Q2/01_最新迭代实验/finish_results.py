"""Summarize completed experiments and verify only the compared finalists."""
from pathlib import Path
import json
import math

from model import FirstContext
from evaluator import evaluate

ROOT=Path(__file__).resolve().parent


def main():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    output=ROOT/'results/final_comparison'
    output.mkdir(parents=True,exist_ok=True)
    comparisons=[]
    for run in ('mainline_v1','clipped_geometry_v1'):
        if not (ROOT/'results'/run/'completion.json').exists(): raise RuntimeError('unfinished input')
        for file in sorted((ROOT/'results'/run/'cases').glob('*.json')):
            source=json.loads(file.read_text(encoding='utf-8'))
            info=source['context']
            context=FirstContext(tuple(info['station']),info['report_deg'])
            row={'context_id':info['id'],'run':run,'modes':{}}
            for mode in ('old_precision','open_search'):
                q=source['comparisons'][mode]['point_local']
                result=evaluate(context,q,tolerance=.2,max_splits=450,verify=True)
                row['modes'][mode]={k:result[k] for k in ('point_local','point_global','lower_m','upper_m','gap_m','worst_witness')}
                row['modes'][mode]['cover_checks']=result['cover_verification']
            row['strict_numeric_improvement']=row['modes']['open_search']['upper_m']<row['modes']['old_precision']['lower_m']
            comparisons.append(row)
            print(json.dumps({'verified':info['id'],'old_interval':[row['modes']['old_precision']['lower_m'],row['modes']['old_precision']['upper_m']],
                              'new_interval':[row['modes']['open_search']['lower_m'],row['modes']['open_search']['upper_m']],
                              'strict_numeric_improvement':row['strict_numeric_improvement']}),flush=True)
    (output/'verified_comparison.json').write_text(json.dumps(comparisons,ensure_ascii=False,indent=2),encoding='utf-8')
    secondary=json.loads((ROOT/'results/mainline_v1/summary.json').read_text(encoding='utf-8'))
    lines=['# 第二问三反馈方案：实验结果','',
           '主指标为同一首次观测下、所有合法第二反馈的最坏MEC半径。下面的上界经过独立矩形覆盖复核；下界来自合法共同反馈源点组。上下界差距不是全局最优性差距。','',
           '|首次场景|原精度方案最坏半径区间/m|新搜索方案最坏半径区间/m|区间严格分离|',
           '|---|---:|---:|---|']
    for row in comparisons:
        a,b=row['modes']['old_precision'],row['modes']['open_search']
        # Round lower downward and upper upward so displayed intervals enclose.
        interval=lambda r:f"[{math.floor(r['lower_m']*100)/100:.2f}, {math.ceil(r['upper_m']*100)/100:.2f}]"
        lines.append(f"|{row['context_id']}|{interval(a)}|{interval(b)}|{'是' if row['strict_numeric_improvement'] else '否'}|")
    lines+=['','center与edge的完整扇环在几何上等价，属于平移旋转回归，不算两种独立困难几何。其余官方上下文是探索性的公共首次几何样本，不是新测点的官方实测。','',
            f"合成对照另有 {secondary['worlds']} 个物理/误差场景、{secondary['paired_rows']} 条配对记录，全部真值被相应输出圆覆盖。",'',
            '|策略|平均半径上界/m|样本最大半径上界/m|无信号次数|',
            '|---|---:|---:|---:|']
    for mode,r in secondary['synthetic_secondary'].items():
        lines.append(f"|{mode}|{r['mean_radius_upper_m']:.2f}|{r['sample_max_radius_upper_m']:.2f}|{r['no_signal_count']}|")
    lines+=['','新方案优化的是最坏情况；当前合成设计中，平均半径略升，不能声称所有指标同时改善。无信号反馈已纳入定位计算，不记作失败。','',
            '有限预算搜索尚未给出连续全域最优证明。算法仅靠首次公开位置、方向选点，没有使用真实源位置、未来报告或清除坐标。',
            '本轮新增官方调用为0。既有真实首次记录用于模型评价；真实第二反馈和实际官方定位误差仍需后续执行新测点才可获取。','',
            '已完成：圆弧三反馈评价、合法量化报告、自适应最坏角度评价、DIRECT/DE与局部搜索、统一基线对照。当前阶段聚焦效果验证，未继续开展空间分支定界证明。','',
            '完整记录：`verified_comparison.json`、`../mainline_v1/`与`../clipped_geometry_v1/`。']
    (output/'RESULTS.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    labels=[r['context_id'].replace('r1-p3-survey-','P3 ').replace('-ch',' / ch') for r in comparisons]
    ys=np.arange(len(comparisons))
    fig,ax=plt.subplots(figsize=(10,5.8))
    for mode,label,color,offset in [('old_precision','Original precision','#A96030',-.12),('open_search','Three-feedback search','#267D77',.12)]:
        lows=np.array([r['modes'][mode]['lower_m'] for r in comparisons])
        highs=np.array([r['modes'][mode]['upper_m'] for r in comparisons])
        mid=(lows+highs)/2
        ax.errorbar(mid,ys+offset,xerr=np.array([mid-lows,highs-mid]),fmt='o',capsize=4,label=label,color=color)
    ax.set_yticks(ys,labels); ax.invert_yaxis(); ax.set_xlabel('Worst-feedback MEC radius interval (m)')
    ax.set_title('Q2: paired comparison under one three-feedback evaluator')
    ax.grid(axis='x',alpha=.2); ax.legend(); fig.tight_layout()
    fig.savefig(output/'comparison.png',dpi=180)
    fig.savefig(output/'comparison.pdf')
    plt.close(fig)
    print(json.dumps({'complete':True,'contexts':len(comparisons),
                      'strict_numeric_improvements':sum(r['strict_numeric_improvement'] for r in comparisons)}),flush=True)


if __name__=='__main__': main()
