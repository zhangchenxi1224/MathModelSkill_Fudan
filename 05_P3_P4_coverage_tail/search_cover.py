"""Geometry-only route experiment, independent of hidden source layouts."""
import math,time
from pathlib import Path
import run
from continuum_cover import ContinuumCover
from bsolver.coverage import order_route,route_length

def main():
    root=Path(__file__).resolve().parent;checker=ContinuumCover(4);rows=[];start=time.monotonic()
    for outer_n,inner_n in [(12,6),(12,7),(12,8),(13,7),(14,7),(14,8),(15,8),(16,8)]:
        for outer_r in [1800/math.cos(math.pi/outer_n)+10,1950,2050]:
            for inner_r in [850,950,1050,1150]:
                for offset in [0,math.pi/inner_n]:
                    points=[(0.,0.)]+[(inner_r*math.cos(2*math.pi*i/inner_n+offset),inner_r*math.sin(2*math.pi*i/inner_n+offset)) for i in range(inner_n)]
                    points += [(outer_r*math.cos(2*math.pi*i/outer_n),outer_r*math.sin(2*math.pi*i/outer_n)) for i in range(outer_n)]
                    ok=checker.prove(points)
                    if ok:
                        route=order_route(points,(0.,0.));length=route_length(route,(0.,0.))
                        row=dict(outer_n=outer_n,inner_n=inner_n,outer_r=outer_r,inner_r=inner_r,offset=offset,
                                 stations=len(points),length_m=length,full_unknown_cost_s=length/5+120*len(points),points=route)
                        rows.append(row);run.write(root/'reports/geometry_cover_candidates.json',rows)
                        print({k:v for k,v in row.items() if k!='points'},flush=True)
    run.write(root/'reports/geometry_search.json',dict(tested=192,certified=len(rows),elapsed_s=time.monotonic()-start,
              selection='geometry only, no generated or official hidden world was used'))
    print('finished',len(rows),'seconds',time.monotonic()-start,flush=True)

if __name__=='__main__':main()
