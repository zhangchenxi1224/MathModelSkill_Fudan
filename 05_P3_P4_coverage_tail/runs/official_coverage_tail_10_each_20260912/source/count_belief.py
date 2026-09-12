"""Count posterior from a joint, persistent reception ledger. Ranking only."""
import math
from functools import lru_cache


def elementary(values,kmax=16):
    e=[1.]+[0.]*kmax
    for v in values:
        for k in range(kmax,0,-1):e[k]+=v*e[k-1]
    return e


class CountBelief:
    def __init__(self,problem,prior):
        self.problem=problem;self.prior={int(k):v for k,v in prior['N_prior'].items()}
        self.directed=prior['directional_fraction'] if problem==4 else 0.
        self.omni=[];self.dirs=[]
        for i in range(64):
            r=1800*math.sqrt((i+.5)/64);t=i*math.pi*(3-math.sqrt(5));x,y=r*math.cos(t),r*math.sin(t)
            for radius in (1083.333333,1250.,1416.666667):
                self.omni.append((x,y,radius,None))
                if problem==4:
                    for j in range(8):
                        a=2*math.pi*(j+.5)/8;self.dirs.append((x,y,radius,(math.cos(a),math.sin(a))))
        self.full=((1<<len(self.omni))-1,(1<<len(self.dirs))-1)
        self.alive={f:self.full for f in range(1,21)}
        self.seen=set();self.cache={}

    def masks(self,q):
        q=tuple(q)
        if q not in self.cache:
            masks=[]
            for cloud in (self.omni,self.dirs):
                bits=0
                for i,(x,y,r,u) in enumerate(cloud):
                    dx,dy=q[0]-x,q[1]-y
                    if dx*dx+dy*dy<=r*r and (u is None or dx*u[0]+dy*u[1]>=0):bits|=1<<i
                masks.append(bits)
            self.cache[q]=tuple(masks)
        return self.cache[q]

    def mass(self,masks):
        return (1-self.directed)*masks[0].bit_count()/len(self.omni)+(self.directed*masks[1].bit_count()/len(self.dirs) if self.dirs else 0.)

    def observe(self,channel,q,response):
        if response['measure_result']=='no_signal':
            vis=self.masks(q);old=self.alive[channel]
            self.alive[channel]=(old[0]&~vis[0],old[1]&~vis[1])
        else:self.seen.add(channel)

    def posterior(self,channels):
        known=sum(k.status in ('detected','cleared') for k in channels.values())
        unknown=[f for f,k in channels.items() if k.status=='unknown']
        likelihood={f:max(1e-5,self.mass(self.alive[f])) for f in unknown}
        coeff=elementary(likelihood.values())
        weights={n:(self.prior[n]*coeff[n-known]/math.comb(20,n) if 0<=n-known<=len(unknown) else 0.) for n in range(10,17)}
        total=sum(weights.values())
        if total<=0:weights={n:float(n>=known) for n in range(10,17)};total=sum(weights.values())
        pn={n:w/total for n,w in weights.items()}
        presence={}
        for f in unknown:
            other=elementary(v for g,v in likelihood.items() if f!=g)
            presence[f]=sum(pn[n]*likelihood[f]*other[n-known-1]/coeff[n-known] for n in range(10,17) if 0<n-known<=len(unknown) and coeff[n-known]>0)
        return dict(known=known,unknown=unknown,likelihood=likelihood,N=pn,presence=presence,
                    expected_remaining=sum(pn[n]*(n-known) for n in pn),p16=pn[16],
                    status='working posterior; count prior official, reception likelihood synthetic quadrature; not an absence proof')

    def route_cost(self,route,current,state):
        unknown=state['unknown'];seen=(0,0);here=current;cost=0.;early=0.
        den=elementary(state['likelihood'].values())[16-state['known']] if state['known']<=16 else 0.
        for q in route:
            discovered={f:min(state['likelihood'][f],self.mass((self.alive[f][0]&seen[0],self.alive[f][1]&seen[1]))) for f in unknown}
            pending=sum(1-state['presence'][f]*discovered[f]/state['likelihood'][f] for f in unknown)
            if den>0:early=state['p16']*elementary(discovered.values())[16-state['known']]/den
            cost+=(1-min(1.,early))*(math.dist(here,q)/5+6*pending)
            vis=self.masks(q);seen=(seen[0]|vis[0],seen[1]|vis[1]);here=q
        return cost
