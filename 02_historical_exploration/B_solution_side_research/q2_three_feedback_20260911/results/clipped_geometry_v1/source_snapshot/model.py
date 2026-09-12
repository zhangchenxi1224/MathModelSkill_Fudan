"""Q2 physical model: one source, shared unknown R, three feedback types."""
from __future__ import annotations
from dataclasses import dataclass
import math

from geometry import Circle,Halfplane,Region,PAD,dist,dot


def wedge(q,angle,epsilon=1.):
    if epsilon>=90: raise ValueError('split angle intervals below 180 degrees')
    lo,hi=map(math.radians,(angle-epsilon,angle+epsilon))
    normals=[(math.sin(lo),-math.cos(lo)),(-math.sin(hi),math.cos(hi))]
    return [Halfplane(n,dot(n,q)) for n in normals]


@dataclass(frozen=True)
class FirstContext:
    station: tuple[float,float]=(0.,0.)
    report_deg: float=0.

    def __post_init__(self):
        if not all(math.isfinite(x) for x in (*self.station,self.report_deg)):
            raise ValueError('finite public first observation required')
        if abs(self.report_deg*100-round(self.report_deg*100))>1e-7:
            raise ValueError('first report must lie on the 0.01 degree interface grid')

    def to_local(self,p):
        a=math.radians(self.report_deg)
        x,y=p[0]-self.station[0],p[1]-self.station[1]
        return x*math.cos(a)+y*math.sin(a),-x*math.sin(a)+y*math.cos(a)

    def to_global(self,p):
        a=math.radians(self.report_deg)
        return (self.station[0]+p[0]*math.cos(a)-p[1]*math.sin(a),
                self.station[1]+p[0]*math.sin(a)+p[1]*math.cos(a))

    def prior(self):
        target=self.to_local((0.,0.))
        eps=math.radians(1.)
        box=(max(5*math.cos(eps),target[0]-1800),min(1500.,target[0]+1800),
             max(-1500*math.sin(eps),target[1]-1800),min(1500*math.sin(eps),target[1]+1800))
        constraints=[Circle(target,1800),Circle((0.,0.),1500),Circle((0.,0.),5,False,True),*wedge((0.,0.),0.)]
        a,b,c,d=box
        constraints.extend([Halfplane((-1.,0.),-a),Halfplane((1.,0.),b),
                            Halfplane((0.,-1.),-c),Halfplane((0.,1.),d)])
        return Region(constraints,box)

    def posterior(self,q,kind,report_deg=None,half_width=0.):
        prior=self.prior()
        cs=list(prior.constraints)
        if kind=='near': cs.append(Circle(q,5.))
        elif kind=='no_signal':
            if q==(0.,0.): return Region(cs,(1.,0.,1.,0.))
            cs.extend([Circle(q,1000.,False,True),Halfplane(q,dot(q,q)/2,True)])
        elif kind=='direction':
            if report_deg is None: raise ValueError('direction requires a report')
            cs.extend([Circle(q,1500.),Circle(q,5.,False,True),
                       *wedge(q,report_deg-self.report_deg,1.+half_width)])
        else: raise ValueError('unknown feedback')
        return Region(cs,prior.box)

    def response(self,source,radius,q,error=0.):
        """Generate a legal quantized response, for external evaluation only."""
        d=dist(source,q)
        if d>radius: return {'kind':'no_signal'}
        if d<=5: return {'kind':'near'}
        if q==(0.,0.): return {'kind':'direction','report_deg':self.report_deg%360}
        actual=(math.degrees(math.atan2(source[1]-q[1],source[0]-q[0]))+self.report_deg)%360
        z=round((actual+error)%360,2)%360
        delta=(z-actual+180)%360-180
        if abs(delta)>1: z=round((z-math.copysign(.01,delta))%360,2)%360
        return {'kind':'direction','report_deg':z}

    def search_rectangle(self):
        target=self.to_local((0.,0.))
        return (max(-3000.,target[0]-3300.),min(3000.,target[0]+3300.),
                max(-3000.,target[1]-3300.),min(3000.,target[1]+3300.))


def common_quantized_report(angles):
    """Return a legal 0.01-degree common report, or None; handles wrapping."""
    if not angles: return None
    reference=angles[0]
    lifted=[reference+((a-reference+180)%360-180) for a in angles]
    low,high=max(a-1 for a in lifted),min(a+1 for a in lifted)
    code=math.ceil(low*100+1e-9)
    if code/100<=high-1e-11: return (code%36000)/100
    return None
