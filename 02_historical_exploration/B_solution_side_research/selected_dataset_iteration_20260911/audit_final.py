"""Independent, read-only final experiment audit; never executes a solver.

Requires all three stages complete before opening their results. Only explicit
invocation after the owner releases confirmation data should run this script.
No imports from experiment.py, methods, vendor or the dataset runtime are used.
"""
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
import argparse
import gzip
import hashlib
import json
import math
import random
import statistics


ROOT = Path(__file__).resolve().parent
PENALTY = 360000.0


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def filehash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def worldhash(case):
    return digest({k: case[k] for k in ('sources', 'error_field')})


def quantile(values, probability):
    ordered = sorted(values)
    if not ordered:
        return None
    index = (len(ordered) - 1) * probability
    lower = int(index)
    return ordered[lower] + (ordered[math.ceil(index)] - ordered[lower]) * (index - lower)


def bootstrap(differences, *, seed, draws=10000):
    if not differences:
        return None
    if len(differences) == 1:
        return {'ci95_s': None, 'ci975_s': None, 'draws': 0, 'seed': seed, 'warning': 'one pair only'}
    rng = random.Random(seed)
    estimates = [statistics.fmean(rng.choices(differences, k=len(differences))) for _ in range(draws)]
    return {'ci95_s': [quantile(estimates, .025), quantile(estimates, .975)],
            'ci975_s': [quantile(estimates, .0125), quantile(estimates, .9875)],
            'draws': draws, 'seed': seed, 'unit': 'paired whole case; resample within this displayed group'}


def derived_seed(seed, *labels):
    return int(digest([seed, *labels])[:16], 16)


def position(value):
    p = (value['x'], value['y']) if isinstance(value, dict) else tuple(value)
    if len(p) != 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in p):
        raise ValueError('invalid position')
    return p


def clean_response(response):
    return {k: v for k, v in response.items() if k != 'real_timestamp_ms'}


def fixed_error(definition, channel, point):
    """Independent transcription of the package's explicitly declared fields."""
    seed, mode = definition['seed'], definition['mode']
    x, y = [0. if v == 0 else float(v) for v in point]
    scale = definition['correlation_length_m']
    def uniform(token):
        raw = hashlib.blake2b(f'{seed}|{token}'.encode('ascii'), digest_size=8).digest()
        return int.from_bytes(raw, 'big') / (2**64 - 1)
    if mode in ('zero', 'positive', 'negative'):
        return {'zero': 0., 'positive': 1., 'negative': -1.}[mode]
    if mode == 'deterministic':
        return 2 * uniform(f'{channel}|{x.hex()}|{y.hex()}') - 1
    if mode == 'correlated':
        phase = 2 * math.pi * uniform(f'phase|{channel}')
        return max(-1., min(1., .45 * math.sin(x / scale + phase)
            + .35 * math.cos(y / scale - .7 * phase)
            + .20 * math.sin((x + y) / (2 * scale) + 1.3 * phase)))
    if mode == 'extreme':
        return 1. if uniform(f'cell|{channel}|{math.floor(x / scale)}|{math.floor(y / scale)}') >= .5 else -1.
    raise ValueError(f'unknown declared field {mode}')


def expected_measurement(case, sources, cleared, channel, point):
    source = sources.get(channel)
    if source is None or channel in cleared:
        return {'measure_result': 'no_signal'}
    dx, dy = point[0] - source['position'][0], point[1] - source['position'][1]
    length = math.hypot(dx, dy)
    if length > source['radius']:
        return {'measure_result': 'no_signal'}
    direction = source['direction_deg']
    if direction is not None and length > 0:
        theta = math.radians(direction)
        if math.cos(theta) * dx + math.sin(theta) * dy < -8 * math.ulp(max(1., abs(dx), abs(dy))):
            return {'measure_result': 'no_signal'}
    if length <= 5:
        return {'measure_result': 'near'}
    bearing = math.degrees(math.atan2(-dy, -dx)) % 360
    return {'measure_result': 'direction',
            'svd_deg': round((bearing + fixed_error(case['error_field'], channel, point)) % 360, 2) % 360}


