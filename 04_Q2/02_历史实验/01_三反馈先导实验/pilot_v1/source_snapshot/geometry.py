"""Circular-arc arrangements and independent interval cover verification.

Circle boundaries stay circular. Open inequalities are relaxed only for upper
bounds; every lower witness must pass the original inequalities with margin.
The arithmetic uses explicit outward allowances, not a formal interval library.
"""
from __future__ import annotations
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations
import math

from vendor.geometry import minimum_enclosing_circle

PAD = 1e-7  # metres; bounded local coordinates are below 10 km
TAU = 2*math.pi


def dist(a,b): return math.hypot(a[0]-b[0], a[1]-b[1])
def dot(a,b): return a[0]*b[0]+a[1]*b[1]
def cross(a,b): return a[0]*b[1]-a[1]*b[0]
def sub(a,b): return a[0]-b[0],a[1]-b[1]
def lerp(a,b,t): return a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1])


@dataclass(frozen=True)
class Circle:
    center: tuple[float,float]
    radius: float
    inside: bool = True
    strict: bool = False

    @property
    def outer_radius(self):
        return self.radius+PAD if self.inside else max(0.,self.radius-PAD)

    def slack(self,p,relaxed=False):
        r=self.outer_radius if relaxed else self.radius
        return r-dist(p,self.center) if self.inside else dist(p,self.center)-r

    def box_possible(self,box):
        low,high=box_distance(self.center,box)
        # Independent outward-rounded distance intervals; equality retained.
        return low<=self.outer_radius+PAD if self.inside else high>=self.outer_radius-PAD


@dataclass(frozen=True)
class Halfplane:
    normal: tuple[float,float]
    offset: float
    strict: bool = False

    def __post_init__(self):
        size=math.hypot(*self.normal)
        if size<=0: raise ValueError('zero normal')
        object.__setattr__(self,'normal',(self.normal[0]/size,self.normal[1]/size))
        object.__setattr__(self,'offset',self.offset/size)

    def slack(self,p,relaxed=False):
        return self.offset+(PAD if relaxed else 0.)-dot(self.normal,p)

    def box_possible(self,box):
        xmin,xmax,ymin,ymax=box
        nx,ny=self.normal
        minimum=nx*(xmin if nx>=0 else xmax)+ny*(ymin if ny>=0 else ymax)
        return minimum<=self.offset+2*PAD


def corners(box):
    a,b,c,d=box
    return [(a,c),(a,d),(b,c),(b,d)]


def box_distance(p,box):
    a,b,c,d=box
    dx=max(a-p[0],0.,p[0]-b)
    dy=max(c-p[1],0.,p[1]-d)
    low=max(0.,math.nextafter(math.hypot(dx,dy)-PAD,-math.inf))
    high=math.nextafter(max(dist(p,v) for v in corners(box))+PAD,math.inf)
    return low,high


def intersections(a,b):
    if isinstance(a,Halfplane) and isinstance(b,Halfplane):
        det=cross(a.normal,b.normal)
        if abs(det)<1e-14: return []
        aa,bb=a.offset+PAD,b.offset+PAD
        return [((aa*b.normal[1]-a.normal[1]*bb)/det,
                 (a.normal[0]*bb-aa*b.normal[0])/det)]
    if isinstance(a,Halfplane): a,b=b,a
    if isinstance(b,Halfplane):
        signed=b.offset+PAD-dot(b.normal,a.center)
        r=a.outer_radius
        if abs(signed)>r+PAD: return []
        h=math.sqrt(max(0.,r*r-signed*signed))
        base=(a.center[0]+signed*b.normal[0],a.center[1]+signed*b.normal[1])
        tangent=(-b.normal[1],b.normal[0])
        return [(base[0]+s*h*tangent[0],base[1]+s*h*tangent[1]) for s in (-1,1)]
    delta=sub(b.center,a.center)
    d=math.hypot(*delta)
    r,t=a.outer_radius,b.outer_radius
    if d<PAD or d>r+t+PAD or d<abs(r-t)-PAD: return []
    along=(r*r-t*t+d*d)/(2*d)
    height=math.sqrt(max(0.,r*r-along*along))
    unit=(delta[0]/d,delta[1]/d)
    base=(a.center[0]+along*unit[0],a.center[1]+along*unit[1])
    return [(base[0]-s*height*unit[1],base[1]+s*height*unit[0]) for s in (-1,1)]


