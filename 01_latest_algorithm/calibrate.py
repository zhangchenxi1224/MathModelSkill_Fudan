"""Fit simple empirical priors on explicitly supplied TRAINING worlds/statistics."""
from pathlib import Path
import argparse
import math
from run import load_cases,read,write


def fit(cases):
    radius=[1.]*5;radial=[1.]*6;nd=n=0;counts={}
    for c in cases:
        counts[str(len(c['sources']))]=counts.get(str(len(c['sources'])),0)+1
        if c['problem']==4:
            n+=len(c['sources']);nd+=sum(s.get('direction_deg') is not None for s in c['sources'])
        for s in c['sources']:
            radius[min(4,max(0,int((s['radius']-1000)/100)))]+=1
            radial[min(5,int(math.hypot(*s['position'])/300))]+=1
    return dict(status='empirical_synthetic_training_prior_not_official_ground_truth',
                directional_prior=(nd+1)/(n+2),radius_mass=[v/sum(radius) for v in radius],
                position_radial_mass=[v/sum(radial) for v in radial],count_histogram=counts,
                training_world_keys=[c['_world_key'] for c in cases],
                limitations='No independent-noise likelihood; full history filters fixed states. Count histogram is recorded, not a stopping rule.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--training-dataset')
    p.add_argument('--official-results',help='Directory containing completed post_exit_audit.json files')
    p.add_argument('--output',required=True)
    a=p.parse_args()
    if not a.training_dataset and not a.official_results:p.error('Provide training data or official summaries')
    value=fit(load_cases(a.training_dataset)) if a.training_dataset else dict(status='official_count_prior_only')
    if a.official_results:
        rows=[read(f) for f in Path(a.official_results).rglob('post_exit_audit.json')]
        rows=[r for r in rows if r.get('problem')==4 and isinstance(r.get('source_total_post_exit'),int)
              and isinstance(r.get('directional_total_post_exit'),int)]
        unique={r['case_code']:r for r in rows if r.get('case_code')}
        n=sum(r['source_total_post_exit'] for r in unique.values())
        nd=sum(r['directional_total_post_exit'] for r in unique.values())
        if n:
            value['directional_prior']=(nd+1)/(n+2)
            value['official_count_cases']=len(unique)
    if Path(a.output).exists():raise ValueError('Choose a new prior output')
    write(a.output,value)


if __name__=='__main__':main()
