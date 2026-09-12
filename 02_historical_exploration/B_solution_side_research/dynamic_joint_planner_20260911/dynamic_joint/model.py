"""Small immutable planning states. No client or simulator is accepted here."""
from dataclasses import dataclass, field, replace
from functools import lru_cache
import math
import random

from ._core.geometry import (distance, minimum_enclosing_circle, nearest_safe_clear,
                              certify_clear, bearing_deg, clip_bearing)
from .counts import posterior
from .working_belief import (Particle, build_belief, normalize, predict_response,
                             PlanningBudgetExceeded, check_budget)


@lru_cache(maxsize=2048)
def _counts(probabilities, label, known, likelihoods):
    from .counts import CountPrior
    return posterior(CountPrior(dict(probabilities), label=label),known,dict(likelihoods))


@lru_cache(maxsize=4096)
def _circle(hull):
    return minimum_enclosing_circle(hull)


@lru_cache(maxsize=4096)
def _clip(hull,point,angle,width):
    return tuple(clip_bearing(hull,point,angle,width))


@dataclass(frozen=True)
class Target:
    channel: int
    status: str
    hull: tuple
    particles: tuple = ()
    likelihood: float = 1.
    coverage: frozenset = frozenset()
    observed: tuple = ()
    local_used: int = 0
    probe_used: bool = False
    offstation_used: int = 0
    fallback_points: tuple = ()
    fallback_used: frozenset = frozenset()


@dataclass(frozen=True)
class Node:
    position: tuple
    current_channel: int
    targets: dict

    def counts(self, prior):
        known = sum(t.status in ('detected', 'cleared') for t in self.targets.values())
        return _counts(tuple(sorted(prior.probabilities.items())),prior.label,known,
                       tuple((f,t.likelihood) for f,t in self.targets.items() if t.status=='unknown'))


@dataclass(frozen=True)
class Action:
    kind: str
    channel: int
    point: tuple
    role: str
    station: int | None = None
    fallback_index: int | None = None
    certified: bool = False

    def key(self):
        return self.kind, self.channel, self.point


class BeliefCache:
    def __init__(self, options, prior):
        self.options, self.prior = options, prior
        self.cache = {}

    def for_channel(self, knowledge, deadline):
        key = (knowledge.channel, knowledge.status, len(knowledge.observations),
               len(knowledge.failed_clear_positions))
        if key in self.cache:
            return self.cache[key]
        if knowledge.status == 'unknown':
            rng = random.Random(self.options['seed']+knowledge.channel*1877)
            accepted = []
            proposed = self.options['unknown_samples']
            for i in range(proposed):
                check_budget(deadline)
                radius = 1800*math.sqrt(rng.random()); angle=rng.random()*2*math.pi
                position=(radius*math.cos(angle),radius*math.sin(angle))
                reception=rng.uniform(1000,1500)
                direction=(rng.uniform(0,360) if rng.random()<self.prior.directional_fraction else None)
                if knowledge.compatible_hidden_state(position, reception, direction):
                    accepted.append(Particle(position,reception,direction,1.,self.options['seed']+i*191+knowledge.channel))
            # Positive floor prevents Monte Carlo misses from declaring absence.
            likelihood = max(self.options['likelihood_floor'], len(accepted)/proposed)
            particles = normalize(accepted)
            if len(particles) > self.options['particle_count']:
                indices = [int(i*len(particles)/self.options['particle_count']) for i in range(self.options['particle_count'])]
                particles = normalize([particles[i] for i in indices])
            result = (tuple(particles), likelihood, {'compatible':len(accepted), 'proposals':proposed,
                                                   'likelihood_floored':not accepted})
        elif knowledge.status == 'detected':
            cfg = dict(seed=self.options['seed'], particle_count=self.options['particle_count'],
                       max_position_proposals=128, min_particles=3,
                       directional_prior=self.prior.directional_fraction)
            particles, info = build_belief(knowledge, cfg, deadline)
            result = tuple(particles), 1., info
        else:
            result = (), 1., {}
        self.cache[key] = result
        return result