@dataclass
class Arc:
    circle: Circle
    start: float
    end: float

    def point(self,theta):
        a=self.circle
        return a.center[0]+a.outer_radius*math.cos(theta),a.center[1]+a.outer_radius*math.sin(theta)

    def includes(self,theta):
        theta%=TAU
        return self.start-1e-12<=theta<=self.end+1e-12 or self.start-1e-12<=theta+TAU<=self.end+1e-12


class Region:
    def __init__(self,constraints,box):
        self.constraints=tuple(constraints)
        self.box=tuple(box)
        self._boundary=None

    def contains(self,p,relaxed=False,margin=0.):
        return all(c.slack(p,relaxed)>=margin for c in self.constraints)

    def actual_witness(self,p):
        # Strict positive margin also keeps closed-boundary float constructions
        # from accidentally providing an illegal lower witness.
        return self.contains(p,False,3*PAD)

    def possible(self,box):
        return all(c.box_possible(box) for c in self.constraints)

    def boundary(self):
        if self._boundary is not None: return self._boundary
        if self.box[0]>self.box[1] or self.box[2]>self.box[3]:
            self._boundary=([],[]); return self._boundary
        by_boundary=[[] for _ in self.constraints]
        for i,a in enumerate(self.constraints):
            for j in range(i):
                for p in intersections(a,self.constraints[j]):
                    by_boundary[i].append(p); by_boundary[j].append(p)
        points=[]; arcs=[]
        for c,ps in zip(self.constraints,by_boundary):
            points.extend(p for p in ps if self.contains(p,True,-4*PAD))
            if isinstance(c,Circle):
                cuts=sorted({0.,TAU,*[math.atan2(p[1]-c.center[1],p[0]-c.center[0])%TAU for p in ps]})
                for lo,hi in zip(cuts,cuts[1:]):
                    arc=Arc(c,lo,hi)
                    if hi>lo and self.contains(arc.point((lo+hi)/2),True,-PAD):
                        arcs.append(arc)
                        points.extend([arc.point(lo),arc.point(hi),arc.point((lo+hi)/2)])
                        points.extend(arc.point(t) for t in (0.,math.pi/2,math.pi,3*math.pi/2) if arc.includes(t))
            else:
                tangent=(-c.normal[1],c.normal[0])
                ps=sorted(ps,key=lambda p:dot(tangent,p))
                for a,b in zip(ps,ps[1:]):
                    if self.contains(lerp(a,b,.5),True,-PAD): points.extend([a,b,lerp(a,b,.5)])
        unique={tuple(round(v,10) for v in p):p for p in points}
        self._boundary=(list(unique.values()),arcs)
        return self._boundary

    def farthest(self,c):
        points,arcs=self.boundary()
        choices=list(points)
        for arc in arcs:
            d=sub(arc.circle.center,c)
            theta=math.atan2(d[1],d[0])
            if arc.includes(theta): choices.append(arc.point(theta))
        return max(choices,key=lambda p:dist(c,p)) if choices else None

    def feasible_points(self,points):
        result=[]
        vertices,_=self.boundary()
        if not vertices: return result
        average=(sum(p[0] for p in vertices)/len(vertices),sum(p[1] for p in vertices)/len(vertices))
        directions=[(math.cos(k*math.pi/4),math.sin(k*math.pi/4)) for k in range(8)]
        for p in points:
            if self.actual_witness(p): result.append(p); continue
            found=None
            for t in (1e-7,1e-5,.001,.01):
                candidate=lerp(p,average,t)
                if self.actual_witness(candidate): found=candidate; break
            if found is None:
                for scale in (1e-5,.001,.1):
                    for dx,dy in directions:
                        candidate=p[0]+scale*dx,p[1]+scale*dy
                        if self.actual_witness(candidate): found=candidate; break
                    if found is not None: break
            if found is not None: result.append(found)
        return result


