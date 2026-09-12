"""Plots of actual audited official practice cases, with protocols kept apart."""
from pathlib import Path
import argparse
from collections import Counter
import datetime as dt
import json
import math
import sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=Path('results/round1'))
    args=parser.parse_args()
    out=args.input/'figures'
    out.mkdir(parents=True,exist_ok=True)
    rows=[]
    for path in sorted((args.input/'cases').glob('*/post_exit_audit.json')):
        audit=json.loads(path.read_text(encoding='utf-8'))
        result=json.loads((path.parent/'result.json').read_text(encoding='utf-8'))
        rows.append(dict(audit,result=result))
    if not rows:
        raise SystemExit('No audited official cases; no synthetic placeholder plot generated')
    colors={'move':'#476b8d','switch':'#e5a34b','measure':'#63a59d','clear':'#bc6a70'}
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'figure.dpi':130})
    fig,axes=plt.subplots(1,2,figsize=(13,4.8),gridspec_kw={'width_ratios':[1,2.2]})
    for p,ax in zip((3,4),axes):
        selected=[r for r in rows if r['problem']==p and r['source_total_post_exit'] is not None and r['directional_total_post_exit'] is not None]
        count=Counter((r['source_total_post_exit'],r['directional_total_post_exit']) for r in selected)
        if p==3:
            ax.bar(range(10,17),[count[n,0] for n in range(10,17)],color='#476b8d')
            ax.set(xticks=range(10,17),xlabel='Total sources N',ylabel='Audited cases')
        else:
            data=np.full((7,17),np.nan)
            for n in range(10,17):
                for nd in range(n+1): data[n-10,nd]=count[n,nd]
            im=ax.imshow(data,origin='lower',aspect='auto',cmap='Blues',vmin=0)
            for n in range(10,17):
                for nd in range(n+1):
                    if count[n,nd]:ax.text(nd,n-10,str(count[n,nd]),ha='center',va='center',fontsize=9)
            ax.set(xticks=range(17),yticks=range(7),yticklabels=range(10,17),xlabel='Directional sources Ndir',ylabel='Total sources N')
            fig.colorbar(im,ax=ax,label='Audited cases',shrink=.8)
        ax.set_title(f'P{p}: observed JOINT composition, n={len(selected)}')
    fig.suptitle(f'Official practice collection: {len(rows)}/160 audited; descriptive data, not formal results')
    fig.tight_layout()
    fig.savefig(out/'joint_composition.png',bbox_inches='tight')
    plt.close(fig)

    groups=[(3,'baseline'),(3,'survey'),(4,'baseline'),(4,'survey')]
    labels=[f'P{p}\n{protocol}' for p,protocol in groups]
    fig,axes=plt.subplots(1,2,figsize=(12,4.8))
    data=[]
    for i,(p,protocol) in enumerate(groups):
        selected=[r for r in rows if (r['problem'],r['protocol'])==(p,protocol)]
        complete=[r for r in selected if r['status']=='complete']
        data.append([r['total_virtual_time_s'] for r in complete])
        axes[0].text(i+1,0,f'{len(complete)}/{len(selected)} complete',rotation=0,ha='center',va='bottom',fontsize=8)
        bottom=0.
        for name,values in [('move',[r['result']['walk_distance_m']/5 for r in complete]),
                            ('switch',[r['result']['switches'] for r in complete]),
                            ('measure',[r['result']['measures']*5 for r in complete]),
                            ('clear',[r['result']['clear_attempts']*3+r['result']['clear_successes']*2 for r in complete])]:
            mean=float(np.mean(values)) if values else 0.
            axes[1].bar(i,mean,bottom=bottom,color=colors[name],label=name if i==0 else None)
            bottom+=mean
    axes[0].boxplot([values or [math.nan] for values in data],tick_labels=labels,showmeans=True)
    axes[0].set(ylabel='Total virtual seconds (complete cases only)',title='Collection protocols remain separate')
    axes[1].set(xticks=range(4),xticklabels=labels,ylabel='Mean virtual seconds (complete cases only)',title='Actual action cost decomposition')
    axes[1].legend(ncol=4,fontsize=9)
    fig.suptitle(f'Official practice, {len(rows)} audited cases; failed effort is never a completion time')
    fig.tight_layout()
    fig.savefig(out/'collection_time_and_cost.png',bbox_inches='tight')
    plt.close(fig)
    manifest={'generated_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'audited_cases':len(rows),
              'complete':sum(r['status']=='complete' for r in rows),
              'incomplete_case_ids':[r['case_id'] for r in rows if r['status']!='complete'],
              'source':'post_exit_audit.json and untouched per-case result.json',
              'scope':'official practice only; no formal results',
              'files':['joint_composition.png','collection_time_and_cost.png']}
    (out/'plot_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False))


if __name__=='__main__': main()