def center_radius(target):
    return _circle(target.hull)


def weighted_center(target):
    if target.particles:
        return (math.fsum(p.position[0]*p.weight for p in target.particles),
                math.fsum(p.position[1]*p.weight for p in target.particles))
    return center_radius(target)[0]


def clear_probability(target, point):
    return math.fsum(p.weight for p in target.particles if distance(p.position,point)<=20)


def candidate_actions(node, points, prior, options, cheap=False):
    """Each real action spends a finite coverage/local/probe/optical allowance."""
    counts = node.counts(prior)
    possible = []
    detected = sorted((t for t in node.targets.values() if t.status=='detected'),
                      key=lambda t:(distance(node.position, weighted_center(t)),t.channel))
    for t in detected[:options['target_limit']]:
        center, radius = center_radius(t)
        if radius <= 19.999:
            point=nearest_safe_clear(t.hull,node.position,radius=19.999)
            if point is not None and certify_clear(t.hull,point,radius=20.,margin=1e-5):
                possible.append(Action('clear',t.channel,tuple(point),'certified_clear',certified=True))
                continue
        # A current-position measurement naturally permits shared stops.
        if t.local_used < options['local_limit']:
            proposals=[node.position]
            radius=max(30.,min(200.,radius*.4))
            # Perpendicular baselines preserve triangulation candidates even
            # when cheaper collinear moves offer almost no angular information.
            origin=t.observed[0] if t.observed else node.position
            bearing=math.atan2(center[1]-origin[1],center[0]-origin[0])
            proposals += [(center[0]+radius*math.cos(bearing+sign*math.pi/2),
                           center[1]+radius*math.sin(bearing+sign*math.pi/2)) for sign in (-1,1)]
            proposals.sort(key=lambda p:distance(node.position,p))
            used=0
            for point in proposals:
                if any(distance(point,p)<1. for p in t.observed):
                    continue
                possible.append(Action('measure',t.channel,tuple(point),'local_measure'))
                used+=1
                if used>=2: break
        if options['allow_probe'] and not t.probe_used and t.particles:
            for point in (node.position,weighted_center(t)):
                if clear_probability(t,point)>=options['probe_threshold']:
                    possible.append(Action('clear',t.channel,tuple(point),'probe'))
                    break
        # Optical attempts compete with sensing; their finite geometric cover
        # stays intact. The planner can interrupt a target between attempts.
        remaining=[(i,p) for i,p in enumerate(t.fallback_points) if i not in t.fallback_used]
        if remaining:
            i,point=min(remaining,key=lambda x:distance(node.position,x[1]))
            possible.append(Action('clear',t.channel,point,'optical',fallback_index=i))
    unknown=[t for t in node.targets.values() if t.status=='unknown']
    if counts['unseen_max']>0 and unknown:
        stations=[i for i in range(len(points)) if any(i not in t.coverage for t in unknown)]
        stations.sort(key=lambda i:(distance(node.position,points[i]),i))
        for i in stations[:options['station_limit']]:
            eligible=[t for t in unknown if i not in t.coverage]
            eligible.sort(key=lambda t:(-(counts['channel_presence'][t.channel])/
                                       (5+int(node.current_channel!=t.channel)),t.channel))
            for t in eligible[:2]:
                possible.append(Action('measure',t.channel,tuple(points[i]),'search',station=i))
        if options['offstation_limit'] and not any(distance(node.position,p)<1e-8 for p in points):
            eligible=[t for t in unknown if t.offstation_used<options['offstation_limit']
                      and all(distance(node.position,p)>=1 for p in t.observed)]
            eligible.sort(key=lambda t:(-counts['channel_presence'][t.channel],t.channel))
            if eligible:
                possible.append(Action('measure',eligible[0].channel,node.position,'search'))
    unique={a.key():a for a in possible}
    def priority(action):
        t=node.targets[action.channel]
        immediate=distance(node.position,action.point)/5+(5 if action.kind=='measure' or action.certified else 3)
        reward=3. if action.certified else 1.
        if action.role=='search': reward=.3+counts['channel_presence'][action.channel]
        if action.role=='optical': reward=.2+2*clear_probability(t,action.point)
        return immediate/reward, action.channel, action.role
    ordered=sorted(unique.values(),key=priority)
    # Preserve representatives of each available family before filling slots.
    result=[]
    for role in ('certified_clear','local_measure','search','optical','probe'):
        found=next((a for a in ordered if a.role==role),None)
        if found is not None and found not in result: result.append(found)
    result += [a for a in ordered if a not in result]
    result=result[:options['candidate_limit']]
    # Do not allow all optional slots to be taken by variants of one target.
    # Keep at least two distinct known channels when both offer actions.
    known_channels={a.channel for a in result if node.targets[a.channel].status=='detected'}
    if len(known_channels)==1 and len(detected)>1:
        other=next((a for a in ordered if node.targets[a.channel].status=='detected'
                    and a.channel not in known_channels),None)
        if other is not None:
            if len(result)<options['candidate_limit']: result.append(other)
            else:
                repeated=next((i for i in range(len(result)-1,-1,-1)
                               if sum(a.role==result[i].role for a in result)>1),None)
                if repeated is not None: result[repeated]=other
    return result


