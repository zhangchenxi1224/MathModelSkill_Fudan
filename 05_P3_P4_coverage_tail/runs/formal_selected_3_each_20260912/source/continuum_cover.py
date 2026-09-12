"""Adaptive sufficient certificate from actual no-signal measurement positions.

For each position cell, retain stations that are within 1000 m of EVERY cell
vertex. P3 requires one such station. P4 requires their convex hull to contain
the entire cell strictly: every possible source/antenna orientation then faces
at least one in-range station. Unproved cells are subdivided, never discarded.
"""
import math
from functools import lru_cache
from shapely.geometry import Polygon, box
from feedback.region import convex_hull


def inside(hull, poly):
    if len(hull)<3:return False
    for a,b in zip(hull,hull[1:]+hull[:1]):
        dx,dy=b[0]-a[0],b[1]-a[1]
        margin=1e-7*math.hypot(dx,dy)
        if any(dx*(y-a[1])-dy*(x-a[0])<=margin for x,y in poly):return False
    return True


@lru_cache(None)
def domain_cells():
    # Circumscribed polygon contains the true circular domain (outward rounding).
    n=2048;r=(1800.+1e-7)/math.cos(math.pi/n)
    domain=Polygon([(r*math.cos(2*math.pi*i/n),r*math.sin(2*math.pi*i/n)) for i in range(n)])
    cells=[]
    for i in range(-9,9):
        for j in range(-9,9):
            p=box(i*200,j*200,(i+1)*200,(j+1)*200).intersection(domain)
            if not p.is_empty and p.area>0:cells.append(tuple(p.exterior.coords)[:-1])
    return tuple(cells)


class ContinuumCover:
    def __init__(self,problem):
        self.problem=problem;self.cache={};self.visited=0

    def prove(self,points):
        key=tuple(sorted(set(map(tuple,points))))
        if key in self.cache:return self.cache[key]
        if len(key)<(4 if self.problem==3 else 10):return False
        visited=0
        def visit(poly,depth):
            nonlocal visited
            visited+=1
            # Bounds the whole convex cell; strict margins cover roundoff.
            eligible=[q for q in key if all((q[0]-x)**2+(q[1]-y)**2<(1000.-1e-6)**2 for x,y in poly)]
            if self.problem==3 and eligible:return True
            if self.problem==4 and inside(convex_hull(eligible),list(poly)):return True
            if depth>=12:return False
            xs=[p[0] for p in poly];ys=[p[1] for p in poly]
            x0,x1,y0,y1=min(xs),max(xs),min(ys),max(ys)
            if max(x1-x0,y1-y0)<1.:return False
            shape=Polygon(poly)
            if x1-x0>=y1-y0:
                mid=(x0+x1)/2;rects=[box(x0,y0,mid,y1),box(mid,y0,x1,y1)]
            else:
                mid=(y0+y1)/2;rects=[box(x0,y0,x1,mid),box(x0,mid,x1,y1)]
            for rect in rects:
                part=shape.intersection(rect)
                if part.is_empty or part.area<=0:continue
                if not visit(tuple(part.exterior.coords)[:-1],depth+1):return False
            return True
        result=all(visit(c,0) for c in domain_cells())
        self.visited+=visited
        self.cache[key]=result
        return result