def triple_lower(points):
    """Radius of a legal set of <=3 points, rounded DOWN using integers.

    Float screening chooses the witness; its final radius uses exact rational
    squared distances, including the obtuse-triangle case.
    """
    if not points: return 0.,[]
    ps=list(dict.fromkeys(tuple(p) for p in points))
    if len(ps)==1: return 0.,ps
    best=0.; chosen=ps[:1]
    def square(a,b): return (a[0]-b[0])**2+(a[1]-b[1])**2
    for p,q in combinations(ps,2):
        r2=square(p,q)/4
        if r2>best: best,chosen=r2,[p,q]
    # Only a bounded pool is needed for a valid lower bound. More points can
    # improve it, but omitted triples never invalidate the saved witness.
    if len(ps)>18:
        center,_=minimum_enclosing_circle(ps)
        ps=sorted(ps,key=lambda p:dist(center,p),reverse=True)[:18]
    for p,q,r in combinations(ps,3):
        aa,bb,cc=sorted([square(p,q),square(p,r),square(q,r)])
        area2=cross(sub(q,p),sub(r,p))
        value=cc/4 if cc>=aa+bb or abs(area2)<1e-12 else aa*bb*cc/(4*area2*area2)
        if value>best: best,chosen=value,[p,q,r]
    exact=[tuple(Fraction(v) for v in p) for p in chosen]
    if len(exact)==2:
        rr=square(*exact)/4
    elif len(exact)==3:
        p,q,r=exact
        aa,bb,cc=sorted([square(p,q),square(p,r),square(q,r)])
        area2=cross(sub(q,p),sub(r,p))
        rr=cc/4 if cc>=aa+bb or area2==0 else aa*bb*cc/(4*area2*area2)
    else: rr=Fraction(0)
    scale=10**6
    scaled=rr*scale*scale
    lower=math.isqrt(scaled.numerator//scaled.denominator)/scale
    return lower,chosen


def enclosing_circle(region,tolerance=.002,max_steps=30,need_lower=True):
    points,arcs=region.boundary()
    if not points:
        return {'empty_relaxation':True,'center':None,'lower_m':0.,'upper_m':0.,'witness':[]}
    active=list(points)
    for iteration in range(max_steps):
        center,r=minimum_enclosing_circle(active)
        far=region.farthest(center)
        upper=dist(center,far)+10*PAD
        if upper-r<=tolerance: break
        active.append(far)
    # All boundary primitives are covered by the farthest-point oracle. No
    # convexification is applied before the observation constraints.
    support=sorted(active,key=lambda p:dist(center,p),reverse=True)[:18]
    feasible=region.feasible_points(support) if need_lower else []
    lower,witness=triple_lower(feasible)
    return {'empty_relaxation':False,'center':center,'lower_m':lower,'upper_m':upper,
            'witness':witness,'exchange_iterations':iteration+1,'arcs':len(arcs),
            'numeric_upper_guard_m':10*PAD}


def verify_circle_cover(region,center,radius,max_boxes=50000):
    """Independent rectangle cover; no arc/intersection code is called.

    If the budget is exhausted, remaining boxes raise the returned upper bound
    rather than being silently dropped. A zero bound needs a proven empty cover.
    """
    if center is None:
        center=((region.box[0]+region.box[1])/2,(region.box[2]+region.box[3])/2)
        radius=0.
    pending=[region.box]; checked=0; unresolved=[]
    while pending and checked<max_boxes:
        box=pending.pop(); checked+=1
        if not region.possible(box): continue
        _,high=box_distance(center,box)
        if high<=radius: continue
        a,b,c,d=box
        if max(b-a,d-c)<1e-9:
            unresolved.append(box); continue
        if b-a>=d-c:
            m=(a+b)/2; pending.extend([(a,m,c,d),(m,b,c,d)])
        else:
            m=(c+d)/2; pending.extend([(a,b,c,m),(a,b,m,d)])
    unresolved.extend(box for box in pending if region.possible(box))
    certified=max([radius]+[box_distance(center,box)[1] for box in unresolved])
    return {'center':center,'radius_upper_m':certified,'requested_radius_m':radius,
            'box_checks':checked,'unresolved_boxes':len(unresolved),
            'requested_circle_verified':not unresolved,
            'method':'outward-guarded rectangle cover; unresolved boxes explicitly covered'}
