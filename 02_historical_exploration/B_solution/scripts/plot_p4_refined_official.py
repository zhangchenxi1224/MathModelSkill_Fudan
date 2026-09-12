"""Static figures for independent official practice arms; reads saved evidence only."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import statistics
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]


def main(folder):
    source=folder/'report/results.json'
    data=json.loads(source.read_text('utf-8'))
    arms=('current','combined_cover')
    names=('Previous candidate','New combined')
    colors=('#667C96','#05836E')
    rows={a:[r for r in data['cases'] if r['stage']=='validation' and r['arm']==a] for a in arms}
    fig,axes=plt.subplots(2,2,figsize=(11,8),constrained_layout=True)
    rng=random.Random(113711)
    specs=[(axes[0,0],'penalized_time_s','Total virtual time per case (s)',True),
           (axes[0,1],'average_clear_time_s','T / N per completed case (s / source)',False),
           (axes[1,1],'post_clear_stop_tail_s','Post-clear completion-check tail (s)',False)]
    for ax,field,label,all_attempts in specs:
        for i,arm in enumerate(arms):
            values=[r[field] for r in rows[arm] if r[field] is not None]
            xs=[i+rng.uniform(-.15,.15) for _ in values]
            ax.scatter(xs,values,color=colors[i],alpha=.65,s=26)
            avg=statistics.fmean(values) if values else 0
            ax.hlines(avg,i-.25,i+.25,color=colors[i],linewidth=3)
            ax.text(i+.27,avg,f'{avg:,.1f}',va='center',fontsize=9,color=colors[i])
        ax.set_xticks((0,1),names)
        ax.set_xlim(-.45,1.65)
        ax.set_ylabel(label)
        ax.grid(axis='y',alpha=.15)
        ax.set_ylim(bottom=0)
    axes[0,0].set_title('30 independent new cases per arm\nDots = cases; lines = means')
    axes[0,1].set_title('Diagnostic per-source time')
    axes[0,1].axhline(900,color='#C18B35',linestyle='--',lw=1,label='900 s / source')
    axes[0,1].axhline(1000,color='#B75750',linestyle=':',lw=1,label='1000 s / source')
    axes[0,1].legend(fontsize=8,loc='lower left')
    ax=axes[1,0]
    bottom=[0.,0.]
    components=[('movement_s','Movement','#6C91AC'),('measurement_s','Measurements','#74B5A3'),
        ('switch_s','Channel switches','#C7B475'),('optical_s','Optical attempts','#B08896'),('laser_s','Success surcharge','#8B8EA7')]
    for key,name,color in components:
        values=[statistics.fmean(r[key] for r in rows[a]) for a in arms]
        ax.bar((0,1),values,bottom=bottom,label=name,color=color,width=.55)
        bottom=[a+b for a,b in zip(bottom,values)]
    ax.set_xticks((0,1),names)
    ax.set_ylabel('Mean actual action cost (virtual s)')
    ax.set_title('Executed cost decomposition')
    ax.legend(fontsize=8,loc='upper right')
    ax.set_ylim(0,max(bottom)*1.25)
    ax.grid(axis='y',alpha=.15)
    axes[1,1].set_title('Retrospective diagnostic; never an early-exit rule')
    completed=[sum(r['complete'] for r in rows[a]) for a in arms]
    fig.suptitle(f'Official P4 practice: frozen strategy transfer validation\nComplete: {completed[0]}/30 vs {completed[1]}/30; 2 engineering pilots excluded; formal tests: 0',fontsize=13)
    out=folder/'report'
    fig.savefig(out/'official_results.png',dpi=180)
    fig.savefig(out/'official_results.pdf')
    plt.close(fig)
    (out/'figure_sources.json').write_text(json.dumps({'input':str(source),
        'input_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'scope':'all independent validation cases; unpaired; pilots excluded'},indent=2),encoding='utf-8')
    print(str(out/'official_results.png'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--round',type=Path,default=ROOT/'results/p4_refined_official')
    a=p.parse_args()
    main(a.round.resolve())
