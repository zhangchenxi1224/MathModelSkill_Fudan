"""Three-feedback action costs using the same optical planner as execution."""
import copy
import math
import time
from bsolver.geometry import distance,minimum_enclosing_circle,certify_clear
from bsolver.sensing import candidate_points,optical_fallback_points
from bsolver.knowledge import InconsistentKnowledge
from methods.clear_cover import build_cover,cover_cost
from .belief import hypotheses,compact,branches


def clear_plan(k,current,states=(),probability_order=False):
    center,r=minimum_enclosing_circle(k.hull)
    if r<19.999 and certify_clear(k.hull,center,radius=20.,margin=1e-5):
        points=[center]
    else:
        try:points,_=build_cover(k,current,'bbox')
        except (ValueError,ArithmeticError):points=optical_fallback_points(k.first_direction)
    points=[tuple(p) for p in points if k.clearance_possible(p)]
    if probability_order and states:
        # Probability per incremental second, followed by all remaining points.
        remaining=list(points); points=[]; uncovered=list(states); here=current
        while remaining and uncovered:
            ranked=[]
            for i,p in enumerate(remaining):
                mass=sum(s.weight for s in uncovered if distance(s.position,p)<=20.)
                ranked.append((mass/(distance(here,p)/5+3),-distance(here,p),-i))
            best=max(ranked)
            if best[0]<=0:break
            p=remaining.pop(-best[2]);points.append(p);here=p
            uncovered=[s for s in uncovered if distance(s.position,p)>20.]
        points.extend(remaining)
    return points


def expected_cover(points,current,states):
    if not points:return 0. if not states else 360000.
    if not states:return cover_cost(points,current)
    elapsed=0.;here=current;costs=[None]*len(states)
    for p in points:
        elapsed+=distance(here,p)/5+3;here=p
        for i,s in enumerate(states):
            if costs[i] is None and distance(s.position,p)<=20.:
                costs[i]=elapsed+2
    # Unrepresented geometry still has a full optical fallback during execution.
    return sum(s.weight*(costs[i] if costs[i] is not None else elapsed+3600.) for i,s in enumerate(states))


def proposals(k,current,limit=6,diverse=False,recovery=False):
    candidates=[q for q,_ in candidate_points(k,current)]
    center,r=minimum_enclosing_circle(k.hull)
    candidates.extend([current,center])
    if k.first_direction:
        p=k.first_direction.position;t=math.radians(k.first_direction.angle)
        for side in (-600.,600.):
            # Q2 open seed in the actual first-observation coordinate frame.
            candidates.append((p[0]+850*math.cos(t)-side*math.sin(t),
                               p[1]+850*math.sin(t)+side*math.cos(t)))
    for t in range(4):
        theta=t*math.pi/2
        candidates.append((center[0]+max(50.,r)*math.cos(theta),center[1]+max(50.,r)*math.sin(theta)))
    unique=[]
    for p in candidates:
        p=tuple(p)
        if any(distance(p,o.position)<.05 for o in k.observations):continue
        if any(distance(p,q)<.05 for q in unique):continue
        unique.append(p)
    # Include open candidates explicitly; do not prefilter them by guaranteed reception.
    near=sorted(unique,key=lambda p:distance(p,current)+.2*distance(p,center))[:max(1,limit-2)]
    open_seeds=unique[-6:] if len(unique)>6 else unique
    for p in sorted(open_seeds,key=lambda p:distance(p,current)):
        if p not in near:near.append(p)
        if len(near)>=limit:break
    if recovery and k.first_direction and k.observations[-1].result=='no_signal':
        anchor=k.first_direction.position;t=math.radians(k.first_direction.angle)
        u=(math.cos(t),math.sin(t));last=k.observations[-1].position
        dx,dy=last[0]-anchor[0],last[1]-anchor[1];along=dx*u[0]+dy*u[1]
        reflected=(anchor[0]+2*along*u[0]-dx,anchor[1]+2*along*u[1]-dy)
        if all(distance(reflected,o.position)>.05 for o in k.observations) and all(distance(reflected,q)>.05 for q in near):near.append(reflected)
    if not diverse:return near
    anchor=k.first_direction.position if k.first_direction else current
    def cross_score(q):
        a=(anchor[0]-center[0],anchor[1]-center[1]);b=(q[0]-center[0],q[1]-center[1])
        denom=max(1.,math.hypot(*a)*math.hypot(*b))
        cross=abs(a[0]*b[1]-a[1]*b[0])/denom
        return cross/(1+distance(q,center)/500+distance(q,current)/1500)
    def receive_score(q):
        # Convex combinations of prior receiving sites remain visible for a fixed source.
        from bsolver.nosignal_sensing import positive_convex_visibility
        certain=positive_convex_visibility(k.positive_positions,q)
        return (int(certain),cross_score(q),-distance(current,q))
    ordered=[near[0]] if near else []
    for pool in (sorted(unique,key=cross_score,reverse=True)[:2],sorted(unique,key=receive_score,reverse=True)[:2],near,unique):
        for q in pool:
            if q not in ordered:ordered.append(q)
            if len(ordered)>=limit:return ordered
    return ordered