def transitions(node, action, points, prior, options):
    """Exact finite-world branches; never submit hypothetical actions to a client."""
    target=node.targets[action.channel]
    counts=node.counts(prior)
    presence=counts['channel_presence'].get(action.channel,1.)
    groups={}
    if action.kind=='clear':
        if action.certified:
            groups[('success',None)]=(1.,list(target.particles))
        elif target.particles:
            for p in target.particles:
                label=('success' if distance(p.position,action.point)<=20 else 'failure',None)
                mass,bank=groups.setdefault(label,(0.,[])); bank.append(p)
                groups[label]=(mass+p.weight,bank)
        else:
            groups[('failure',None)]=(1.,[])
    else:
        if presence<1: groups[('no_signal',None)]=(1-presence,[])
        if target.particles:
            for p in target.particles:
                kind,angle=predict_response(p,action.point)
                label=(kind,int(angle//options['angle_bin_deg']) if angle is not None else None)
                mass,bank=groups.setdefault(label,(0.,[])); bank.append(p)
                groups[label]=(mass+presence*p.weight,bank)
        else:
            mass,bank=groups.get(('no_signal',None),(0.,[]))
            groups[('no_signal',None)]=(mass+presence,bank)
    # Coalesce low-mass direction bins only. This keeps probability mass and
    # no_signal/near outcomes; merged direction branches retain the old hull.
    direction=sorted([k for k in groups if k[0]=='direction'],key=lambda k:-groups[k][0])
    keep=max(1,options['branch_limit']-sum(k[0]!='direction' for k in groups))
    if len(direction)>keep:
        merged=direction[max(0,keep-1):]
        mass=math.fsum(groups[k][0] for k in merged)
        bank=[p for k in merged for p in groups[k][1]]
        for k in merged: del groups[k]
        groups[('direction',None)]=(mass,bank)
    result=[]
    for label,(probability,bank) in groups.items():
        if probability<=1e-12: continue
        kind,bin_id=label
        updates={'particles':tuple(normalize(bank))}
        cost=distance(node.position,action.point)/5
        channel=node.current_channel
        if action.kind=='clear':
            cost+=5 if kind=='success' else 3
            if kind=='success': updates['status']='cleared'
            if action.role=='probe': updates['probe_used']=True
            if action.fallback_index is not None:
                updates['fallback_used']=target.fallback_used|{action.fallback_index}
        else:
            cost+=5+int(channel!=action.channel); channel=action.channel
            updates['observed']=target.observed+(action.point,)
            if action.role=='local_measure': updates['local_used']=target.local_used+1
            if action.station is not None: updates['coverage']=target.coverage|{action.station}
            elif target.status=='unknown': updates['offstation_used']=target.offstation_used+1
            if kind=='near': updates.update(status='cleared'); cost+=5
            elif kind=='direction':
                updates['status']='detected'
                if bin_id is not None:
                    angle=(bin_id+.5)*options['angle_bin_deg']
                    clipped=_clip(target.hull,action.point,angle,1.0051+options['angle_bin_deg']/2+1e-7)
                    if clipped: updates['hull']=tuple(clipped)
            elif target.status=='unknown':
                conditional=math.fsum(p.weight for p in bank) if target.particles else 1.
                updates['likelihood']=max(options['likelihood_floor'],target.likelihood*conditional)
                if len(updates.get('coverage',target.coverage))==len(points):
                    updates['status']='absent'
        targets=dict(node.targets); targets[action.channel]=replace(target,**updates)
        child=Node(action.point,channel,targets)
        # Particle approximation can put mass on an impossible count/coverage
        # outcome. Retain it with the parent residual, never call it a certificate.
        try: child.counts(prior); consistent=True
        except ValueError: consistent=False
        result.append((probability,child,cost,{'kind':kind,'angle_bin':bin_id,'count_consistent':consistent}))
    return result


def terminal_value(node, points, prior, options):
    """Time-valued completion proxy; not a proven bound or learned value function."""
    counts=node.counts(prior)
    known=[t for t in node.targets.values() if t.status=='detected']
    unseen=[t for t in node.targets.values() if t.status=='unknown'] if counts['unseen_max'] else []
    missing=sum(len(points)-len(t.coverage) for t in unseen)
    stations=[p for i,p in enumerate(points) if any(i not in t.coverage for t in unseen)]
    # Positive certificate-work floor prevents tiny existence probabilities
    # from assigning zero cost to unresolved channels.
    fraction=counts['expected_unseen']/max(1,len(unseen))
    scan_weight=.25+.75*fraction
    value=5.5*missing*scan_weight+options['unseen_service_s']*counts['expected_unseen']
    destinations=[weighted_center(t) for t in known]
    if stations:
        value+=scan_weight*max(distance(node.position,p) for p in stations)/5
    position=node.position
    for _ in range(len(destinations)):
        point=min(destinations,key=lambda p:distance(position,p))
        value+=distance(position,point)/5; position=point; destinations.remove(point)
    for t in known:
        _,radius=center_radius(t)
        if t.particles:
            center=weighted_center(t)
            radius=math.sqrt(math.fsum(p.weight*distance(center,p.position)**2 for p in t.particles))
        value+=5+min(600.,2*max(0.,radius-19.999)/5)
    return value


def choose(node, points, prior, options, deadline):
    roots=candidate_actions(node,points,prior,options)
    if not roots: raise ValueError('no finite-progress candidate')
    visited=0
    def evaluate(state,action,depth):
        nonlocal visited
        check_budget(deadline); visited+=1
        if visited>options['node_limit']: raise PlanningBudgetExceeded('planning_node_limit')
        branches=transitions(state,action,points,prior,options)
        value=0.; traces=[]
        for probability,child,cost,label in branches:
            check_budget(deadline)
            selected=None
            if not label['count_consistent']:
                tail=terminal_value(state,points,prior,options)
            elif depth<=1:
                tail=terminal_value(child,points,prior,options)
            else:
                alternatives=candidate_actions(child,points,prior,options)
                if not alternatives: tail=terminal_value(child,points,prior,options)
                else:
                    candidates=[(evaluate(child,a,depth-1)[0],a) for a in alternatives]
                    tail,selected=min(candidates,key=lambda item:item[0])
            value+=probability*(cost+tail)
            traces.append({'outcome':label,'probability':probability,'immediate_cost_s':cost,
                           'second_action':selected.__dict__ if selected else None,'continuation_s':tail})
        return value,traces
    evaluations=[]
    for action in roots:
        score,branches=evaluate(node,action,options['horizon'])
        evaluations.append({'action':action.__dict__,'score_s':score,'branches':branches})
    check_budget(deadline)
    best=min(range(len(roots)),key=lambda i:evaluations[i]['score_s'])
    return roots[best],{'evaluations':evaluations,'expanded_actions':visited,
                        'scope':'cross-channel finite-candidate receding horizon',
                        'score_status':'working-model expected cost plus heuristic leaf; no global optimality claim'}
