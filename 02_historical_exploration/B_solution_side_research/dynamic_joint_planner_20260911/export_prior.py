"""Export ONLY count/type marginals from an already fitted composition model."""
import argparse
import hashlib
from pathlib import Path

from evaluate import read,write
from dynamic_joint.counts import CountPrior


def export(model_path, output):
    path=Path(model_path).resolve(); model=read(path)
    destination=Path(output).resolve(); destination.mkdir(parents=True,exist_ok=False)
    hashes={}
    for problem in (3,4):
        distribution=model['problems'][str(problem)]['count_distribution']
        marginal={str(n):sum(r['probability'] for r in distribution if r['n']==n) for n in range(10,17)}
        total=sum(marginal.values())
        if abs(total-1)>1e-6: raise ValueError('composition distribution is not normalized')
        expected_n=sum(r['n']*r['probability'] for r in distribution)
        directed=sum(r['n_directed']*r['probability'] for r in distribution)
        # Broad mixing also prevents the directional working sampler losing
        # either source type through a zero frequency in the calibration fit.
        fraction=0. if problem==3 else .65*directed/expected_n+.35*.5
        prior=dict(count_probabilities=marginal,directional_fraction=fraction,broad_weight=.35,
                   label=model['model_id']+'_marginals_with_35pct_broad')
        CountPrior.from_config(problem,prior)
        spec=dict(name=f'dynamic_joint_h2_prior_p{problem}',implementation='dynamic_joint',
                  problems=[problem],limit=2,planner={'horizon':2},prior=prior)
        target=destination/f'dynamic_h2_prior_p{problem}.json'; write(target,spec)
        hashes[target.name]=hashlib.sha256(target.read_bytes()).hexdigest()
    write(destination/'provenance.json',dict(source_path=str(path),model_id=model['model_id'],
          source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),provenance=model.get('provenance'),
          output_sha256=hashes,interpretation='Count marginal plus expected source-type fraction only. '
          'Online per-channel particles approximate geometry; not a full joint N/N_directed posterior. '
          'No calibration case coordinates or evaluation scenario truth enter the policy.'))
    return destination


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args(); print(export(args.model,args.output))
