from pathlib import Path
import math,json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
ROOT=Path(__file__).resolve().parent
font=FontProperties(fname=r'C:\Windows\Fonts\msyh.ttc')
old=ROOT.parent/'log_guided_iteration_20260912/runs/official_log_guided_10_each_20260912/cases/log-next-p4-004/result.json'
proof=json.loads(old.read_text(encoding='utf-8'))['stop_evidence']['refined_coverage_certificate']
rows=json.loads((ROOT/'reports/geometry_cover_candidates.json').read_text(encoding='utf-8'))
new=min(rows,key=lambda r:r['full_unknown_cost_s'])
fig,axes=plt.subplots(1,2,figsize=(12,5.5))
for ax,points,length,title in [(axes[0],proof['stations'],proof['open_route_length_m'],'原三角网：25站'),(axes[1],new['points'],new['length_m'],'双环覆盖：25站')]:
    ax.add_patch(plt.Circle((0,0),1800,facecolor='#e8f0fb',edgecolor='#8ca9cf',lw=1.3))
    route=[(0,0)]+points
    ax.plot([p[0] for p in route],[p[1] for p in route],color='#2d6fbb',lw=1.3,alpha=.8)
    ax.scatter([p[0] for p in points],[p[1] for p in points],s=28,color='#153f71',zorder=3)
    ax.scatter([0],[0],marker='*',s=120,color='#d68526',zorder=4)
    ax.set_aspect('equal');ax.set_xlim(-2700,2700);ax.set_ylim(-2500,2700)
    ax.set_title(f'{title}\n固定搜索路程 {length/1000:.2f} km',fontproperties=font,fontsize=14)
    ax.set_xlabel('x / m');ax.set_ylabel('y / m');ax.grid(alpha=.15)
    ax.spines[['top','right']].set_visible(False)
fig.suptitle('同一1800米圆域、最小1000米接收半径、任意定向朝向',fontproperties=font,fontsize=15)
fig.tight_layout();fig.savefig(ROOT/'reports/geometry_comparison.png',dpi=170)
print(ROOT/'reports/geometry_comparison.png')
