"""Adaptive evaluation over the exact 36000 legal direction report codes."""
from __future__ import annotations
import heapq
import math
import time

from geometry import enclosing_circle,verify_circle_cover


def evaluate(context,q,tolerance=1.,max_splits=120,circle_tolerance=.002,verify=False):
    started=time.monotonic()
    q=tuple(float(x) for x in q)
    if q==(0.,0.):
        circle=enclosing_circle(context.prior(),circle_tolerance)
        return {'point_local':q,'point_global':context.to_global(q),
                'lower_m':circle['lower_m'],'upper_m':circle['upper_m'],
                'gap_m':circle['upper_m']-circle['lower_m'],'repeat_first':True,
                'worst_witness':{'kind':'direction','report_deg':context.report_deg,**circle},
                'branches':{'repeat':circle},'report_partition':[],'report_codes_covered':1,
                'elapsed_s':time.monotonic()-started}
    branches={kind:enclosing_circle(context.posterior(q,kind),circle_tolerance)
              for kind in ('no_signal','near')}
    worst_kind=max(branches,key=lambda k:branches[k]['lower_m'])
    lower=branches[worst_kind]['lower_m']
    witness={'kind':worst_kind,**branches[worst_kind]}
    samples={}; leaves={}; heap=[]; serial=0

    def interval(lo,hi):
        nonlocal serial
        center=(lo+hi)/200
        half=(hi-lo)/200
        circle=enclosing_circle(context.posterior(q,'direction',center,half),circle_tolerance,need_lower=False)
        result={'lo_code':lo,'hi_code':hi,**circle}
        serial+=1
        leaves[serial]=result
        heapq.heappush(heap,(-circle['upper_m'],serial))

    def sample(code):
        nonlocal lower,witness
        if code in samples: return
        circle=enclosing_circle(context.posterior(q,'direction',code/100),circle_tolerance)
        samples[code]=circle
        if circle['lower_m']>lower:
            lower=circle['lower_m']; witness={'kind':'direction','report_deg':code/100,**circle}

    # Every integer code is covered exactly once, including 0 and 35999.
    for lo in range(0,36000,1000): interval(lo,min(35999,lo+999))
    splits=0
    while heap:
        top=-heap[0][0]
        upper=max(top,*(c['upper_m'] for c in branches.values()))
        if upper-lower<=tolerance or splits>=max_splits: break
        _,key=heapq.heappop(heap)
        row=leaves[key]
        lo,hi=row['lo_code'],row['hi_code']
        sample((lo+hi)//2)
        if row['upper_m']-lower<=tolerance:
            heapq.heappush(heap,(-row['upper_m'],key)); break
        if lo==hi:
            # An unresolved inner circle gap is reported, not treated as an
            # angle-search success. Leave the interval in the covering ledger.
            heapq.heappush(heap,(-row['upper_m'],key)); break
        del leaves[key]
        mid=(lo+hi)//2
        interval(lo,mid); interval(mid+1,hi)
        splits+=1
    partition=sorted(leaves.values(),key=lambda r:r['lo_code'])
    checks=[]
    if verify:
        # Only final selected candidates need this independent second oracle.
        for kind,row in branches.items():
            radius=row['upper_m']+.01 if not row['empty_relaxation'] else 0.
            check=verify_circle_cover(context.posterior(q,kind),row['center'],radius,max_boxes=30000)
            row['verified_upper_m']=check['radius_upper_m']; checks.append({'kind':kind,**check})
        for row in partition:
            center=(row['lo_code']+row['hi_code'])/200
            half=(row['hi_code']-row['lo_code'])/200
            radius=row['upper_m']+.01 if not row['empty_relaxation'] else 0.
            check=verify_circle_cover(context.posterior(q,'direction',center,half),row['center'],radius,max_boxes=10000)
            row['verified_upper_m']=check['radius_upper_m']
            checks.append({'kind':'direction','lo_code':row['lo_code'],'hi_code':row['hi_code'],**check})
    upper=max([r.get('verified_upper_m',r['upper_m']) for r in partition]+[
        c.get('verified_upper_m',c['upper_m']) for c in branches.values()])
    assert sum(r['hi_code']-r['lo_code']+1 for r in partition)==36000
    assert all(b['lo_code']==a['hi_code']+1 for a,b in zip(partition,partition[1:]))
    return {'point_local':q,'point_global':context.to_global(q),'lower_m':lower,'upper_m':upper,
            'gap_m':upper-lower,'requested_gap_m':tolerance,'gap_reached':upper-lower<=tolerance,
            'worst_witness':witness,'branches':branches,'report_partition':partition,
            'report_codes_covered':36000,'actual_reports_evaluated':len(samples),
            'report_splits':splits,'cover_verification':checks,
            'upper_status':'independent_rectangle_cover' if verify else 'analytic_arc_numeric_enclosure',
            'global_optimality_claim':False,'elapsed_s':time.monotonic()-started}
