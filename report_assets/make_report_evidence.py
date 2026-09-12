from pathlib import Path
import sys,json,statistics
WORK=Path(__file__).resolve().parent
sys.path.insert(0,str(WORK/'python_deps'))
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
ROOT=Path(__file__).resolve().parents[1]/'01_latest_algorithm'
OUT=Path(__file__).resolve().parents[1]
FIG=OUT/'report_assets';FIG.mkdir(parents=True,exist_ok=True)
font_manager.fontManager.addfont('C:/Windows/Fonts/msyh.ttc')
plt.rcParams.update({'font.family':'Microsoft YaHei','font.size':10,'axes.unicode_minus':False,'axes.spines.top':False,'axes.spines.right':False,'savefig.dpi':200})
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
pairs={d:read(ROOT/f'reports/final_{d}/paired_cases.json') for d in ['new240','core960']}
results={d:read(ROOT/f'reports/final_{d}/results.json') for d in pairs}
stats=[]
for d in pairs:
    for p in [3,4]:
        a=[r for r in pairs[d] if r['problem']==p]
        runs=[r for r in results[d] if r['problem']==p]
        b=np.array([r['baseline_T_s'] for r in a]);c=np.array([r['candidate_T_s'] for r in a]);delta=c-b
        stats.append(dict(dataset=d,problem=p,n=len(a),base=float(b.mean()),candidate=float(c.mean()),gain_pct=float((b.mean()-c.mean())/b.mean()*100),base_p95=float(np.quantile(b,.95)),candidate_p95=float(np.quantile(c,.95)),slower=int((delta>1e-6).sum()),max_regression=float(delta.max()),mean_wall=statistics.mean(r['wall_s'] for r in runs),max_wall=max(r['wall_s'] for r in runs),max_abs_timing_residual=max(abs(r.get('timing_residual_s',0)) for r in runs),complete=sum(r['complete'] for r in runs),region_violations=sum(len(r['region_violations']) for r in runs)))
    
for p in [3,4]:
    fig,axs=plt.subplots(1,2,figsize=(9,3.65),layout='constrained')
    for ax,d,label in zip(axs,['new240','core960'],['新240','旧960']):
        a=[r for r in pairs[d] if r['problem']==p]
        b=[r['baseline_T_s'] for r in a];c=[r['candidate_T_s'] for r in a]
        colors=['#bf5145' if y>x else '#287a95' for x,y in zip(b,c)]
        ax.scatter(b,c,c=colors,s=14,alpha=.65,linewidths=0)
        lo=min(b+c)-120;hi=max(b+c)+120;ax.plot([lo,hi],[lo,hi],color='#686868',ls='--',lw=1)
        ax.set(xlim=(lo,hi),ylim=(lo,hi),xlabel='冻结基线总虚拟时间 / 秒',ylabel='改良算法总虚拟时间 / 秒',title=f'P{p} {label}  n={len(a)}')
        ax.grid(alpha=.12)
    fig.savefig(FIG/f'p{p}_paired.png');plt.close(fig)
    fig,ax=plt.subplots(figsize=(8.1,3.1),layout='constrained')
    for d,color,label in [('new240','#287a95','新240'),('core960','#a16b32','旧960')]:
        vals=np.sort([r['delta_T_s'] for r in pairs[d] if r['problem']==p]);ax.step(vals,np.arange(1,len(vals)+1)/len(vals),where='post',label=label,color=color)
    ax.axvline(0,color='#777',ls='--',lw=1);ax.set(xlabel='候选减基线的单局时间差 / 秒',ylabel='累计场景比例',ylim=(0,1.02));ax.legend(loc='lower right');ax.grid(alpha=.15)
    fig.savefig(FIG/f'p{p}_delta_cdf.png');plt.close(fig)

summ=read(ROOT/'reports/final_new240/paired_summary.json')
fig,axs=plt.subplots(1,2,figsize=(9,3.4),layout='constrained')
for ax,p in zip(axs,[3,4]):
    rows=[r for r in summ if r['problem']==p]
    fields=['mean_movement_delta_s','mean_measure_delta_s','mean_switch_delta_s','mean_clear_delta_s']
    x=np.arange(4)
    for j,(r,col) in enumerate(zip(rows,['#287a95','#a16b32'])):
        ax.bar(x+(j-.5)*.35,[r[f] for f in fields],width=.35,color=col,label='H1' if r['error_mode']=='correlated' else 'H2')
    ax.axhline(0,color='#555',lw=.7);ax.set(xticks=x,xticklabels=['移动','检测','换频','清除'],ylabel='候选减基线 / 秒',title=f'P{p} 新240平均成本变化');ax.legend();ax.grid(axis='y',alpha=.12)
fig.savefig(FIG/'cost_decomposition.png');plt.close(fig)

fig,axs=plt.subplots(1,2,figsize=(9,3.3),layout='constrained')
screen=read(ROOT/'reports/new240_screen16/summary.json')
coarse=read(ROOT/'reports/new240_coarse16/summary.json')
for ax,p in zip(axs,[3,4]):
    rows=[r for r in screen if r['problem']==p and r['stratum']=='all']
    by={r['arm']:r for r in rows}
    base=rows[0]['mean_T_s']-rows[0]['mean_paired_difference_s']
    labels=['冻结基线','一步决策','两步前瞻'];vals=[base,by['three_feedback']['mean_T_s'],by['dp_geometric']['mean_T_s']]
    if p==4:
        rr=next(r for r in coarse if r['problem']==p and r['stratum']=='all' and r['arm']=='three_feedback_fast')
        labels+=['一步 深度3'];vals+=[rr['mean_T_s']]
    ax.plot(np.arange(len(vals)),vals,marker='o',color='#287a95');ax.set(xticks=np.arange(len(vals)),xticklabels=labels,ylabel='平均总虚拟时间 / 秒',title=f'P{p} 同8个开发场景的候选比较');ax.grid(alpha=.15)
fig.savefig(FIG/'screen_comparison.png');plt.close(fig)
(FIG/'report_statistics.json').write_text(json.dumps({'groups':stats,'paired_H':{d:read(ROOT/f'reports/final_{d}/paired_summary.json') for d in pairs},'official':read(ROOT/'runs/official_practice_5_each_20260912_1430/report/results.json'),'quantile_method':'numpy linear 95th percentile; descriptive recomputation of existing final records; no new solver runs'},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(stats,ensure_ascii=False,indent=2))
