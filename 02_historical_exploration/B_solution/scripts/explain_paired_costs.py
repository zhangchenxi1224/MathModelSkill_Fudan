"""Recompute actual paired cost changes without pooling scenario populations."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bsolver.round_experiments import write_csv, write_json


def components(row):
    return {
        'movement_s': row['walk_distance_m'] / 5,
        'measurement_s': row['measures'] * 5,
        'switch_s': row['switches'],
        'optical_s': row['clear_attempts'] * 3,
        'laser_s': row['clear_successes'] * 2,
        'walk_distance_m': row['walk_distance_m'],
        'measures': row['measures'],
        'clear_attempts': row['clear_attempts'],
        'failed_clear_attempts': row['clear_attempts'] - row['clear_successes'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = json.loads(args.results.read_text(encoding='utf-8-sig'))
    index = {(r['case_id'], r['variant']): r for r in rows}
    if len(index) != len(rows):
        raise ValueError('Duplicate case/arm')
    if len({r['partition'] for r in rows}) != 1:
        raise ValueError('Pass exactly one already released development/confirmation partition')
    pairs = []
    residuals = []
    for candidate in rows:
        if candidate['variant'] == args.baseline:
            continue
        baseline = index[(candidate['case_id'], args.baseline)]
        for key in ('scenario_sha256', 'error_field_sha256', 'partition', 'pool', 'composition_model', 'mechanism_id'):
            if baseline[key] != candidate[key]:
                raise ValueError(f'Unpaired world or population: {key}')
        base_cost, cand_cost = components(baseline), components(candidate)
        row = {k: candidate[k] for k in ('case_id', 'problem', 'pool', 'composition_model', 'mechanism_id', 'partition', 'variant')}
        row.update(baseline_variant=args.baseline, baseline_complete=baseline['evaluation_complete'],
                   candidate_complete=candidate['evaluation_complete'],
                   total_actual_time_delta_s=candidate['total_virtual_time_s']-baseline['total_virtual_time_s'])
        row.update({key + '_delta': cand_cost[key]-base_cost[key] for key in base_cost})
        delta = sum(row[key + '_delta'] for key in ('movement_s', 'measurement_s', 'switch_s', 'optical_s', 'laser_s'))
        residual = row['total_actual_time_delta_s']-delta
        residuals.append(abs(residual))
        if abs(residual) > .002:
            raise ValueError(f'Actual action cost mismatch: {row["case_id"]}: {residual}')
        row['component_residual_s'] = residual
        pairs.append(row)
    groups = defaultdict(list)
    for row in pairs:
        for mechanism in ('__design_mixture__', row['mechanism_id']):
            groups[tuple(row[k] for k in ('problem', 'pool', 'composition_model', 'partition', 'variant')) + (mechanism,)].append(row)
    summaries = []
    for key, values in sorted(groups.items()):
        row = dict(zip(('problem', 'pool', 'composition_model', 'partition', 'variant', 'mechanism_id'), key))
        row.update(n_pairs=len(values), baseline_failures=sum(not r['baseline_complete'] for r in values),
                   candidate_failures=sum(not r['candidate_complete'] for r in values),
                   interpretation='Actual executed-action costs, including failures; not hypothetical completion times or an official population mean')
        for metric in [k for k in values[0] if k.endswith('_delta') or k == 'total_actual_time_delta_s']:
            row[metric + '_mean'] = statistics.fmean(r[metric] for r in values)
        summaries.append(row)
    write_json(args.output/'paired_cost_cases.json', pairs)
    write_csv(args.output/'paired_cost_cases.csv', pairs)
    write_json(args.output/'paired_cost_summary.json', summaries)
    write_csv(args.output/'paired_cost_summary.csv', summaries)
    metadata = {'results_sha256': hashlib.sha256(args.results.read_bytes()).hexdigest(),
                'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'pairs': len(pairs), 'groups': len(summaries),
                'maximum_cost_reconstruction_residual_s': max(residuals, default=None)}
    write_json(args.output/'metadata.json', metadata)
    print(json.dumps(metadata, ensure_ascii=False))


if __name__ == '__main__':
    main()