def posterior(k,q,kind,angle,bin_deg,region_depth=3):
    child=copy.deepcopy(k)
    # Direction bins represent a range of reports. Widen the planning envelope
    # so every report in the bin is retained; actual updates keep original epsilon.
    if kind=='direction':child.epsilon_deg+=bin_deg/2
    child.depth=min(child.depth,region_depth)
    response={'measure_result':kind,'accepted':True}
    if kind=='direction':response['svd_deg']=angle
    child.observe(q,response)
    return child


def choose(k,current,current_channel,options,prior=None,remaining=1):
    states=compact(hypotheses(k,prior,count=options.get('samples',40)),options.get('particles',20))
    if not states:return None,{'reason':'no_working_particles_use_geometric_baseline'}
    deadline=time.monotonic()+options.get('planning_seconds',5.)
    ordered=options.get('probability_order',False)
    horizon=min(options.get('horizon',1),remaining)
    nodes=0
    total_nodes=0
    node_budget=options.get('node_budget',8)
    def terminal(points,here,cloud):
        goal=options.get('next_goal')
        weight=options.get('terminal_weight',0.)
        if not goal or not weight:return 0.
        fallback=points[-1] if points else here
        if not cloud:return weight*distance(fallback,goal)/5
        return weight*sum(s.weight*distance(next((p for p in points if distance(p,s.position)<=20),fallback),goal)/5 for s in cloud)
    def search(state,here,channel,cloud,depth):
        nonlocal nodes,total_nodes
        points=clear_plan(state,here,cloud,ordered)
        value=expected_cover(points,here,cloud)+terminal(points,here,cloud)
        best={'kind':'cover','point':here,'expected_s':value}
        if depth<=0 or nodes>=node_budget or time.monotonic()>=deadline:return best
        bin_deg=options.get('angle_bin_deg',8.)
        if options.get('adaptive_bins') and minimum_enclosing_circle(state.hull)[1]<=100:
            bin_deg=2.
        for q in proposals(state,here,options.get('candidate_limit',5),options.get('diverse_candidates',False),options.get('mirror_recovery',False)):
            if state is k and options.get('balanced_roots'):nodes=0
            if nodes>=node_budget or time.monotonic()>=deadline:break
            nodes+=1
            total_nodes+=1
            cost=distance(here,q)/5+5+int(channel!=state.channel)
            outcomes=[]; valid=True
            for kind,angle,mass,conditional in branches(cloud,q,bin_deg):
                if time.monotonic()>=deadline:
                    valid=False;break
                if kind=='near':future=5.+terminal([q],q,conditional)
                else:
                    try:
                        child=posterior(state,q,kind,angle,bin_deg,options.get('planning_region_depth',3))
                        future=search(child,q,state.channel,conditional,depth-1)['expected_s']
                    except (ValueError,ArithmeticError,InconsistentKnowledge):
                        valid=False;break
                cost+=mass*future
                outcomes.append({'kind':kind,'angle':angle,'probability':mass,'remaining_s':future})
            if valid and cost<best['expected_s']:
                best={'kind':'measure','point':q,'expected_s':cost,'branches':outcomes}
        return best
    decision=search(k,current,current_channel,states,horizon)
    # Bounded single-point probability probes are only enabled in the DP arm.
    if options.get('allow_probe'):
        points=clear_plan(k,current,states,ordered)
        for q in points[:3]:
            success=sum(s.weight for s in states if distance(s.position,q)<=20.)
            if success<=0:continue
            failed=[s for s in states if distance(s.position,q)>20.]
            from .belief import State
            failed=[State(s.position,s.radius,s.orientation,s.weight/(1-success)) for s in failed] if success<1 else []
            value=distance(current,q)/5+3+2*success+(1-success)*expected_cover(points,q,failed)
            if value<decision['expected_s']:
                decision={'kind':'clear','point':q,'expected_s':value,'success_probability':success}
    return decision,{'evaluated_actions':total_nodes,'node_budget':node_budget,'budget_scope':'per_root' if options.get('balanced_roots') else 'whole_tree','horizon':horizon,'particles':len(states),
        'probability_status':(prior or {}).get('status','uncalibrated_working_model'),
        'elapsed_budget_reached':time.monotonic()>=deadline,'decision':decision}
