"""Offline diagnostics from local result/decision artifacts; no simulator calls."""
from pathlib import Path
import argparse
import collections
import gzip
import json
import statistics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--results', required=True, help='partition results.json')
    args = parser.parse_args()
    path = Path(args.results)
    rows = json.loads(path.read_text(encoding='utf-8'))
    diagnostics = []
    for problem in (3, 4):
        selected = [r for r in rows if r['problem'] == problem]
        for route in sorted({r['route'] for r in selected}):
            subset = [r for r in selected if r['route'] == route]
            counts = collections.Counter()
            elapsed = []
            for row in subset:
                logs = path.parent / row['case_id'] / route / 'decisions.jsonl.gz'
                if not logs.exists():
                    logs = logs.with_suffix('')
                if not logs.exists():
                    counts['missing_logs'] += 1
                    continue
                opener = gzip.open if logs.suffix == '.gz' else open
                with opener(logs, 'rt', encoding='utf-8') as fp:
                    for line in fp:
                        event = json.loads(line)
                        if event['event'] == 'choose_action' and route in ('fisher', 'infogain', 'rollout', 'probe'):
                            counts['planner_calls'] += 1
                            selection = event['selection']
                            planner = selection.get('planner') if selection.get('fallback') else selection
                            if selection.get('fallback'):
                                counts['fallbacks'] += 1
                                counts['fallback_' + str(planner.get('fallback_reason', 'rejected_action'))] += 1
                            else:
                                counts['planned_actions_used'] += 1
                            if isinstance(planner, dict) and 'elapsed_s' in planner:
                                elapsed.append(planner['elapsed_s'])
                        if event['event'] == 'clear' and event.get('certificate', {}).get('type') == 'bounded_experimental_probe':
                            counts['probe_attempts'] += 1
                            counts['probe_successes'] += event['response']['clear_result'] == 'success'
                        if event['event'] == 'shared_station_selection':
                            counts['shared_station_measures'] += 1
                        if event['event'] == 'reorder_pending_coverage':
                            counts['coverage_reorders'] += 1
            success = [r for r in subset if r['audit_complete']]
            diagnostics.append({'problem': problem, 'route': route, 'worlds': len(subset),
                                'complete': len(success), 'mean_success_time_s': statistics.mean(r['total_virtual_time_s'] for r in success) if success else None,
                                'max_program_s': max(r['program_real_time_s'] for r in subset),
                                'planning_time_s': sum(elapsed), 'counts': dict(counts)})
    output = path.parent / 'route_diagnostics.json'
    output.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
