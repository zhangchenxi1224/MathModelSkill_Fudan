"""Freeze fresh PRACTICE allocation using the already collected baseline variance.

This design script never opens a simulator or sends a robot action.
"""
from pathlib import Path
import argparse
import datetime as dt
import hashlib
import json
import math
import random
import statistics


def sample_plan(values, relative_effect=.10, minimum=40, maximum=60):
    if len(values)<10:
        raise ValueError('At least ten complete prior official baseline cases are needed for this variance proxy')
    if relative_effect<=0 or minimum<40 or maximum>60 or minimum>maximum:
        raise ValueError('Use positive target effect and user-planned 40..60 total cases per problem')
    mean,sd=statistics.fmean(values),statistics.stdev(values)
    normal=statistics.NormalDist()
    z=normal.inv_cdf(.975)+normal.inv_cdf(.8)
    delta=mean*relative_effect
    needed_arm=max(2,math.ceil(2*z*z*sd*sd/(delta*delta)))
    planned=min(maximum,max(minimum,4*math.ceil(2*needed_arm/4)))
    per_arm=planned//2
    noncentral=delta/sd*math.sqrt(per_arm/2) if sd else math.inf
    power=1-normal.cdf(normal.inv_cdf(.975)-noncentral)+normal.cdf(-normal.inv_cdf(.975)-noncentral)
    return dict(prior_complete_cases=len(values),prior_mean_s=mean,prior_sd_s=sd,
                target_relative_effect=relative_effect,target_delta_s=delta,
                required_per_arm_normal_approx=needed_arm,planned_total=planned,planned_per_arm=per_arm,
                approximate_power_at_target=power,capped_below_requested_power=2*needed_arm>maximum,
                minimum_detectable_relative_effect=z*sd*math.sqrt(2/per_arm)/mean,
                assumptions='Independent equal-variance arms; two-sided alpha .05 and target power .80. Prior baseline variance is a planning proxy; candidate variance may differ.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--collection',type=Path,default=Path('results/round1'))
    p.add_argument('--candidate',type=Path,required=True,help='Frozen per-problem policy specs selected from local experiments')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--relative-effect',type=float,default=.10)
    p.add_argument('--seed',type=int,default=2026091159)
    args=p.parse_args()
    if (args.output/'plan.json').exists():
        raise SystemExit('Plan already exists; never rerandomize after outcomes are available')
    candidates=json.loads(args.candidate.read_text(encoding='utf-8'))
    if set(candidates.get('problems',{}))!={'3','4'}:
        raise SystemExit('Both problem-specific frozen candidates are required')
    prior=[]
    for path in (args.collection/'cases').glob('*/post_exit_audit.json'):
        audit=json.loads(path.read_text(encoding='utf-8'))
        if audit['protocol']=='baseline': prior.append(audit)
    cases=[]
    sample_sizes={}
    for problem in (3,4):
        rows=[r for r in prior if r['problem']==problem]
        values=[r['total_virtual_time_s'] for r in rows if r['status']=='complete']
        sizing=sample_plan(values,args.relative_effect)
        sizing['prior_failed_cases']=sum(r['status']!='complete' for r in rows)
        sample_sizes[str(problem)]=sizing
        rng=random.Random(args.seed+problem)
        for block in range(sizing['planned_total']//4):
            arms=['baseline','baseline','candidate','candidate']
            rng.shuffle(arms)
            for arm in arms:
                sequence=sum(r['problem']==problem for r in cases)+1
                case_id=f'r2-p{problem}-validation-{sequence:03d}'
                cases.append(dict(case_id=case_id,problem=problem,protocol=arm,arm=arm,
                                  block=block,sequence_within_problem=sequence,split='validation',
                                  environment='official_practice',round=2,pilot=False,
                                  policy_spec=candidates['problems'][str(problem)] if arm=='candidate' else {'sensing':'active','local_measure_limit':5,'scheduling':'joint'},
                                  allocation_seed=args.seed))
    # Alternate problems as well as balanced random arms, using no case feedback.
    cases.sort(key=lambda r:(r['sequence_within_problem'],r['problem']))
    for i,r in enumerate(cases):r['sequence']=i
    plan=dict(created_utc=dt.datetime.now(dt.timezone.utc).isoformat(),cases=cases,sample_sizes=sample_sizes,
              candidate_sha256=hashlib.sha256(args.candidate.read_bytes()).hexdigest(),candidate_file=str(args.candidate.resolve()),
              practice_authorized_by_round_plan=True,formal_authorized=False,dispatch_ready=False,
              primary='Complete clearance first; independent-arm total virtual completion time; failures retained as failures and prespecified penalized sensitivity loss.',
              stop_rule='Fixed sample size, no early stopping for favorable performance; safety/interface faults pause dispatch and preserve all attempts.',
              uncertainty='No same-case official pairing. Do not infer reliable worst-5% behavior from 20-30 cases per arm.')
    args.output.mkdir(parents=True,exist_ok=True)
    for r in cases:
        folder=args.output/'cases'/r['case_id']
        folder.mkdir(parents=True,exist_ok=True)
        (folder/'assignment.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
    (args.output/'plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'sample_sizes':sample_sizes,'planned_cases':len(cases),'dispatch_ready':False},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
