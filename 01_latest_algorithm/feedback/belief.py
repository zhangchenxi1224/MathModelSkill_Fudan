"""Working joint probabilities, always distinct from geometric exclusion."""
from dataclasses import dataclass
import math
from methods.cover_order import working_samples
from bsolver.geometry import bearing_deg, distance
from .region import area

TAU=2*math.pi


@dataclass(frozen=True)
class State:
    position: tuple
    radius: float
    orientation: float | None
    weight: float


def arcs(center):
    lo=(center-math.pi/2)%TAU
    hi=lo+math.pi
    return [(lo,min(TAU,hi))]+([(0.,hi-TAU)] if hi>TAU else [])


def intersect(a,b):
    return [(max(x,u),min(y,v)) for x,y in a for u,v in b if min(y,v)>max(x,u)+1e-12]


def orientation_arcs(k,g,r):
    intervals=[(0.,TAU)]
    for o in k.observations:
        d=distance(g,o.position)
        if o.result=='no_signal' and d>r:
            continue
        if o.result!='no_signal' and d>r+1e-8:
            return []
        if d<1e-10:
            if o.result=='no_signal':return []
            continue
        center=math.atan2(o.position[1]-g[1],o.position[0]-g[0])
        if o.result=='no_signal':center+=math.pi
        intervals=intersect(intervals,arcs(center))
        if not intervals:return []
    return intervals


def radial_density(g,prior):
    weights=prior.get('position_radial_mass')
    if not weights:return 1.
    r=min(1799.999999,math.hypot(*g))
    i=int(r/1800*len(weights))
    # Positions are proposed uniformly in area; correct annulus mass to density.
    return weights[i]/max(1e-12,(i+1)**2-i**2)


def hypotheses(k,prior=None,count=40):
    prior=prior or {}
    if k.status not in ('detected','unknown') or not k.cells:return []
    cells=k.cells
    total=sum(area(c['poly']) for c in cells)
    raw=[]
    directed=prior.get('directional_prior',.5) if k.problem==4 else 0.
    radius_mass=prior.get('radius_mass',[1.,1.,1.,1.,1.])
    for cell in cells:
        fraction=area(cell['poly'])/total if total>1e-10 else 1/len(cells)
        number=max(1,round(count*fraction))
        for g in working_samples(cell['poly'],min(number,128)):
            if any(distance(g,p)<=20 for p in k.failed_clear_positions):continue
            lower=max([1000.]+[distance(g,p) for p in k.positive_positions])
            if lower>1500.+1e-8:continue
            spatial=fraction/number*radial_density(g,prior)
            for i,rmass in enumerate(radius_mass):
                a=max(lower,1000+500*i/len(radius_mass))
                b=1000+500*(i+1)/len(radius_mass)
                if a>b:continue
                r=(a+b)/2
                mass=spatial*rmass*max(1e-10,b-a)/(500/len(radius_mass))
                if directed<1 and k.compatible_hidden_state(g,r,None):
                    raw.append(State(g,r,None,mass*(1-directed)))
                if directed>0:
                    for lo,hi in orientation_arcs(k,g,r):
                        theta=math.degrees((lo+hi)/2)
                        if k.compatible_hidden_state(g,r,theta):
                            raw.append(State(g,r,theta,mass*directed*(hi-lo)/TAU))
    total=sum(s.weight for s in raw)
    if not total:return []  # Never interprets an empty sampled cloud as absence.
    return [State(s.position,s.radius,s.orientation,s.weight/total) for s in raw]


def compact(states,limit=24):
    if len(states)<=limit:return states
    # Deterministic stratified resampling of the working distribution.
    result=[]; j=0; cumulative=states[0].weight
    for i in range(limit):
        target=(i+.5)/limit
        while cumulative<target and j<len(states)-1:
            j+=1; cumulative+=states[j].weight
        s=states[j]
        result.append(State(s.position,s.radius,s.orientation,1/limit))
    return result


def branches(states,q,bin_deg=4.):
    grouped={}
    for s in states:
        d=distance(s.position,q)
        visible=d<=s.radius
        if s.orientation is not None:
            t=math.radians(s.orientation)
            visible=visible and ((q[0]-s.position[0])*math.cos(t)+(q[1]-s.position[1])*math.sin(t)>=0)
        if not visible:outcomes=[(('no_signal',None),1.)]
        elif d<=5:outcomes=[(('near',None),1.)]
        else:
            angle=bearing_deg(q,s.position)
            outcomes=[(('direction',(math.floor(((angle+e)%360)/bin_deg)+.5)*bin_deg),w)
                      for e,w in [(-.9,1/6),(0.,2/3),(.9,1/6)]]
        for key,w in outcomes:
            grouped.setdefault(key,[]).append(State(s.position,s.radius,s.orientation,s.weight*w))
    result=[]
    for (kind,angle),rows in grouped.items():
        mass=sum(s.weight for s in rows)
        result.append((kind,angle,mass,[State(s.position,s.radius,s.orientation,s.weight/mass) for s in rows]))
    return result
