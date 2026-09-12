"""Immutable scenarios, isolated workers, paired evaluation and held-out gates.

All hidden state is used exclusively by this external harness and LocalSimulator.
The policy receives public observations, public constants and an explicit config.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys
import time
import traceback
import zipfile

from bsolver.experiments import generate_case, variant_configs
from bsolver.geometry import contains, distance
from bsolver.protocol import RobotClient
from bsolver.simulator import LocalSimulator, Source
from bsolver.strategy import SolverConfig

ROOT = Path(__file__).resolve().parents[1]
ROUTES = ('robust', 'fisher', 'infogain', 'rollout', 'joint_route', 'probe')
PENALTY = 360000.


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                    separators=(',', ':')).encode()).hexdigest()


def world_digest(case):
    """Ignore labels and partitions; identify the actual fixed hidden world."""
    return digest({k: case[k] for k in ('problem', 'seed', 'sources', 'error_mode', 'correlation_length')})


def old_worlds(value):
    if isinstance(value, dict):
        if isinstance(value.get('sources'), list) and 'seed' in value:
            field = value.get('error_field', {})
            normalized = dict(value, seed=field.get('seed', value['seed']),
                              correlation_length=value.get('correlation_length', field.get('correlation_length_m', 150.)))
            if all(k in normalized for k in ('problem', 'error_mode')):
                yield normalized
        for nested in value.values():
            yield from old_worlds(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from old_worlds(nested)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temp.replace(path)


def source_files():
    paths = [ROOT / 'run.py']
    paths += sorted((ROOT / 'diverse').glob('*.py'))
    paths += sorted((ROOT / 'vendor' / 'bsolver').glob('*.py'))
    return {str(p.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths}


def verify_manifest(manifest):
    if manifest['manifest_sha256'] != digest({k: v for k, v in manifest.items()
                                               if k != 'manifest_sha256'}):
        raise ValueError('manifest modified; create a new experiment')
    if manifest['source_files'] != source_files():
        raise ValueError('runtime source drift; prepare a new manifest/output after code changes')
    for case in manifest['cases']:
        if case['scenario_sha256'] != digest({k: v for k, v in case.items()
                                               if k != 'scenario_sha256'}):
            raise ValueError('scenario modified')


def fitted_distribution(calibration, problem):
    if calibration.get('model_status') != 'fitted_composition_only':
        raise ValueError('composition pool requires frozen fitted_composition_only data')
    rows = calibration['problems'][str(problem)]['composition_candidates']['smoothed_joint']['count_distribution']
    for row in rows:
        n, nd, p = row['n'], row['n_directed'], row['probability']
        if not isinstance(n, int) or not isinstance(nd, int) or not 10 <= n <= 16 or not 0 <= nd <= n:
            raise ValueError('illegal composition')
        if problem == 3 and nd != 0 or not math.isfinite(p) or p < 0:
            raise ValueError('invalid composition probability')
    if abs(sum(r['probability'] for r in rows) - 1.) > 1e-6:
        raise ValueError('composition probabilities must sum to one')
    return rows


def make_case(seed, problem, pool, index, calibration=None):
    rng = random.Random(seed)
    if pool == 'composition_fit':
        rows = fitted_distribution(calibration, problem)
        selected = rng.choices(rows, weights=[r['probability'] for r in rows])[0]
        n, nd = selected['n'], selected['n_directed']
    elif problem == 3:
        n, nd = 10 + index % 7, 0
    else:
        # All 98 legal (N,Ndir) combinations occur in each full block.
        compositions = [(n, nd) for n in range(10, 17) for nd in range(n + 1)]
        random.Random(96319).shuffle(compositions)
        n, nd = compositions[index % len(compositions)]
    layout = 'random'
    if pool == 'stress':
        layout = ('boundary', 'tangent', 'hidden_outward_last', 'clustered',
                  'center_and_boundary', 'near_grid')[index % 6]
        if problem == 4 and layout in ('boundary', 'tangent'):
            nd = n
        elif problem == 4 and layout == 'hidden_outward_last':
            nd = 1
    # Cross layout with radius over 18 stress rows; noise cycle 5 is coprime
    # to the six-layout cycle. Small designs are labelled incomplete grids.
    radius_mode = ('min', 'mixed', 'max')[(index // 6) % 3 if pool == 'stress' else index % 3]
    error_mode = ('deterministic', 'correlated', 'extreme', 'positive', 'negative')[index % 5]
    base_layout = 'random' if layout in ('tangent', 'near_grid') else layout
    case = generate_case(seed, problem, n, base_layout, radius_mode, error_mode)
    directional_indices = set(rng.sample(range(n), nd))
    if problem == 4 and layout == 'hidden_outward_last':
        directional_indices = {n - 1}
    for i, source in enumerate(case['sources']):
        if layout == 'tangent':
            angle = 2 * math.pi * i / n
            source['position'] = (1800 * math.cos(angle), 1800 * math.sin(angle))
        elif layout == 'near_grid':
            # Optical/triangular-grid neighbourhoods including the target centre.
            positions = [(0., 0.), (4.999, 0.), (5.001, 0.), (950., 0.),
                         (475., 950 * math.sqrt(3) / 2)]
            source['position'] = positions[i % len(positions)]
        angle = math.degrees(math.atan2(source['position'][1], source['position'][0]))
        source['direction_deg'] = ((angle + (90 if layout == 'tangent' else 0)) % 360
                                   if layout in ('boundary', 'tangent', 'hidden_outward_last')
                                   else rng.uniform(0, 360)) if i in directional_indices else None
    case.update(layout=layout, n_directed=nd, pool=pool,
                correlation_length=(75., 150., 300.)[(index // 5) % 3],
                mechanism_status='synthetic_assumptions_not_official_generator')
    return case


def prepare(args):
    destination = Path(args.output)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('prepare requires a new empty output directory')
    if args.per_cell < 1 or args.confirm_per_cell < 1:
        raise ValueError('positive cell sizes required')
    calibration = read(args.calibration) if args.calibration else None
    mechanisms_path = getattr(args, 'mechanisms', None)
    mechanisms = read(mechanisms_path) if mechanisms_path else None
    if mechanisms is not None:
        if not calibration or not isinstance(mechanisms, list) or not mechanisms:
            raise ValueError('nonempty mechanism list requires a composition model')
        for mechanism in mechanisms:
            if (mechanism.get('status') not in ('retained', 'unresolved', 'matched', 'insufficient_evidence', 'not_rejected')
                    or mechanism.get('radius_mode') not in ('min', 'mixed', 'max')
                    or mechanism.get('error_mode') not in ('deterministic', 'correlated', 'extreme', 'positive', 'negative', 'zero')
                    or not math.isfinite(mechanism.get('correlation_length', 150.))
                    or mechanism.get('correlation_length', 150.) <= 0):
                raise ValueError('invalid or rejected mechanism cannot enter composition pool')
    if calibration:
        for problem in (3, 4):
            fitted_distribution(calibration, problem)
    routes = args.routes.split(',')
    if 'robust' not in routes or len(set(routes)) != len(routes) or any(r not in ROUTES for r in routes):
        raise ValueError('routes must be unique known routes and include robust')
    pools = ['broad', 'stress'] + (['composition_fit'] if calibration else [])
    partitions = [('engineering', args.per_cell)] if args.profile == 'smoke' else [
        ('development', args.per_cell), ('confirmation', args.confirm_per_cell)]
    cases = []
    exclusions = []
    used_seeds, used_worlds = set(), set()
    for exclusion in getattr(args, 'exclude_manifest', []):
        data = read(exclusion)
        exclusions.append({'path': str(Path(exclusion).resolve()), 'sha256': digest(data)})
        for prior in old_worlds(data):
            used_seeds.add(prior['seed'])
            used_worlds.add(world_digest(prior))
    serial = 0
    for partition, size in partitions:
        for problem in (3, 4):
            for pool in pools:
                for index in range(size):
                    seed = args.seed + serial
                    case = make_case(seed, problem, pool, index, calibration)
                    if pool == 'composition_fit' and mechanisms:
                        mechanism = mechanisms[index % len(mechanisms)]
                        case.update(error_mode=mechanism['error_mode'], radius_mode=mechanism['radius_mode'],
                                    correlation_length=mechanism.get('correlation_length', 150.),
                                    mechanism_id=mechanism.get('id'), mechanism_status=mechanism['status'])
                        for source in case['sources']:
                            source['radius'] = (1000. if mechanism['radius_mode'] == 'min' else 1500.
                                                if mechanism['radius_mode'] == 'max' else
                                                random.Random(f"{seed}:radius:{source['channel']}").uniform(1000., 1500.))
                    world_hash = world_digest(case)
                    if seed in used_seeds or world_hash in used_worlds:
                        raise ValueError('seed/world overlap with a prior manifest; choose a fresh --seed')
                    used_seeds.add(seed)
                    used_worlds.add(world_hash)
                    case.update(case_id=f'diverse-{partition}-p{problem}-{pool}-{seed}', partition=partition)
                    case['world_sha256'] = world_hash
                    case['scenario_sha256'] = digest(case)
                    cases.append(case)
                    serial += 1
    manifest = {
        'schema_version': 1, 'profile': args.profile, 'master_seed': args.seed,
        'environment': 'self_built_only', 'routes': routes,
        'configs': {str(p): asdict(variant_configs(p)['joint_triangular']) for p in (3, 4)},
        'options': read(args.options) if args.options else {},
        'source_files': source_files(), 'cases': cases,
        'excluded_manifests': exclusions,
        'composition_snapshot': calibration,
        'mechanism_snapshot': mechanisms,
        'mechanism_input_sha256': hashlib.sha256(Path(mechanisms_path).read_bytes()).hexdigest() if mechanisms_path else None,
        'composition_input_sha256': hashlib.sha256(Path(args.calibration).read_bytes()).hexdigest() if calibration else None,
        'mechanisms': 'radius, geometry, direction and fixed error fields are uncalibrated sensitivity assumptions',
        'score': {'primary': 'complete-and-truth-audited', 'failure_penalty_s': PENALTY,
                  'comparisons': 'paired within pool/problem; no official population aggregate',
                  'selection': 'development only; max one candidate per problem; all pools mean penalty <= baseline and no extra failures'},
    }
    manifest['manifest_sha256'] = digest(manifest)
    write(destination / 'manifest.json', manifest)
    with zipfile.ZipFile(destination / 'source_snapshot.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in manifest['source_files']:
            archive.write(ROOT / name, name)
    cells = []
    for partition, _ in partitions:
        for problem in (3, 4):
            for pool in pools:
                rows = [c for c in cases if (c['partition'], c['problem'], c['pool']) == (partition, problem, pool)]
                cells.append({'partition': partition, 'problem': problem, 'pool': pool, 'n': len(rows),
                              'distinct_compositions': len({(len(c['sources']), c['n_directed']) for c in rows}),
                              'mechanism_cells': sorted({(c['layout'], c['radius_mode'], c['error_mode'], c['correlation_length']) for c in rows})})
    write(destination / 'design_coverage.json', {'cells': cells, 'note': 'Finite design; no claim of exhaustive support unless counts prove it.'})
    print(json.dumps({'manifest': str(destination / 'manifest.json'), 'scenarios': len(cases),
                      'partitions': {p: sum(c['partition'] == p for c in cases) for p, _ in partitions}}))


def _evaluate_job(job):
    case, route, config_data, options, output, code_hash, manifest_hash = job
    from diverse.policies import make_solver
    out = Path(output) / case['case_id'] / route
    out.mkdir(parents=True, exist_ok=False)
    write(out / 'case.json', case)
    sources = [Source(s['channel'], tuple(s['position']), s['radius'], s['direction_deg']) for s in case['sources']]
    env = LocalSimulator(sources, seed=case['seed'], error_mode=case['error_mode'],
                         correlation_length=case['correlation_length'], enforce_case_size=True)
    client = RobotClient('local-team', transport=env, log_path=str(out / 'requests.jsonl'))
    try:
        solver = make_solver(client, SolverConfig(**config_data), route,
                             decision_log=str(out / 'decisions.jsonl'), options=options)
        result = solver.run()
    finally:
        client.close_log()  # Windows requires closing the journal before compression/unlink.
    truth = env.summary()  # Externally inspected only after termination.
    violations = []
    truth_by_channel = {s.channel: s.position for s in sources}
    for event in solver.decisions:
        knowledge = event.get('knowledge')
        if knowledge and knowledge['channel'] in truth_by_channel:
            if not contains(knowledge['hull'], truth_by_channel[knowledge['channel']], tol=1e-4):
                violations.append({'sequence': event['sequence'], 'channel': knowledge['channel']})
    complete = (result['status'] == 'complete' and truth['all_cleared'] and not violations
                and abs(result['timing_residual_s']) < .001 and result['stop_evidence'] is not None)
    # Action costs independently grouped by policy reason, based on the public trace.
    costs, position, channel, previous_time = {}, (0., 0.), 1, 0.
    for event in solver.decisions:
        if event['event'] not in ('measure', 'clear'):
            continue
        reason = event.get('reason', event.get('certificate', {}).get('type', event['event']))
        item = costs.setdefault(reason, {'walk_m': 0., 'actions': 0, 'virtual_s': 0.})
        item['walk_m'] += distance(position, event['position'])
        item['actions'] += 1
        item['virtual_s'] += event['virtual_time_s'] - previous_time
        position, previous_time = event['position'], event['virtual_time_s']
    result.update(route=route, case_id=case['case_id'], pool=case['pool'], partition=case['partition'],
                  seed=case['seed'], layout=case['layout'], error_mode=case['error_mode'],
                  correlation_length=case['correlation_length'],
                  radius_mode=case['radius_mode'], source_total=len(sources), n_directed=case['n_directed'],
                  clear_fraction=truth['clearance_ratio'], audit_complete=complete,
                  hull_invariant_violations=violations, environment='self_built', environment_summary=truth,
                  scenario_sha256=case['scenario_sha256'], source_sha256=code_hash,
                  manifest_sha256=manifest_hash, options=options, cost_by_reason=costs,
                  failure_penalized_s=result['total_virtual_time_s'] if complete else PENALTY)
    write(out / 'result.json', result)
    # Logs remain complete, just compressed. No sensitive official logs are read.
    for name in ('requests.jsonl', 'decisions.jsonl'):
        path = out / name
        if path.exists():
            with path.open('rb') as src, gzip.open(str(path) + '.gz', 'wb') as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
            path.unlink()
    return result


def evaluate_job(job):
    case, route, config_data, options, output, code_hash, manifest_hash = job
    out = Path(output) / case['case_id'] / route
    start = time.monotonic()
    try:
        if digest(source_files()) != code_hash:
            raise RuntimeError('source drift before worker execution')
        result = _evaluate_job(job)
    except Exception as exc:
        # Infrastructure failures remain in the denominator and on disk.
        # Unknown actual costs stay null; only the prespecified loss is finite.
        stored = out / 'result.json'
        if stored.exists():
            result = read(stored)
            result['artifact_error'] = f'{type(exc).__name__}: {exc}'
        else:
            result = dict(case_id=case['case_id'], route=route, problem=case['problem'],
                          pool=case['pool'], partition=case['partition'], seed=case['seed'],
                          layout=case['layout'], radius_mode=case['radius_mode'], error_mode=case['error_mode'],
                          correlation_length=case['correlation_length'],
                          status='harness_failed', error=f'{type(exc).__name__}: {exc}',
                          traceback=traceback.format_exc(), audit_complete=False,
                          failure_penalized_s=PENALTY, total_virtual_time_s=None,
                          program_real_time_s=time.monotonic()-start, walk_distance_m=None,
                          measures=None, hull_invariant_violations=[], clear_fraction=None,
                          source_total=len(case['sources']), n_directed=case['n_directed'],
                          source_sha256=code_hash, scenario_sha256=case['scenario_sha256'],
                          manifest_sha256=manifest_hash, config=config_data, options=options,
                          environment='self_built', stop_evidence=None)
    result['source_valid'] = digest(source_files()) == code_hash
    if not result['source_valid']:
        result.update(audit_complete=False, failure_penalized_s=PENALTY, status='invalid_source_drift')
    write(out / 'result.json', result)
    return result


def expected_assignments(manifest, partition, selection=None):
    for case in manifest['cases']:
        if case['partition'] != partition:
            continue
        routes = manifest['routes'] if partition != 'confirmation' else list(dict.fromkeys(
            ['robust', selection['choices'][str(case['problem'])]]))
        # Independent fixed randomization of within-world execution ordering.
        routes = list(routes)
        random.Random(case['seed'] ^ 98217).shuffle(routes)
        for route in routes:
            yield case, route


def run(args):
    manifest = read(args.manifest)
    verify_manifest(manifest)
    selection = read(args.selection) if args.selection else None
    if args.partition == 'confirmation':
        if not selection or selection['manifest_sha256'] != manifest['manifest_sha256']:
            raise ValueError('confirmation requires frozen selection for this manifest')
        if selection.get('selection_sha256') != digest({k: v for k, v in selection.items() if k != 'selection_sha256'}):
            raise ValueError('selection modified after freezing')
        if any(r not in manifest['routes'] for r in selection['choices'].values()):
            raise ValueError('selection names an unregistered route')
        selection_path = Path(args.selection)
        if selection_path.resolve() != (Path(args.manifest).parent / 'selection.json').resolve():
            raise ValueError('use the canonical frozen selection.json beside the manifest')
        dev_results = selection_path.parent / 'development' / 'results.json'
        if not dev_results.exists() or digest(read(dev_results)) != selection['development_results_sha256']:
            raise ValueError('development results missing or changed since selection')
    elif selection:
        raise ValueError('selection applies only to confirmation')
    destination = Path(args.manifest).parent / args.partition
    destination.mkdir(exist_ok=True)
    if (destination / 'INVALID_SOURCE_DRIFT.json').exists():
        raise ValueError('this batch had source drift; preserve it and prepare a new experiment')
    # One launch at a time; a crashed process leaves a visible stale lock.
    lock = destination / 'RUNNING.lock'
    with lock.open('x', encoding='utf-8') as fp:
        fp.write(str(time.time()))
    started = time.monotonic()
    try:
        jobs, results = [], []
        code_hash = digest(manifest['source_files'])
        assignments = list(expected_assignments(manifest, args.partition, selection))
        if not assignments:
            raise ValueError('partition is not in this plan')
        for case, route in assignments:
            path = destination / case['case_id'] / route / 'result.json'
            if path.exists():
                result = read(path)
                if not args.resume:
                    raise ValueError('existing results; use --resume, never overwrite')
                if (result['manifest_sha256'] != manifest['manifest_sha256'] or result['route'] != route
                        or result['scenario_sha256'] != case['scenario_sha256']
                        or result['source_sha256'] != code_hash or result['config'] != manifest['configs'][str(case['problem'])]
                        or result['options'] != manifest['options'] or not result.get('source_valid')):
                    raise ValueError('stored result provenance mismatch')
                results.append(result)  # Including failures; no success-only retries.
            else:
                unfinished = path.parent
                if unfinished.exists():
                    if not args.resume:
                        raise ValueError('partial attempt exists; inspect it, then use --resume')
                    # Preserve interrupted logs separately; do not overwrite or delete them.
                    archive = destination / 'interrupted_attempts' / f'{case["case_id"]}-{route}-{time.time_ns()}'
                    base = destination.resolve()
                    if not unfinished.resolve().is_relative_to(base) or not archive.resolve().is_relative_to(base):
                        raise ValueError('attempt paths must remain in the experiment partition')
                    archive.parent.mkdir(parents=True, exist_ok=True)
                    unfinished.rename(archive)
                jobs.append((case, route, manifest['configs'][str(case['problem'])], manifest['options'],
                             str(destination), code_hash, manifest['manifest_sha256']))
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(evaluate_job, job): job for job in jobs}
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                if len(results) % 12 == 0 or len(results) == len(assignments):
                    print(json.dumps({'done': len(results), 'expected': len(assignments),
                                      'audited_complete': sum(r['audit_complete'] for r in results),
                                      'elapsed_s': round(time.monotonic() - started, 1)}), flush=True)
        try:
            verify_manifest(manifest)  # A code edit during work invalidates the whole batch.
            if not all(r['source_valid'] for r in results):
                raise ValueError('worker observed source drift')
        except ValueError as exc:
            write(destination / 'INVALID_SOURCE_DRIFT.json', {'reason': str(exc)})
            raise
        results.sort(key=lambda r: (r['case_id'], r['route']))
        write(destination / 'results.json', results)
        summary = summarize(results)
        summary.update(manifest_sha256=manifest['manifest_sha256'], expected_runs=len(assignments),
                       elapsed_s=time.monotonic() - started, workers=args.workers,
                       profile=manifest['profile'], partition=args.partition)
        write(destination / 'summary.json', summary)
        write_summary_md(destination / 'summary.md', summary)
    finally:
        lock.unlink(missing_ok=True)


def quantile(values, probability):
    values = sorted(values)
    if not values:
        return None
    x = (len(values) - 1) * probability
    return values[int(x)] * (1 - x % 1) + values[math.ceil(x)] * (x % 1)


def bootstrap_mean(values, seed=71293):
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    means = [statistics.mean(rng.choices(values, k=len(values))) for _ in range(1000)]
    return [quantile(means, .025), quantile(means, .975)]


def summarize(results):
    groups, paired = [], []
    keys = sorted({(r['problem'], r['pool'], r['route']) for r in results})
    lookup = {(r['case_id'], r['route']): r for r in results}
    for problem, pool, route in keys:
        rows = [r for r in results if (r['problem'], r['pool'], r['route']) == (problem, pool, route)]
        complete = [r for r in rows if r['audit_complete']]
        groups.append({'problem': problem, 'pool': pool, 'route': route, 'n': len(rows),
                       'complete': len(complete), 'failures': len(rows) - len(complete),
                       'mean_penalized_s': statistics.mean(r['failure_penalized_s'] for r in rows),
                       'mean_success_time_s': statistics.mean(r['total_virtual_time_s'] for r in complete) if complete else None,
                       'p95_success_time_s': quantile([r['total_virtual_time_s'] for r in complete], .95),
                       'max_program_real_s': max(r['program_real_time_s'] for r in rows),
                       'mean_walk_m': statistics.mean(r['walk_distance_m'] for r in rows if r['walk_distance_m'] is not None)
                       if any(r['walk_distance_m'] is not None for r in rows) else None,
                       'mean_measures': statistics.mean(r['measures'] for r in rows if r['measures'] is not None)
                       if any(r['measures'] is not None for r in rows) else None})
        if route == 'robust':
            continue
        pairs = [(lookup[(r['case_id'], 'robust')], r) for r in rows]
        deltas = [c['failure_penalized_s'] - b['failure_penalized_s'] for b, c in pairs]
        success_pairs = [(b, c) for b, c in pairs if b['audit_complete'] and c['audit_complete']]
        ratios = [100 * (c['total_virtual_time_s'] / b['total_virtual_time_s'] - 1) for b, c in success_pairs]
        paired.append({'problem': problem, 'pool': pool, 'route': route, 'n_pairs': len(pairs),
                       'mean_penalty_delta_s': statistics.mean(deltas), 'pointwise_95_ci_s': bootstrap_mean(deltas),
                       'new_failures': sum(b['audit_complete'] and not c['audit_complete'] for b, c in pairs),
                       'rescued_failures': sum(not b['audit_complete'] and c['audit_complete'] for b, c in pairs),
                       'dual_success_n': len(success_pairs), 'regressions': sum(x > 1e-6 for x in ratios),
                       'worst_regression_pct': max(ratios) if ratios else None,
                       'mean_paired_success_delta_pct': statistics.mean(ratios) if ratios else None})
    mechanism_groups = []
    for key in sorted({(r['problem'], r['pool'], r['route'], r['layout'], r['radius_mode'], r['error_mode'], r['correlation_length']) for r in results}):
        rows = [r for r in results if (r['problem'], r['pool'], r['route'], r['layout'], r['radius_mode'], r['error_mode'], r['correlation_length']) == key]
        mechanism_groups.append(dict(zip(('problem', 'pool', 'route', 'layout', 'radius_mode', 'error_mode', 'correlation_length'), key),
                                     n=len(rows), complete=sum(r['audit_complete'] for r in rows),
                                     mean_penalty_s=statistics.mean(r['failure_penalized_s'] for r in rows),
                                     precision='small stratum, descriptive only' if len(rows) < 10 else 'exploratory'))
    return {'runs': len(results), 'audited_complete': sum(r['audit_complete'] for r in results),
            'hull_violations': sum(len(r['hull_invariant_violations']) for r in results),
            'groups': groups, 'paired': paired, 'mechanism_groups': mechanism_groups,
            'interpretation': 'Synthetic paired experiments. Exploratory pointwise intervals, no multiplicity correction. Success-only statistics are conditional. Never official scores.'}


def write_summary_md(path, summary):
    lines = ['# 多元路线本地实验', '',
             f"完成并通过真值审计：{summary['audited_complete']}/{summary['runs']}；外包违规 {summary['hull_violations']}。", '',
             '各池是假设实验设计，不合并为官方总体。区间为探索性点态区间，成功子集指标存在选择效应。', '',
             '|问题|池|路线|全清/总数|平均惩罚秒|成功子集 P95 秒|',
             '|---|---|---|---:|---:|---:|']
    for row in summary['groups']:
        p95 = row['p95_success_time_s']
        lines.append(f"|{row['problem']}|{row['pool']}|{row['route']}|{row['complete']}/{row['n']}|{row['mean_penalized_s']:.2f}|{p95 if p95 is None else round(p95, 2)}|")
    lines += ['', '失败的 360000 秒仅为预先约定的惩罚损失，不是实际完成时间。逐案例成本分解和完整日志保留在同目录。']
    Path(path).write_text('\n'.join(lines) + '\n', encoding='utf-8')


def freeze(args):
    manifest = read(args.manifest)
    verify_manifest(manifest)
    if manifest['profile'] != 'research':
        raise ValueError('engineering smoke results cannot select a winner')
    parent = Path(args.manifest).parent
    if (parent / 'selection.json').exists():
        raise ValueError('selection is already frozen; no replacement after confirmation')
    results = read(parent / 'development' / 'results.json')
    expected = {(c['case_id'], r) for c, r in expected_assignments(manifest, 'development')}
    if len(results) != len(expected) or {(r['case_id'], r['route']) for r in results} != expected:
        raise ValueError('complete development results required')
    for result in results:
        case = next(c for c in manifest['cases'] if c['case_id'] == result['case_id'])
        if (result['manifest_sha256'] != manifest['manifest_sha256'] or not result.get('source_valid')
                or result['source_sha256'] != digest(manifest['source_files'])
                or result['scenario_sha256'] != case['scenario_sha256']
                or result['config'] != manifest['configs'][str(case['problem'])]
                or result['options'] != manifest['options']):
            raise ValueError('result manifest mismatch')
    summary = summarize(results)
    choices = {}
    for problem in (3, 4):
        candidates = []
        for route in manifest['routes']:
            rows = [r for r in summary['paired'] if r['problem'] == problem and r['route'] == route]
            if (rows and all(r['new_failures'] == 0 and r['mean_penalty_delta_s'] <= 0 for r in rows)
                    and any(r['mean_penalty_delta_s'] < -1e-6 for r in rows)):
                candidates.append((max(r['mean_penalty_delta_s'] for r in rows), route))
        choices[str(problem)] = min(candidates)[1] if candidates else 'robust'
    selection = {'manifest_sha256': manifest['manifest_sha256'], 'choices': choices,
                 'development_results_sha256': digest(results), 'selection_rule': manifest['score']['selection'],
                 'status': 'frozen_for_one_confirmation_only_not_official_deployment'}
    selection['selection_sha256'] = digest(selection)
    write(parent / 'selection.json', selection)
    print(json.dumps(selection, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare')
    p.add_argument('--profile', choices=['smoke', 'research'], default='research')
    p.add_argument('--output', required=True)
    p.add_argument('--calibration')
    p.add_argument('--mechanisms', help='frozen retained/unresolved mechanism list for composition pool only')
    p.add_argument('--options', help='JSON with public policy options, no hidden case parameters')
    p.add_argument('--exclude-manifest', action='append', default=[], help='reject reused seeds or hidden worlds from prior plans')
    p.add_argument('--routes', default=','.join(ROUTES))
    p.add_argument('--per-cell', type=int, default=40, help='scenarios per problem/pool in development or engineering')
    p.add_argument('--confirm-per-cell', type=int, default=20)
    p.add_argument('--seed', type=int, default=202609118230)
    p.set_defaults(func=prepare)
    p = commands.add_parser('run')
    p.add_argument('--manifest', required=True)
    p.add_argument('--partition', choices=['engineering', 'development', 'confirmation'], default='development')
    p.add_argument('--workers', type=int, default=2)
    p.add_argument('--resume', action='store_true')
    p.add_argument('--selection')
    p.set_defaults(func=run)
    p = commands.add_parser('freeze-selection')
    p.add_argument('--manifest', required=True)
    p.set_defaults(func=freeze)
    args = parser.parse_args()
    if hasattr(args, 'workers') and args.workers < 1:
        parser.error('workers must be positive')
    args.func(args)
