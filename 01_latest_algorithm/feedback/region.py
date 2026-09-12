"""Conservative cell union: holes, shared radius, and fixed directional orientation.

Cells overapproximate feasible positions. Orientation sectors use outward linear
relaxations, never sampled directions as an exclusion certificate.
"""
import math
from fractions import Fraction
from bsolver.geometry import clip_halfplane as _clip_halfplane, distance_to_polygon, max_distance, contains
from bsolver.knowledge import ChannelKnowledge, InconsistentKnowledge

PAD = 1e-5


def convex_hull(points):
    points = sorted(set(map(tuple, points)))
    if len(points) <= 2:
        return points
    def cross(a, b, c):
        x=(b[0]-a[0])*(c[1]-a[1]); y=(b[1]-a[1])*(c[0]-a[0])
        if abs(x-y)>1e-12*(abs(x)+abs(y)+1.):return x-y
        ax,ay=map(Fraction,a);bx,by=map(Fraction,b);cx,cy=map(Fraction,c)
        return (bx-ax)*(cy-ay)-(by-ay)*(cx-ax)
    lower, upper = [], []
    for p in points:
        while len(lower)>1 and cross(lower[-2],lower[-1],p)<=0:
            lower.pop()
        lower.append(p)
    for p in reversed(points):
        while len(upper)>1 and cross(upper[-2],upper[-1],p)<=0:
            upper.pop()
        upper.append(p)
    return lower[:-1]+upper[:-1]


def clip_halfplane(poly, normal, offset, **kwargs):
    result = _clip_halfplane(poly, normal, offset, **kwargs)
    # Roundoff at an intersection can add a reversed edge ~1e-13 m long.
    # Its spurious inward normal corrupts membership/distance tests.
    # Taking the convex hull preserves every computed vertex.
    return result if result == poly else convex_hull(result)


def closer_positive(poly, positive, negative):
    # |g-negative| > |g-positive|. Close/outward boundary for an outer set.
    normal = (negative[0]-positive[0], negative[1]-positive[1])
    offset = (sum(v*v for v in negative)-sum(v*v for v in positive))/2
    return clip_halfplane(poly, normal, offset+PAD*max(1.,math.hypot(*normal)))


def area(poly):
    return abs(sum(p[0]*q[1]-p[1]*q[0] for p,q in zip(poly,poly[1:]+poly[:1])))/2


class FeedbackKnowledge(ChannelKnowledge):
    def __init__(self, channel, problem, epsilon_deg=1.0051, *, depth=3, sectors=16):
        super().__init__(channel,problem,epsilon_deg)
        self.depth, self.sectors = depth, sectors
        self.cells = [dict(poly=self.hull[:],omni=True,directed=problem==4)]
        self.revision = 0
        self.region_stats = dict(negative_pairs=0, cells=1, excluded_cells=0,
                                 omni_cells=1,directed_cells=int(problem==4))
        self.absence_proof = None

    def observe(self, position, response, coverage_index=None):
        super().observe(position,response,coverage_index)
        self.revision += 1
        self.refresh()

    def record_clear(self, position, response):
        super().record_clear(position,response)
        self.revision += 1
        if self.status != 'cleared':
            self.refresh()

    def feasible_cell(self, poly, positives, negatives):
        if not poly or distance_to_polygon(poly,(0.,0.))>1800.+PAD:
            return False,False
        if any(max_distance(poly,p)<20.-PAD for p in self.failed_clear_positions):
            return False,False
        low = max([1000.] + [distance_to_polygon(poly,p)-PAD for p in positives])
        if low>1500.+PAD:
            return False,False
        omni_poly = poly
        for n in negatives:
            if max_distance(omni_poly,n)<1000.-PAD:
                omni_poly=[]
                break
            for p in positives:
                omni_poly=closer_positive(omni_poly,p,n)
                if not omni_poly:
                    break
            if not omni_poly:
                break
            local_low=max([1000.]+[distance_to_polygon(omni_poly,p)-PAD for p in positives])
            if max_distance(omni_poly,n)<local_low-PAD:
                omni_poly=[]
                break
        if self.problem==3:
            return bool(omni_poly),False
        # At these negative sites every state in this position cell is in range.
        # Thus the same orientation must face all positives and away from these.
        forced=[n for n in negatives if max_distance(poly,n)<low-PAD]
        if not positives and not forced:
            return bool(omni_poly),True
        half=math.pi/self.sectors
        deviation=2*math.sin(half/2)
        for j in range(self.sectors):
            theta=2*math.pi*(j+.5)/self.sectors
            u=(math.cos(theta),math.sin(theta))
            candidate=poly
            for p in positives:
                slack=max_distance(poly,p)*deviation+PAD
                candidate=clip_halfplane(candidate,u,u[0]*p[0]+u[1]*p[1]+slack)
                if not candidate:
                    break
            for n in forced if candidate else []:
                slack=max_distance(poly,n)*deviation+PAD
                candidate=clip_halfplane(candidate,(-u[0],-u[1]),-u[0]*n[0]-u[1]*n[1]+slack)
                if not candidate:
                    break
            if candidate:
                return bool(omni_poly),True
        return bool(omni_poly),False

    def refresh(self):
        positives=list(self.positive_positions)
        negatives=[o.position for o in self.observations if o.result=='no_signal']
        poly=convex_hull(self.hull)
        if self.problem==3:
            for n in negatives:
                for p in positives:
                    poly=closer_positive(poly,p,n)
        kept=[]
        excluded=0
        def visit(poly,level):
            nonlocal excluded
            omni,directed=self.feasible_cell(poly,positives,negatives)
            if not (omni or directed):
                excluded+=1
                return
            xs=[p[0] for p in poly]; ys=[p[1] for p in poly]
            if level>=self.depth or max(max(xs)-min(xs),max(ys)-min(ys))<5:
                kept.append(dict(poly=poly,omni=omni,directed=directed))
                return
            # Binary spatial subdivision: depth 6 => at most 64 outer cells.
            axis=int(max(ys)-min(ys)>max(xs)-min(xs))
            mid=(min(ys)+max(ys))/2 if axis else (min(xs)+max(xs))/2
            normal=(0.,1.) if axis else (1.,0.)
            a=clip_halfplane(poly,normal,mid+PAD)
            b=clip_halfplane(poly,(-normal[0],-normal[1]),-mid+PAD)
            if a:visit(a,level+1)
            if b:visit(b,level+1)
        if poly:visit(poly,0)
        self.cells=kept
        self.region_stats=dict(negative_pairs=len(positives)*len(negatives),cells=len(kept),
             excluded_cells=excluded,omni_cells=sum(c['omni'] for c in kept),
             directed_cells=sum(c['directed'] for c in kept))
        if kept:
            self.hull=convex_hull(p for c in kept for p in c['poly']) if excluded else poly
        elif positives:
            raise InconsistentKnowledge('all-feedback region contradicts a detected source')
        else:
            self.status='absent'
            self.absence_proof=dict(kind='empty_conservative_cell_union',
                observations=len(self.observations),sectors=self.sectors,depth=self.depth)

    def clearance_possible(self, point):
        return any(distance_to_polygon(c['poly'],point)<=20.+PAD for c in self.cells)

    def region_contains(self, point):
        return any(contains(c['poly'],point,tol=1e-4) for c in self.cells)

    def snapshot(self):
        value=super().snapshot()
        value.update(region=self.region_stats,absence_proof=self.absence_proof,
                     region_cells=[c['poly'] for c in self.cells])
        return value