def hull_contains(vertices, point, tol=1e-4):
    if not vertices:
        return False
    if len(vertices) == 1:
        return math.dist(vertices[0], point) <= tol
    if len(vertices) == 2:
        a, b = vertices
        dx, dy = b[0] - a[0], b[1] - a[1]
        ratio = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / (dx * dx + dy * dy) if dx or dy else 0
        ratio = min(1., max(0., ratio))
        return math.dist((a[0] + ratio * dx, a[1] + ratio * dy), point) <= tol
    crosses = []
    for a, b in zip(vertices, vertices[1:] + vertices[:1]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        crosses.append(((dx * (point[1] - a[1]) - dy * (point[0] - a[0])), tol * max(1., math.hypot(dx, dy))))
    return all(c >= -t for c, t in crosses) or all(c <= t for c, t in crosses)


def jsonlines(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8-sig') as fp:
        for line in fp:
            if line.strip():
                yield json.loads(line)


def audit_requests(path, case):
    """Reaccount accepted unique actions, including correct clear/channel rules."""
    issues, accepted, seen = [], [], {}
    sources = {s['channel']: s for s in case['sources']}
    cleared, current, point, virtual_us = set(), 1, (0., 0.), 0
    entered = exited = False
    stats = Counter(walk_distance_m=0., measures=0, switches=0, clear_attempts=0,
                    clear_successes=0, clear_failures=0, movement_cost_s=0.)
    residual = 0.
    for number, event in enumerate(jsonlines(path), 1):
        if event.get('event') != 'request':
            continue
        response, body = event.get('response') or {}, event.get('request') or {}
        if response.get('accepted') is not True:
            continue
        request_id = body.get('request_id')
        signature = digest([event['path'], body, clean_response(response)])
        if request_id in seen:
            if seen[request_id] != signature:
                issues.append(f'conflicting duplicate request {number}')
            continue
        if not request_id:
            issues.append(f'missing request id {number}')
        seen[request_id] = signature
        route = event['path']
        before, after = event.get('state_before', {}), event.get('state_after', {})
        if before and (math.dist(position(before['position']), point) > 1e-7
                or before['current_channel'] != current
                or abs(before['virtual_time_s'] - virtual_us / 1e6) > 2e-6):
            issues.append(f'before-state mismatch {number}')
        if route == '/enter':
            if entered or exited:
                issues.append(f'duplicate enter {number}')
            entered = True
        elif route == '/exit':
            if not entered or exited:
                issues.append(f'invalid exit {number}')
            exited = True
        elif route in ('/measure', '/clear'):
            if not entered or exited:
                issues.append(f'action outside active session {number}')
            target, channel = position(body['position']), body['channel']
            length = math.dist(point, target)
            movement_us = round(length / 5 * 1e6)
            virtual_us += movement_us
            stats['walk_distance_m'] += length
            stats['movement_cost_s'] += movement_us / 1e6
            if route == '/measure':
                switched = int(channel != current)
                virtual_us += (5 + switched) * 1000000
                stats['switches'] += switched
                stats['measures'] += 1
                current = channel
                expected = expected_measurement(case, sources, cleared, channel, target)
                if {k: response.get(k) for k in expected} != expected:
                    issues.append(f'feedback disagrees with exact world/error seed {number}')
            else:
                success = channel in sources and channel not in cleared and math.dist(target, sources[channel]['position']) <= 20
                if response.get('clear_result') != ('success' if success else 'no_target_in_range'):
                    issues.append(f'clear truth disagreement {number}')
                virtual_us += (5 if success else 3) * 1000000
                stats['clear_attempts'] += 1
                stats['clear_successes' if success else 'clear_failures'] += 1
                if success:
                    cleared.add(channel)
            point = target
            accepted.append({'event': route[1:], 'position': list(point), 'channel': current,
                             'target_channel': channel, 'response': clean_response(response),
                             'virtual_time_s': virtual_us / 1e6})
        else:
            issues.append(f'unknown accepted action {route}')
        delta = abs(response.get('virtual_time_s', -1) - virtual_us / 1e6)
        residual = max(residual, delta)
        if delta > 2e-6:
            issues.append(f'virtual time disagreement {number}')
        if after and (math.dist(position(after['position']), point) > 1e-7
                or after['current_channel'] != current or after['cleared_count'] != len(cleared)
                or set(after['cleared_channels']) != cleared
                or abs(after['virtual_time_s'] - virtual_us / 1e6) > 2e-6):
            issues.append(f'after-state mismatch {number}')
    stats.update(measurement_cost_s=5 * stats['measures'], switch_cost_s=stats['switches'],
                 optical_attempt_cost_s=3 * stats['clear_attempts'], laser_cost_s=2 * stats['clear_successes'],
                 total_virtual_time_s=virtual_us / 1e6, unique_accepted_requests=len(seen))
    return {'issues': issues, 'stats': dict(stats), 'actions': accepted,
            'cleared_channels': sorted(cleared), 'all_cleared': cleared == set(sources),
            'entered': entered, 'exited': exited, 'max_action_time_residual_s': residual}


def audit_decisions(path, case, actions, stop_evidence):
    issues, seq, action_index, hull_checks = [], 0, 0, 0
    sources = {s['channel']: s for s in case['sources']}
    final_knowledge, measured = {}, set()
    for event in jsonlines(path):
        if event.get('sequence') != seq:
            issues.append(f'decision sequence mismatch {seq}')
        seq += 1
        knowledge = event.get('knowledge')
        if knowledge:
            channel = knowledge['channel']
            final_knowledge[channel] = knowledge
            if channel in sources:
                hull_checks += 1
                if not hull_contains(knowledge['hull'], sources[channel]['position']):
                    issues.append(f'truth excluded from hull at {event.get("sequence")} channel {channel}')
        if event.get('event') not in ('measure', 'clear'):
            continue
        if action_index >= len(actions):
            issues.append('extra decision action')
            continue
        action = actions[action_index]
        if (event['event'] != action['event'] or list(event['position']) != action['position']
                or event['channel'] != action['channel'] or clean_response(event['response']) != action['response']
                or abs(event['virtual_time_s'] - action['virtual_time_s']) > 2e-6
                or not knowledge or knowledge['channel'] != action['target_channel']):
            issues.append(f'request/decision mismatch at action {action_index}')
        if event['event'] == 'measure':
            measured.add((knowledge['channel'], tuple(event['position'])))
        action_index += 1
    if action_index != len(actions):
        issues.append('missing decision actions')
    if stop_evidence:
        kind = stop_evidence['type']
        if kind == 'known_upper_bound':
            if len(set(stop_evidence['cleared_channels'])) != 16:
                issues.append('invalid 16-source stopping witness')
        elif kind == 'per_channel_coverage':
            points = stop_evidence['points']
            if len(points) != stop_evidence['required_point_count']:
                issues.append('coverage count mismatch')
            cleared, absent = set(stop_evidence['cleared_channels']), set(stop_evidence['absent_channels'])
            if cleared & absent or cleared | absent != set(range(1, 21)):
                issues.append('coverage stop misses or duplicates channels')
            for channel in absent:
                indices = stop_evidence['checked_indices'].get(str(channel), [])
                if set(indices) != set(range(len(points))):
                    issues.append(f'incomplete stopping coverage indices for {channel}')
                if any((channel, tuple(point)) not in measured for point in points):
                    issues.append(f'stopping station has no actual measurement for {channel}')
        else:
            issues.append(f'unknown stopping witness {kind}')
    return {'issues': issues, 'decision_rows': seq, 'hull_checks': hull_checks}


def audit_case(directory, row, result):
    case = row['case']
    issues = []
    request_path, decision_path = directory / 'requests.jsonl.gz', directory / 'decisions.jsonl.gz'
    if not request_path.exists() or not decision_path.exists():
        return {'issues': ['missing complete request or decision log'], 'costs': None, 'verified_complete': False}
    requests = audit_requests(request_path, case)
    decisions = audit_decisions(decision_path, case, requests['actions'], result.get('stop_evidence'))
    issues.extend(requests['issues']); issues.extend(decisions['issues'])
    stats = requests['stats']
    for field in ('walk_distance_m', 'measures', 'switches', 'clear_attempts', 'clear_successes', 'total_virtual_time_s'):
        recorded = result.get(field)
        if recorded is not None and abs(recorded - stats[field]) > (1e-5 if field == 'walk_distance_m' else 2e-6):
            issues.append(f'run result disagrees with independent {field}')
    verified = result.get('status') == 'complete' and requests['all_cleared'] and requests['exited'] and bool(result.get('stop_evidence')) and not issues
    if result.get('audit_complete') is not verified:
        issues.append('reported audit_complete disagrees with independent audit')
    expected_loss = stats['total_virtual_time_s'] if verified else PENALTY
    if result.get('loss_s') != expected_loss:
        issues.append('incorrect failure penalty or successful time')
    if verified:
        env = result.get('environment_summary', {})
        if not env.get('all_cleared') or env.get('cleared_count') != len(case['sources']) or result.get('clear_fraction') != 1:
            issues.append('environment completion summary disagreement')
        if set(result['stop_evidence']['cleared_channels']) != set(requests['cleared_channels']):
            issues.append('stopping evidence cleared channels disagree with actual clears')
    return {'issues': issues, 'costs': stats, 'verified_complete': verified,
            'request_count': stats['unique_accepted_requests'], 'decision_count': decisions['decision_rows'],
            'hull_checks': decisions['hull_checks'], 'max_action_time_residual_s': requests['max_action_time_residual_s'],
            'requests_file_sha256': filehash(request_path), 'decisions_file_sha256': filehash(decision_path)}


def comparison(rows, selected, *, partition, problem, hypothesis, seed=202609119731, draws=10000):
    lookup = {(r['case_id'], r['variant']): r for r in rows}
    bases = sorted([r for r in rows if r['problem'] == problem and r['variant'] == 'champion'
                    and (hypothesis == 'design_mixture' or r['hypothesis'] == hypothesis)], key=lambda r: r['case_id'])
    candidates = [lookup[(r['case_id'], selected)] for r in bases]
    delta = [c['loss_s'] - b['loss_s'] for b, c in zip(bases, candidates)]
    def arm(values):
        successes = [r for r in values if r['audit_complete']]
        result = {'n': len(values), 'complete': len(successes), 'failures': len(values) - len(successes),
                  'mean_penalized_T_s': statistics.fmean(r['loss_s'] for r in values),
                  'mean_penalized_T_per_N_s': statistics.fmean(r['loss_s'] / r['source_total'] for r in values),
                  'mean_success_T_s': statistics.fmean(r['total_virtual_time_s'] for r in successes) if successes else None,
                  'mean_success_T_per_N_s': statistics.fmean(r['total_virtual_time_s'] / r['source_total'] for r in successes) if successes else None,
                  'p95_success_T_s': quantile([r['total_virtual_time_s'] for r in successes], .95),
                  'max_success_T_s': max((r['total_virtual_time_s'] for r in successes), default=None),
                  'max_program_real_s': max(r['program_real_time_s'] for r in values)}
        costs = [r['_audit']['costs'] for r in values if r.get('_audit', {}).get('costs') is not None]
        result['cost_observed_n'] = len(costs)
        result['mean_costs_and_calls'] = {k: statistics.fmean(v[k] for v in costs) for k in (
            'walk_distance_m', 'measures', 'switches', 'clear_attempts', 'clear_successes', 'clear_failures',
            'movement_cost_s', 'measurement_cost_s', 'switch_cost_s', 'optical_attempt_cost_s', 'laser_cost_s')} if costs else None
        return result
    baseline, candidate = arm(bases), arm(candidates)
    worst_index = max(range(len(delta)), key=lambda i: delta[i])
    return {'partition': partition, 'problem': problem, 'hypothesis': hypothesis, 'selected': selected,
            'baseline': baseline, 'candidate': candidate,
            'mean_delta_s': statistics.fmean(delta),
            'ratio_of_means_reduction_pct': 100 * (1 - candidate['mean_penalized_T_s'] / baseline['mean_penalized_T_s']),
            'mean_paired_reduction_pct': statistics.fmean(100 * (1 - c['loss_s'] / b['loss_s']) for b, c in zip(bases, candidates)),
            'regressions': sum(d > 1e-5 for d in delta), 'new_failures': sum(b['audit_complete'] and not c['audit_complete'] for b, c in zip(bases, candidates)),
            'worst_regression_s': delta[worst_index], 'worst_regression_case_id': bases[worst_index]['case_id'],
            'worst_regression_pct': max(100 * (c['loss_s'] / b['loss_s'] - 1) for b, c in zip(bases, candidates)),
            'bootstrap': bootstrap(delta, seed=derived_seed(seed, partition, problem, hypothesis), draws=draws)}


def check(condition, label, issues):
    if not condition:
        issues.append(label)


def preflight(selection_path, stages):
    required = [Path(selection_path), Path(selection_path).parent / 'protocol.json']
    for stage in stages.values():
        required.extend(Path(stage) / name for name in ('plan.json', 'results.json', 'summary.json'))
        if (Path(stage) / 'RUNNING.lock').exists():
            raise RuntimeError(f'stage still running: {stage}; no results opened')
        if (Path(stage) / 'INVALID.json').exists():
            raise RuntimeError(f'invalidated stage: {stage}; no results opened')
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise RuntimeError('final stages not ready; no results opened: ' + ', '.join(missing))


def audit_stage(stage, partition, protocol, selection, *, audit_logs=True):
    stage = Path(stage).resolve()
    plan, results, summary = read(stage / 'plan.json'), read(stage / 'results.json'), read(stage / 'summary.json')
    issues = []
    check(plan['plan_sha256'] == digest({k: v for k, v in plan.items() if k != 'plan_sha256'}), 'plan self hash', issues)
    check(plan['partition'] == partition, 'partition mismatch', issues)
    check(Path(plan['output']).resolve() == stage, 'plan output path mismatch', issues)
    check(plan['protocol_sha256'] == digest(protocol), 'plan protocol binding', issues)
    check(plan['source_files'] == selection['source_files'], 'stage/frozen source mismatch', issues)
    check(plan['case_file_sha256'] == protocol['case_file_hashes'][partition] == digest(plan['cases']), 'registered exact worlds mismatch', issues)
    check(plan['dataset_runtime_hashes'] == protocol['dataset_runtime_hashes'], 'registered runtime hashes mismatch', issues)
    check(Path(plan['dataset_runtime']).resolve() == (stage / 'runtime').resolve(), 'runtime not isolated in stage', issues)
    if partition == 'development':
        check(plan.get('selection_sha256') is None, 'development unexpectedly uses frozen selection', issues)
        check(digest(results) == selection['development_results_sha256'], 'frozen development results mismatch', issues)
        check(Path(selection['development_stage']).resolve() == stage, 'selection points at different development stage', issues)
    else:
        check(plan['selection_sha256'] == selection['selection_sha256'], 'stage selection binding', issues)
        check(plan['specs'] == selection['validation_specs'], 'confirmation/stress specs not exact frozen selection', issues)
    champion = plan['specs'].get('champion', {})
    check({k: v for k, v in champion.items() if k != 'problems'} == {'implementation': 'champion', 'limit': 1}
          and sorted(champion.get('problems', [3, 4])) == [3, 4], 'strong champion changed or missing a problem', issues)
    for relative, expected in plan['source_files'].items():
        path = stage / 'source' / relative
        check(path.is_file() and filehash(path) == expected, f'source bytes mismatch: {relative}', issues)
    for relative, expected in plan['dataset_runtime_hashes'].items():
        path = stage / 'runtime' / relative
        check(path.is_file() and filehash(path) == expected, f'runtime bytes mismatch: {relative}', issues)
    case_lookup = {row['case']['case_id']: row for row in plan['cases']}
    check(len(case_lookup) == len(plan['cases']), 'duplicate case ids', issues)
    worlds = set()
    for row in plan['cases']:
        case = row['case']
        check(case['scenario_sha256'] == digest({k: v for k, v in case.items() if k != 'scenario_sha256'}), f'case self hash: {case["case_id"]}', issues)
        worlds.add(worldhash(case))
    check(len(worlds) == len(plan['cases']), 'duplicate hidden worlds', issues)
    expected = {(case_id, name) for case_id, row in case_lookup.items() for name, spec in plan['specs'].items()
                if row['case']['problem'] in spec.get('problems', [3, 4])}
    actual = [(r['case_id'], r['variant']) for r in results]
    check(len(actual) == len(set(actual)) and set(actual) == expected, 'missing/extra/duplicate case runs', issues)
    check(summary['expected_runs'] == len(expected) and summary['runs'] == len(results), 'summary run count mismatch', issues)
    check(summary['plan_sha256'] == plan['plan_sha256'], 'summary plan binding', issues)
    case_audits = []
    for number, r in enumerate(results, 1):
        if r['case_id'] not in case_lookup or r['variant'] not in plan['specs']:
            issues.append(f'unknown case/variant in results: {r["case_id"]}/{r["variant"]}')
            continue
        row, spec = case_lookup[r['case_id']], plan['specs'][r['variant']]
        case = row['case']; own_issues = []
        for field, expected_value in {'spec': spec, 'problem': case['problem'], 'hypothesis': row['hypothesis'],
                'scenario_sha256': case['scenario_sha256'], 'world_sha256': worldhash(case),
                'role': row['role'], 'source_total': len(case['sources']), 'plan_sha256': plan['plan_sha256'], 'source_valid': True}.items():
            check(r.get(field) == expected_value, f'result {field} mismatch', own_issues)
        directory = stage / 'cases' / r['case_id'] / r['variant']
        check((directory / 'result.json').is_file() and read(directory / 'result.json') == r, 'per-case result/table mismatch', own_issues)
        try:
            independent = audit_case(directory, row, r) if audit_logs else {'issues': [], 'costs': None, 'verified_complete': None}
        except (OSError, ValueError, KeyError, TypeError) as error:
            independent = {'issues': [f'log parse/audit failed: {type(error).__name__}: {error}'],
                           'costs': None, 'verified_complete': False}
        own_issues.extend(independent['issues'])
        r['_audit'] = independent
        case_audits.append({'case_id': r['case_id'], 'variant': r['variant'], 'issues': own_issues,
            'verified_complete': independent['verified_complete'], 'costs': independent.get('costs'),
            'hull_checks': independent.get('hull_checks', 0), 'request_count': independent.get('request_count', 0),
            'max_action_time_residual_s': independent.get('max_action_time_residual_s'),
            'requests_file_sha256': independent.get('requests_file_sha256'),
            'decisions_file_sha256': independent.get('decisions_file_sha256')})
        if own_issues:
            issues.append(f'{r["case_id"]}/{r["variant"]}: ' + '; '.join(own_issues))
        if number % 120 == 0:
            print(json.dumps({'audit_stage': partition, 'checked': number, 'total': len(results), 'issues': len(issues)}), flush=True)
    check(summary['all_complete'] == sum(r['audit_complete'] for r in results), 'summary completion mismatch', issues)
    return {'issues': issues, 'cases': len(plan['cases']), 'runs': len(results),
            'complete': sum(r['audit_complete'] for r in results), 'case_audits': case_audits,
            'groups': dict(Counter(f'{r["case"]["problem"]}:{r["hypothesis"]}' for r in plan['cases'])),
            'plan_sha256': plan['plan_sha256'], 'results_file_sha256': filehash(stage / 'results.json'),
            'world_hashes': sorted(worlds)}, results, plan


def independently_select(results, specs):
    lookup = {(r['case_id'], r['variant']): r for r in results}
    choices = {}
    for problem in (3, 4):
        eligible = []
        for variant in specs:
            rows = [r for r in results if r['problem'] == problem and r['variant'] == variant]
            groups = [[r for r in rows if r['hypothesis'] == h] for h in ('H1', 'H2')]
            if all(group and all(r['audit_complete'] for r in group)
                   and statistics.fmean(r['loss_s'] - lookup[(r['case_id'], 'champion')]['loss_s'] for r in group) <= 1e-6
                   for group in groups):
                eligible.append((statistics.fmean(r['loss_s'] for r in rows), variant))
        choices[str(problem)] = min(eligible)[1] if eligible else 'champion'
    return choices


def audit_final(selection_path, stages, *, seed=202609119731, draws=10000, audit_logs=True):
    preflight(selection_path, stages)
    selection_path = Path(selection_path).resolve()
    selection, protocol = read(selection_path), read(selection_path.parent / 'protocol.json')
    issues = []
    check(selection['selection_sha256'] == digest({k: v for k, v in selection.items() if k != 'selection_sha256'}), 'selection self hash', issues)
    check(selection['protocol_sha256'] == digest(protocol), 'selection protocol binding', issues)
    check(protocol['failure_loss_seconds'] == PENALTY, 'failure penalty differs from audit rule', issues)
    for partition in stages:
        check(digest(read(selection_path.parent / f'{partition}_cases.json')) == protocol['case_file_hashes'][partition], f'campaign registered {partition} worlds changed', issues)
    audits, comparisons, plans, tables = {}, [], {}, {}
    for partition, stage in stages.items():
        audit, rows, plan = audit_stage(stage, partition, protocol, selection, audit_logs=audit_logs)
        audits[partition], plans[partition], tables[partition] = audit, plan, rows
        issues.extend(f'{partition}: {issue}' for issue in audit['issues'])
        for problem in (3, 4):
            hypotheses = ['design_mixture'] + sorted({r['hypothesis'] for r in rows if r['problem'] == problem})
            for h in hypotheses:
                try:
                    comparisons.append(comparison(rows, selection['choices'][str(problem)], partition=partition,
                        problem=problem, hypothesis=h, seed=seed, draws=draws))
                except (KeyError, ValueError, statistics.StatisticsError) as error:
                    issues.append(f'{partition} P{problem}/{h}: comparison unavailable due to missing or invalid pairs: {error}')
    expected_sizes = {'development': 360, 'confirmation': 4 * protocol['fresh_per_group'], 'stress': 48}
    for partition, count in expected_sizes.items():
        check(audits[partition]['cases'] == count, f'{partition} registered size mismatch', issues)
    for first, second in [('development', 'confirmation'), ('development', 'stress'), ('confirmation', 'stress')]:
        check(not set(audits[first]['world_hashes']) & set(audits[second]['world_hashes']), f'{first}/{second} hidden world overlap', issues)
    try:
        choices = independently_select(tables['development'], plans['development']['specs'])
    except (KeyError, ValueError, statistics.StatisticsError) as error:
        choices = dict(selection['choices'])
        issues.append(f'independent selection unavailable due to incomplete development records: {error}')
    check(choices == selection['choices'], 'independent full-development selection disagrees', issues)
    selected_names = set(choices.values()) - {'champion'}
    check(set(selection['validation_specs']) == selected_names | {'champion'}, 'extra or missing frozen validation arm', issues)
    for name in selected_names:
        scope = sorted(int(p) for p, v in choices.items() if v == name)
        spec = selection['validation_specs'][name]
        check(sorted(spec.get('problems', [3, 4])) == scope, f'validation scope wrong for {name}', issues)
        check({k: v for k, v in spec.items() if k != 'problems'} ==
              {k: v for k, v in plans['development']['specs'][name].items() if k != 'problems'}, f'validation candidate changed: {name}', issues)
    return {'created_utc': datetime.now(timezone.utc).isoformat(), 'audit_passed': not issues,
            'issues': issues, 'issue_count': len(issues), 'source_sha256': filehash(__file__),
            'selection_file_sha256': filehash(selection_path), 'selection_sha256': selection['selection_sha256'],
            'protocol_sha256': digest(protocol), 'independently_recomputed_choices': choices,
            'bootstrap_master_seed': seed, 'bootstrap_draws': draws, 'logs_independently_audited': audit_logs,
            'stages': audits, 'comparisons': comparisons,
            'claim_limits': ['Supplied 360 worlds are reused synthetic development/regression, not new holdout.',
                'New confirmation is synthetic under retained H1/H2, not fresh official practice.',
                'Development bootstrap is descriptive after selection; confirmation uses frozen choices.',
                'The two problem-level confirmation contrasts use 97.5% intervals; H-specific 95% intervals are descriptive.',
                'Mean ratio reduction differs from mean of paired percentages; both are explicitly named.',
                'All logs use fixed declared error fields; hidden truth is accessed only by this post-run audit.',
                'Coverage geometry is supplied by separately verified construction; this audit checks actual stopping ledgers and observations.']}


def render_markdown(report):
    lines = ['# 最终实验独立审计', '',
             f"审计通过：{report['audit_passed']}；问题数：{report['issue_count']}。本工具不执行策略、不访问官方接口。", '',
             '|分区|题|冻结策略|配对数|冠军均值/s|候选均值/s|均值差/s|均值比改善|配对97.5%区间/s|退步数|最大退步/s|',
             '|---|---:|---|---:|---:|---:|---:|---:|---|---:|---:|']
    for row in report['comparisons']:
        if row['hypothesis'] != 'design_mixture':
            continue
        ci = row['bootstrap']['ci975_s']
        shown = 'NA' if ci is None else f'[{ci[0]:.2f}, {ci[1]:.2f}]'
        lines.append(f"|{row['partition']}|{row['problem']}|{row['selected']}|{row['baseline']['n']}|{row['baseline']['mean_penalized_T_s']:.2f}|{row['candidate']['mean_penalized_T_s']:.2f}|{row['mean_delta_s']:.2f}|{row['ratio_of_means_reduction_pct']:.3f}%|{shown}|{row['regressions']}|{row['worst_regression_s']:.2f}|")
    lines += ['', '分层均值、T/N、P95、最大值、全部失败、调用和移动成本以及逐局审计均保存在同名JSON。费用从唯一接受请求独立重算；不能用失败短耗时替代360000秒罚时。', '',
              f"独立bootstrap：{report['bootstrap_draws']}次，主种子{report['bootstrap_master_seed']}；开发区间仅作描述。", '',
              '## H1/H2分层及尾部', '',
              '|分区|题|H|冠军→候选均值/s|均值差/s|冠军→候选T/N|冠军→候选成功局P95/s|失败数（冠军/候选）|',
              '|---|---:|---|---|---:|---|---|---|']
    for row in report['comparisons']:
        if row['hypothesis'] not in ('H1', 'H2'):
            continue
        b, c = row['baseline'], row['candidate']
        tail = 'NA' if b['p95_success_T_s'] is None or c['p95_success_T_s'] is None else f"{b['p95_success_T_s']:.2f}→{c['p95_success_T_s']:.2f}"
        lines.append(f"|{row['partition']}|{row['problem']}|{row['hypothesis']}|{b['mean_penalized_T_s']:.2f}→{c['mean_penalized_T_s']:.2f}|{row['mean_delta_s']:.2f}|{b['mean_penalized_T_per_N_s']:.2f}→{c['mean_penalized_T_per_N_s']:.2f}|{tail}|{b['failures']}/{c['failures']}|")
    lines += ['', '## 独立请求成本分解', '',
              '|分区|题|移动/s（冠军→候选）|测量/s|切频/s|光学尝试/s|激光/s|',
              '|---|---:|---|---|---|---|---|']
    for row in report['comparisons']:
        if row['hypothesis'] != 'design_mixture':
            continue
        b, c = row['baseline']['mean_costs_and_calls'], row['candidate']['mean_costs_and_calls']
        if not b or not c:
            continue
        cells = [f"{b[k]:.2f}→{c[k]:.2f}" for k in ('movement_cost_s', 'measurement_cost_s', 'switch_cost_s', 'optical_attempt_cost_s', 'laser_cost_s')]
        lines.append(f"|{row['partition']}|{row['problem']}|" + '|'.join(cells) + '|')
    lines += ['', '## 边界', '']
    lines.extend(f'- {item}' for item in report['claim_limits'])
    if report['issues']:
        lines += ['', '## 发现的问题', '']
        lines.extend(f'- {item}' for item in report['issues'])
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection', default=str(ROOT / 'campaign/frozen_selection.json'))
    parser.add_argument('--full', default=str(ROOT / 'runs/full'))
    parser.add_argument('--confirmation', default=str(ROOT / 'runs/confirmation'))
    parser.add_argument('--stress', default=str(ROOT / 'runs/stress'))
    parser.add_argument('--output', default=str(ROOT / 'reports'))
    parser.add_argument('--draws', type=int, default=10000)
    parser.add_argument('--seed', type=int, default=202609119731)
    args = parser.parse_args()
    output = Path(args.output)
    targets = [output / 'independent_audit.json', output / 'independent_audit.md']
    if any(path.exists() for path in targets):
        raise ValueError('independent audit report already exists; use a new output directory')
    if args.draws < 10000:
        raise ValueError('final audit requires at least 10000 independent bootstrap draws')
    report = audit_final(args.selection, {'development': args.full, 'confirmation': args.confirmation, 'stress': args.stress},
                         seed=args.seed, draws=args.draws)
    output.mkdir(parents=True, exist_ok=True)
    targets[0].write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    targets[1].write_text(render_markdown(report), encoding='utf-8')
    print(json.dumps({'audit_passed': report['audit_passed'], 'issues': report['issue_count'], 'output': str(output)}))
    if not report['audit_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
