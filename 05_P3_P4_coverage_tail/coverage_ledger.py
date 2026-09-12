"""Conservative continuum reception coverage; bits represent whole cells, not samples."""
import math


class CoverageLedger:
    def __init__(self, problem, step=100., sectors=36):
        self.problem=problem
        self.step=step
        self.sectors=1 if problem==3 else sectors
        self.cell_radius=step/math.sqrt(2)
        self.cells=[]
        m=math.ceil(1800/step)
        for i in range(-m,m):
            for j in range(-m,m):
                x,y=(i+.5)*step,(j+.5)*step
                if max(0,abs(x)-step/2)**2+max(0,abs(y)-step/2)**2<=1800**2:
                    self.cells.append((x,y))
        self.axes=[(math.cos(2*math.pi*(j+.5)/self.sectors),math.sin(2*math.pi*(j+.5)/self.sectors)) for j in range(self.sectors)]
        self.angular_error=2*math.sin(math.pi/(2*self.sectors))
        self.full=(1<<(len(self.cells)*self.sectors))-1
        self.cache={}
        self.observed={f:0 for f in range(1,21)}
        self.samples={f:[] for f in range(1,21)}

    def mask(self, q):
        q=tuple(q)
        if q in self.cache:return self.cache[q]
        result=0
        for i,(x,y) in enumerate(self.cells):
            dx,dy=q[0]-x,q[1]-y
            d=math.hypot(dx,dy)
            if d+self.cell_radius>=1000-1e-6:continue
            if self.problem==3:
                result|=1<<i
            else:
                margin=self.cell_radius+d*self.angular_error+1e-6
                for j,(ux,uy) in enumerate(self.axes):
                    if dx*ux+dy*uy>margin:result|=1<<(i*self.sectors+j)
        self.cache[q]=result
        return result

    def observe(self, f, q, response):
        if response['measure_result']=='no_signal':
            self.observed[f]|=self.mask(q)
            self.samples[f].append(tuple(q))

    def union(self, points):
        result=0
        for q in points:result|=self.mask(q)
        return result

    def sufficient(self, channels, future=0):
        return all(self.observed[f]|future==self.full for f in channels)

