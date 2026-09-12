import statistics,random

def percentile(values,q):
    a=sorted(values);x=(len(a)-1)*q;i=int(x)
    return a[i]+(a[min(i+1,len(a)-1)]-a[i])*(x-i)

def paired(rows,base,problem,mode):
    items=[r for r in rows if r['problem']==problem and r['error_mode']==mode]
    if not items:return None
    groups=[];differences=[]
    for n in range(10,17):
        g=[r for r in items if r['source_total']==n]
        if not g:continue
        b=statistics.mean(base[r['world_key']]['loss_s'] for r in g)
        c=statistics.mean(r['loss_s'] for r in g)
        ds=[(r['loss_s']-base[r['world_key']]['loss_s'])/n for r in g]
        groups.append(dict(N=n,cases=len(g),baseline_T=b,T=c,gain_pct=100*(1-c/b)))
        differences.append(ds)
    bmacro=statistics.mean(g['baseline_T']/g['N'] for g in groups)
    cmacro=statistics.mean(g['T']/g['N'] for g in groups)
    rng=random.Random(93241+problem);boot=[]
    for _ in range(3000):
        boot.append(statistics.mean(statistics.mean(rng.choices(g,k=len(g))) for g in differences))
    lower,upper=percentile(boot,.025),percentile(boot,.975)
    low=[g for g in groups if g['N']<16]
    delta=[r['loss_s']-base[r['world_key']]['loss_s'] for r in items]
    return dict(problem=problem,H='H1' if mode=='correlated' else 'H2',cases=len(items),
      complete=sum(r['complete'] for r in items),baseline_mean_T=statistics.mean(base[r['world_key']]['loss_s'] for r in items),
      mean_T=statistics.mean(r['loss_s'] for r in items),macro_gain_pct=100*(1-cmacro/bmacro),
      macro_delta_T_per_N=cmacro-bmacro,CI95_delta_T_per_N=[lower,upper],
      CI95_gain_pct=[-100*upper/bmacro,-100*lower/bmacro],
      low_N_gain_pct=100*(1-statistics.mean(g['T']/g['N'] for g in low)/statistics.mean(g['baseline_T']/g['N'] for g in low)),
      N16_gain_pct=next((g['gain_pct'] for g in groups if g['N']==16),None),
      slower=sum(d>1e-5 for d in delta),max_regression_s=max(delta),mean_delta_s=statistics.mean(delta),
      baseline_p95_T_per_N=percentile([base[r['world_key']]['loss_s']/r['source_total'] for r in items],.95),
      p95_T_per_N=percentile([r['loss_s']/r['source_total'] for r in items],.95),by_N=groups)
