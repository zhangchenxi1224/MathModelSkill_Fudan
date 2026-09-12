"""Read-only replay of archived evidence, independent of policy decisions."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'vendor'))
from experiment import read,write,digest,world_digest


def error_at(definition,f,p):
    seed=definition['seed'];scale=definition['correlation_length_m'];mode=definition['mode']
    def unit(token):
        return int.from_bytes(hashlib.blake2b(f'{seed}|{token}'.encode('ascii'),digest_size=8).digest(),'big')/(2**64-1)
    x,y=(0. if q==0 else float(q) for q in p)
    if mode in ('zero','positive','negative'):return {'zero':0.,'positive':1.,'negative':-1.}[mode]
    if mode=='deterministic':return 2*unit(f'{f}|{x.hex()}|{y.hex()}')-1
    if mode=='extreme':return 1. if unit(f'cell|{f}|{math.floor(x/scale)}|{math.floor(y/scale)}')>=.5 else -1.
    if mode!='correlated':raise ValueError('unknown fixed error field')
    phase=2*math.pi*unit(f'phase|{f}')
    return max(-1.,min(1.,.45*math.sin(x/scale+phase)+.35*math.cos(y/scale-.7*phase)+.2*math.sin((x+y)/(2*scale)+1.3*phase)))


def expected_measure(case,f,p,cleared):
    source=next((s for s in case['sources'] if s['channel']==f),None)
    if source is None or f in cleared:return 'no_signal',None
    dx,dy=p[0]-source['position'][0],p[1]-source['position'][1]
    d=math.hypot(dx,dy)
    if d>source['radius']:return 'no_signal',None
    direction=source.get('direction_deg')
    if direction is not None and d>0:
        projection=dx*math.cos(math.radians(direction))+dy*math.sin(math.radians(direction))
        if projection < -8*math.ulp(max(1.,abs(dx),abs(dy))):return 'no_signal',None
    if d<=5:return 'near',None
    angle=round((math.degrees(math.atan2(-dy,-dx))%360+error_at(case['error_field'],f,p))%360,2)%360
    return 'direction',angle


def contains_convex(poly,p):
    if not poly:return False
    if len(poly)==1:return math.dist(poly[0],p)<1e-4
    signs=[]
    for a,b in zip(poly,poly[1:]+poly[:1]):
        dx,dy=b[0]-a[0],b[1]-a[1]
        value=dx*(p[1]-a[1])-dy*(p[0]-a[0]);tol=1e-4*max(1.,math.hypot(dx,dy))
        signs.append((value,tol))
    return all(x>=-tol for x,tol in signs) or all(x<=tol for x,tol in signs)


def audit_run(case,folder,result):
    issues=[];requests=0;position=(0.,0.);channel=1;virtual_us=0;cleared=set();seen={};measured={f:set() for f in range(1,21)}
    sources={s['channel']:s for s in case['sources']}
    counts=Counter();entered=False;exited=False
    with gzip.open(folder/'requests.jsonl.gz','rt',encoding='utf-8') as fp:
        for line in fp:
            row=json.loads(line)
            if row['event']!='request':continue
            if row.get('outcome')!='accepted':issues.append('unaccepted request');continue
            req=row['request'];response=row['response'];path=row['path'];key=req['request_id']
            payload=(path,req)
            if key in seen:
                if seen[key]!=payload:issues.append('request id conflict')
                continue
            seen[key]=payload;requests+=1
            if path=='/enter':entered=True
            elif path=='/exit':exited=True
            elif path in ('/measure','/clear'):
                if not entered or exited:issues.append('action outside session')
                f=req['channel'];p=(req['position']['x'],req['position']['y'])
                virtual_us+=round(math.dist(position,p)/5*1e6)
                counts['walk_m']+=math.dist(position,p);position=p
                if path=='/measure':
                    counts['measure']+=1;counts['switch']+=int(channel!=f)
                    virtual_us+=(5+int(channel!=f))*1000000;channel=f;measured[f].add(p)
                    kind,angle=expected_measure(case,f,p,cleared)
                    if response.get('measure_result')!=kind:issues.append('measurement kind mismatch')
                    if angle is not None and abs(response.get('svd_deg',math.inf)-angle)>1e-7:issues.append('fixed angle mismatch')
                else:
                    counts['clear']+=1
                    success=f in sources and f not in cleared and math.dist(sources[f]['position'],p)<=20.
                    if response.get('clear_result')!=('success' if success else 'no_target_in_range'):issues.append('clear mismatch')
                    virtual_us+=(5 if success else 3)*1000000
                    if success:cleared.add(f);counts['success']+=1
                state=row['state_after']
                if state['current_channel']!=channel or tuple(state['position'][v] for v in ('x','y'))!=position:
                    issues.append('client state mismatch')
            else:issues.append('unknown endpoint')
            if abs(response['virtual_time_s']-virtual_us/1e6)>1e-7:issues.append('quantized billing mismatch')
    if not entered or not exited or cleared!=set(sources):issues.append('session or full clearance missing')
    if abs(result['total_virtual_time_s']-virtual_us/1e6)>1e-7:issues.append('result time mismatch')
    for stat,key in [('measures','measure'),('switches','switch'),('clear_attempts','clear'),('clear_successes','success')]:
        if result.get(stat)!=counts[key]:issues.append('result counter mismatch '+stat)
    evidence=result.get('stop_evidence') or {}
    if evidence.get('type')=='known_upper_bound':
        if len(cleared)!=16:issues.append('invalid upper-bound termination')
    elif evidence.get('type')=='per_channel_coverage':
        points={tuple(p) for p in evidence['points']}
        if set(evidence['cleared_channels'])!=cleared:issues.append('clear evidence mismatch')
        absent=set(evidence['absent_channels'])
        if absent|cleared!=set(range(1,21)) or absent&cleared:issues.append('incomplete channel partition')
        for f in absent:
            if not points<=measured[f]:issues.append('missing real coverage measurement')
            if set(evidence['checked_indices'][str(f)])!=set(range(len(points))):issues.append('bad coverage indices')
        if case['problem']==3:
            ring=[math.hypot(*p) for p in points if math.hypot(*p)>1e-6]
            if len(points)!=7 or len(ring)!=6:issues.append('bad omni point set')
            elif max(ring)-min(ring)>1e-5 or max(ring[0]**2/3,1800**2+ring[0]**2-1800*math.sqrt(3)*ring[0])>=1000**2:issues.append('bad omni covering bound')
            angles=sorted(math.atan2(p[1],p[0]) for p in points if math.hypot(*p)>1e-6)
            if len(angles)==6 and any(abs((angles[(i+1)%6]-angles[i])%(2*math.pi)-math.pi/3)>1e-7 for i in range(6)):
                issues.append('bad omni angular spacing')
        elif len(points)==25:
            proof=evidence.get('refined_coverage_certificate',{})
            if points!={tuple(p) for p in proof.get('stations',[])}:issues.append('refined coverage point drift')
            if proof.get('reception_distance_upper_bound_m',math.inf)>=1000 or proof.get('strict_projection_lower_bound_m',0)<=0:
                issues.append('invalid refined cover margin')
        elif len(points)!=31:issues.append('unknown directional cover')
    else:issues.append('missing termination certificate')
    hulls=0;phase=False;knowledge_status={};previous_target=None;interrupted=0
    with gzip.open(folder/'decisions.jsonl.gz','rt',encoding='utf-8') as fp:
        for line in fp:
            row=json.loads(line);k=row.get('knowledge')
            if k:
                knowledge_status[k['channel']]=k['status']
                if k['channel'] in sources:
                    hulls+=1
                    if not contains_convex(k['hull'],sources[k['channel']]['position']):issues.append('truth outside hull')
            if row['event']=='confirmed16_service_phase':
                if sum(v in ('detected','cleared') for v in knowledge_status.values())!=16:issues.append('unsupported16 phase')
                phase=True
            if phase and row['event']=='measure' and row.get('reason')=='global_coverage':issues.append('search after confirmed16')
    return dict(issues=sorted(set(issues)),requests=requests,hulls=hulls,counts=dict(counts))


def audit_stages(stages,output):
    failures=[];total=Counter();rows=[]
    for stage in stages:
        stage=Path(stage);plan=read(stage/'plan.json');results=read(stage/'results.json')
        if digest({k:v for k,v in plan.items() if k!='plan_sha256'})!=plan['plan_sha256']:raise ValueError('plan hash changed')
        for name,expected in plan['source_files'].items():
            if hashlib.sha256((stage/'source'/name).read_bytes()).hexdigest()!=expected:raise ValueError('source snapshot changed')
        for name,expected in plan['dataset_runtime_hashes'].items():
            if hashlib.sha256((stage/'runtime'/name).read_bytes()).hexdigest()!=expected:raise ValueError('runtime snapshot changed')
        cases={r['case']['case_id']:r['case'] for r in plan['cases']}
        expected_runs=sum(sum(c['problem'] in s.get('problems',[3,4]) for s in plan['specs'].values()) for c in cases.values())
        if len(results)!=expected_runs:raise ValueError('missing stage runs')
        for r in results:
            if r['plan_sha256']!=plan['plan_sha256'] or r['spec']!=plan['specs'][r['variant']]:raise ValueError('result provenance changed')
            case=cases[r['case_id']]
            if r['world_sha256']!=world_digest(case) or r['scenario_sha256']!=case['scenario_sha256']:raise ValueError('case mismatch')
            folder=stage/'cases'/r['case_id']/r['variant']
            if read(folder/'result.json')!=r:raise ValueError('aggregate result differs from per-case result')
            audit=audit_run(case,folder,r)
            if audit['issues']:failures.append({'stage':str(stage),'case':r['case_id'],'variant':r['variant'],'issues':audit['issues']})
            total['runs']+=1;total['requests']+=audit['requests'];total['hulls']+=audit['hulls']
        print(json.dumps(dict(stage=str(stage),cumulative_runs=total['runs'],issues=len(failures))),flush=True)
    result=dict(passed=not failures,counts=dict(total),issues=failures,stages=[str(s) for s in stages],
                scope='Recomputed fixed feedback, billing, client state, source hull, actual coverage and stopping ledger; synthetic truth only.')
    write(output,result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--stage',action='append',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();r=audit_stages(args.stage,args.output);raise SystemExit(0 if r['passed'] else 1)
