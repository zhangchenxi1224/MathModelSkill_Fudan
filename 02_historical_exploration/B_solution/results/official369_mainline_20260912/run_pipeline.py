"""369 official cases -> calibrated models -> 960 paired worlds -> 240 new worlds.

All action execution uses LocalSimulator. No official HTTP endpoint is called.
Run commands from the deployed directory; immutable source data is read only.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import gzip
import hashlib
import html
import json
import math
from pathlib import Path
import random
import statistics as st
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'vendor'))
sys.path.insert(0, str(ROOT))
PROJECT = Path(r'D:/2026WorkExperience/CUMCM2026Problems')
MAIN = PROJECT/'B_solution'
SIDE = PROJECT/'B_solution_side_research'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def world_sha(case):
    return digest({k: case[k] for k in ['problem', 'sources', 'error_field']})


def seed(*parts):
    return int(digest(['official369-v1-20260912', *parts])[:16], 16)


def mean(values):
    return st.mean(values) if values else None


def quantile(values, q):
    if not values:
        return None
    s = sorted(values); pos = (len(s)-1)*q; lo = int(pos); hi = math.ceil(pos)
    return s[lo]+(s[hi]-s[lo])*(pos-lo)


def interval(values, repeats=2000):
    if not values:
        return [None, None]
    if len(values) == 1:
        return [None, None]
    rng = random.Random(369240)
    boot = [st.mean(rng.choices(values, k=len(values))) for _ in range(repeats)]
    return [quantile(boot, .025), quantile(boot, .975)]


def feature_summary(extracted):
    """Public-observable, whole-case features; no latent distances/errors."""
    c = extracted['case']; rows = extracted['measurements']
    existing = set(c['confirmed_existing_channels'])
    coverage = [r for r in rows if r['phase'] == 'coverage' and not r['cleared_before']]
    features = {}
    for label, items in [('coverage_all', coverage),
                         ('coverage_existing', [r for r in coverage if r['channel'] in existing]),
                         ('local', [r for r in rows if r['phase'] == 'adaptive_local' and not r['cleared_before']])]:
        if items:
            for outcome in ['direction', 'near', 'no_signal']:
                features[label+'_'+outcome] = sum(r['outcome'] == outcome for r in items)/len(items)
    for kind in ['same_point_repeat', 'neighbor_1m']:
        ps = [r for r in extracted['proxies'] if r['reported_angle_delta_deg'] is not None and
              ((kind == 'same_point_repeat' and r['kind'] == kind) or
               (kind == 'neighbor_1m' and r['kind'] == 'nearest_observed_point' and .999999 <= r['separation_m'] <= 1.000001))]
        if ps:
            features[kind+'_reported_angle_delta_deg'] = mean([r['reported_angle_delta_deg'] for r in ps])
    contractions = [r['area_ratio'] for r in extracted['contractions'] if r['area_ratio'] is not None and r['outcome'] == 'direction']
    if contractions:
        features['direction_hull_area_ratio'] = mean(contractions)
    for key in ['total_virtual_time_s', 'walk_distance_m', 'measures', 'switches', 'clear_attempts']:
        if c.get(key) is not None:
            features[key] = c[key]
    stations = {}
    patterns = defaultdict(dict)
    for r in coverage:
        if r['channel'] in existing:
            patterns[r['channel']][r['station_id']] = int(r['visible'])
    station_ids = sorted({r['station_id'] for r in coverage}, key=lambda s: (0,int(s)) if str(s).isdigit() else (1,str(s)))
    for station in station_ids:
        vals = [row[station] for row in patterns.values() if station in row]
        if vals:
            features['station_visible__'+station] = mean(vals)
    if c['protocol'] == 'survey':
        pair_counts = Counter(); valid = 0
        for values in patterns.values():
            for a, b in zip(station_ids, station_ids[1:]):
                if a in values and b in values:
                    pair_counts[str(values[a])+str(values[b])] += 1; valid += 1
        if valid:
            for p in ['00', '01', '10', '11']:
                features['adjacent_station_pair_'+p] = pair_counts[p]/valid
    return features


def official_roots():
    return [MAIN/'results'/p for p in ['official_practice', 'round1/cases', 'round2/cases',
            'p3_latest_official/cases', 'p4_refined_official/cases']] + [SIDE/p for p in [
            'selected_dataset_iteration_20260911/runs/official_practice_10_each/cases',
            'synthesis_iteration_20260911/runs/official_practice_5_each/cases',
            'attachment_comparison_20260911/runs/official_p4_10_each_20260912/cases']]


def joint_map(model):
    return {(r['n'], r['n_directed']): r['probability'] for r in model['count_distribution']}


def predictive_score(rows, model):
    p = joint_map(model)
    return {'n': len(rows), 'mean_negative_log_likelihood': mean([-math.log(p[(r['N'], r['Ndir'])]) for r in rows]),
            'mean_brier': mean([sum(v*v for v in p.values())+1-2*p[(r['N'], r['Ndir'])] for r in rows])}


def prepare():
    from bsolver.calibration import extract_case, fit_composition
    from bsolver.round_experiments import make_scenario
    from policies import SPECS
    if (ROOT/'data/development960.json').exists():
        raise FileExistsError('Already prepared; use later commands, not a new split.')
    records = []; cases = []; codes = set(); protocol_jobs = []
    for root in official_roots():
        for p in sorted(root.glob('*/post_exit_audit.json')):
            audit = read(p)
            if audit.get('environment') != 'official_practice' or audit.get('case_code') in codes:
                continue
            codes.add(audit['case_code'])
            item = extract_case(p.parent)
            c = item['case']; c['batch'] = str(root.relative_to(PROJECT))
            c['original_split'] = c['split']; c['original_issues'] = c.pop('issues')
            c['split'] = 'fit'  # New explicit calibration scope, never source-file mutation.
            cases.append(c)
            records.append({'case': c, 'features': feature_summary(item)})
            if root == MAIN/'results/round1/cases':
                for hypothesis, mode in [('H1', 'correlated'), ('H2', 'extreme')]:
                    scenario = make_scenario(seed('protocol', c['case_code']), c['problem'], c['N'], c['Ndir'],
                        pool='protocol_composition_matched', model='official_case_N_Ndir', error_mode=mode,
                        index=len(protocol_jobs), partition='calibration')
                    scenario['case_id'] = 'matched-'+c['case_code']+'-'+hypothesis
                    scenario['scenario_sha256'] = digest({k:v for k,v in scenario.items() if k != 'scenario_sha256'})
                    protocol_jobs.append({'case': scenario, 'hypothesis': hypothesis, 'variant': 'protocol',
                        'official_case_code': c['case_code'], 'protocol': c['protocol'], 'survey_seed': c['survey_seed'],
                        'phase': 'protocol_checks'})
            if len(cases) % 50 == 0:
                print('Extracted official', len(cases), flush=True)
    assert len(cases) == 369, f'Expected frozen 369 cases, got {len(cases)}'
    assert len(protocol_jobs) == 320
    old_fit = [c for c in cases if c['batch'] == 'B_solution/results/round1/cases' and c['original_split'] == 'fit']
    # Windows uses backslashes in relative paths.
    old_fit = [c for c in cases if c['batch'].replace('\\','/') == 'B_solution/results/round1/cases' and c['original_split'] == 'fit']
    assert len(old_fit) == 120, len(old_fit)
    old_ids = {c['case_code'] for c in old_fit}
    validation_ids = set()
    for p in [3, 4]:
        eligible = sorted([c for c in cases if c['problem'] == p and c['case_code'] not in old_ids],
                          key=lambda c: seed('calibration_holdout', c['case_code']))
        validation_ids.update(c['case_code'] for c in eligible[:round(.2*len(eligible))])
    new_fit = [c for c in cases if c['case_code'] not in validation_ids]
    models = {'old120': {}, 'development_fit': {}, 'all369': {}}
    checks = {}
    for p in [3, 4]:
        for label, rows in [('old120', old_fit), ('development_fit', new_fit), ('all369', cases)]:
            models[label][str(p)] = fit_composition(rows, p, prior_effective_cases=2., bootstrap_repeats=0)
        holdout = [c for c in cases if c['problem'] == p and c['case_code'] in validation_ids]
        checks[str(p)] = {label: predictive_score(holdout, models[label][str(p)]) for label in ['old120', 'development_fit']}
    write(ROOT/'calibration/official369.json', records)
    write(ROOT/'calibration/composition_models.json', models)
    write(ROOT/'calibration/composition_checks.json', {'fixed_prior_effective_cases': 2.,
        'old_fit_cases': 120, 'development_fit_cases': len(new_fit), 'development_validation_cases': len(validation_ids),
        'validation_case_codes': sorted(validation_ids), 'checks': checks,
        'final_generation_model': 'all369',
        'interpretation': 'Fixed model family compared on held-out non-old-fit cases; after this development check, fit all369 for new simulation. This is not new official validation.'})
    paths = [MAIN/'results'/p/'manifest.json' for p in ['round1/local', 'round1/stage2', 'round1/stage3', 'p4_refinement']]
    paths += [SIDE/b/'campaign/confirmation_cases.json' for b in ['selected_dataset_iteration_20260911', 'synthesis_iteration_20260911']]
    worlds = {}; excluded = set()
    for path in paths:
        data = read(path)
        rows = data['scenarios'] if isinstance(data, dict) else [r['case'] for r in data]
        for c in rows:
            w = world_sha(c); excluded.add(w)
            if c['composition_model'] == 'smoothed_joint':
                worlds.setdefault(w, {'case': c, 'hypothesis': 'H1' if c['error_mode'] == 'correlated' else 'H2',
                                      'world_sha256': w, 'source_manifest': str(path), 'role': 'seen_development'})
    assert len(worlds) == 960, len(worlds)
    development = list(worlds.values())
    for row in development:
        c = row['case']; pair = (c['n'], c['n_directed']); p = str(c['problem'])
        row['composition_weight'] = joint_map(models['all369'][p])[pair]/joint_map(models['old120'][p])[pair]
    write(ROOT/'data/development960.json', development)
    write(ROOT/'data/previous_world_hashes.json', sorted(excluded))
    write(ROOT/'data/protocol320.json', protocol_jobs)
    protocol = {'scope': 'local only', 'official_cases': 369, 'official_by_problem': dict(Counter(c['problem'] for c in cases)),
        'development_worlds': 960, 'development_by_problem': dict(Counter(r['case']['problem'] for r in development)),
        'variants': SPECS, 'planned_development_runs': sum(len(SPECS[r['case']['problem']]) for r in development),
        'confirmation_worlds': 240, 'confirmation_per_problem': 120, 'confirmation_H_per_problem': 60,
        'confirmation_seed_namespace': 'official369-confirm-v1-20260912',
        'selection_rule': 'All complete and zero maintained-hull violations; both H means and ordinary overall mean no worse than main; rank eligible by new-composition importance-weighted mean paired delta. At most one candidate per problem; otherwise retain main.',
        'confirmation_rule': 'Run frozen main and selected candidate on newly generated worlds. Accept only if every run completes without hull violation, overall paired CI95 upper<0, and neither H mean regresses; otherwise retain main and report inconclusive/rejected. No reselection on confirmation.',
        'mechanisms': {'H1': 'correlated, length150m', 'H2': 'extreme grid, length150m'},
        'unidentified': ['exact source positions', 'radius distribution within1000-1500', 'orientation distribution', 'unique error generator'],
        'protocol_checks': '320 local executions: each first-round official composition/protocol matched once under each H; layouts synthetic, not official replay.',
        'weights': 'new all369 smoothed joint / old120 smoothed joint; descriptive importance weighting, not proof of population fit',
        'source_snapshot': file_sha(ROOT/'source_snapshot.json'), 'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    write(ROOT/'protocol.json', protocol)
    print(json.dumps(protocol, ensure_ascii=False, indent=2), flush=True)


def run_job(job):
    from bsolver.calibration import extract_case
    from bsolver.geometry import contains
    from bsolver.protocol import RobotClient
    from bsolver.round_experiments import frozen_baseline
    from bsolver.simulator import FixedErrorField, LocalSimulator, Source
    from bsolver.strategy import Solver
    from bsolver.survey import SurveySolver
    from policies import make_policy
    case = job['case']; variant = job['variant']; phase = job['phase']
    dest = ROOT/'runs'/phase/'cases'/case['case_id']/variant
    if (dest/'result.json').exists():
        saved = read(dest/'result.json')
        if saved['world_sha256'] != world_sha(case) or saved.get('code_sha256') != job['code_sha256']:
            raise ValueError('Existing output belongs to a different world or code')
        return saved
    dest.mkdir(parents=True, exist_ok=True)
    started = time.monotonic(); client = None; policy = None; env = None
    try:
        field = case['error_field']
        env = LocalSimulator([Source(**s) for s in case['sources']], robot_id='local-team', enforce_case_size=True,
             error_field=FixedErrorField(field['seed'], field['mode'], field['correlation_length_m']))
        client = RobotClient('local-team', transport=env, log_path=dest/'requests.jsonl')
        if variant == 'protocol':
            conf = frozen_baseline(case['problem'])
            policy = (Solver(client, conf, dest/'decisions.jsonl') if job['protocol'] == 'baseline' else
                      SurveySolver(client, conf, survey_seed=job['survey_seed'], sample_count=2, decision_log=dest/'decisions.jsonl'))
        else:
            policy = make_policy(client, case['problem'], variant, dest/'decisions.jsonl')
        result = policy.run()
        client.close_log()
        truth = env.summary()
        actual_locations = {s['channel']: s['position'] for s in case['sources']}
        violations = []
        for event in policy.decisions:
            k = event.get('knowledge')
            if k and k['channel'] in actual_locations and not contains(k['hull'], actual_locations[k['channel']], tol=1e-4):
                violations.append([event['sequence'], k['channel']])
        stats = client.stats
        independent_time = stats['walk_distance']/5+stats['switches']+5*stats['measures']+3*stats['clear_attempts']+2*stats['successes']
        complete = result['status'] == 'complete' and truth['all_cleared'] and not violations and abs(result['total_virtual_time_s']-independent_time)<.002
        result.update(complete=bool(complete), n=case['n'], nd=case['n_directed'],
            source_total=case['n'], cleared=stats['successes'], walk_distance_m=stats['walk_distance'],
            measures=stats['measures'], switches=stats['switches'], clear_attempts=stats['clear_attempts'],
            failed_clear_attempts=stats['failures'], mean_time_per_source_s=result['total_virtual_time_s']/case['n'],
            timing_residual_s=result['total_virtual_time_s']-independent_time,
            hull_invariant_violations=violations, stats=policy.stats,
            loss_s=result['total_virtual_time_s'] if complete else 360000.)
    except Exception as exc:
        result = {'complete': False, 'status': 'harness_failed', 'error': f'{type(exc).__name__}: {exc}',
                  'traceback': traceback.format_exc(), 'loss_s': 360000.,
                  'total_virtual_time_s': client.virtual_time if client else 0.,
                  'program_real_time_s': time.monotonic()-started,
                  'n': case['n'], 'nd': case['n_directed'], 'hull_invariant_violations': []}
    finally:
        if client:
            client.close_log()
    result.update(case_id=case['case_id'], problem=case['problem'], variant=variant,
                  phase=phase, hypothesis=job['hypothesis'], world_sha256=world_sha(case),
                  code_sha256=job['code_sha256'], composition_weight=job.get('composition_weight',1.),
                  environment='local_synthetic', official_case_code=job.get('official_case_code'))
    write(dest/'result.json', result)
    if variant == 'protocol':
        write(dest/'assignment.json', {'environment': 'local', 'case_id': case['case_id'], 'problem': case['problem'],
              'protocol': job['protocol'], 'survey_seed': job['survey_seed'], 'model_candidate': job['hypothesis'],
              'split': 'development', 'common_case_id': job['official_case_code']})
        write(dest/'post_exit_audit.json', {'environment': 'local', 'N': case['n'], 'Ndir': case['n_directed']})
        result['protocol_features'] = feature_summary(extract_case(dest))
        result['protocol'] = job['protocol']
        write(dest/'result.json', result)
    for filename in ['requests.jsonl', 'decisions.jsonl']:
        path = dest/filename
        if path.exists():
            with gzip.open(str(path)+'.gz', 'wb', compresslevel=3) as fp:
                fp.write(path.read_bytes())
            path.unlink()  # Only newly generated run log, after lossless compression.
    return result


def code_digest():
    paths = [ROOT/'run_pipeline.py', ROOT/'policies.py'] + sorted((ROOT/'vendor').rglob('*.py')) + sorted((ROOT/'side_methods').rglob('*.py'))
    return digest({str(p.relative_to(ROOT)): file_sha(p) for p in paths})


def execute(phase, workers=6, limit=None):
    from policies import SPECS
    if phase == 'protocol_checks':
        jobs = read(ROOT/'data/protocol320.json')
    elif phase == 'development':
        jobs = [{**r, 'variant': variant, 'phase': phase} for r in read(ROOT/'data/development960.json')
                for variant in SPECS[r['case']['problem']]]
    elif phase == 'confirmation':
        selection = read(ROOT/'selection.json')
        jobs = []
        for r in read(ROOT/'data/confirmation240.json'):
            selected = selection['selected'][str(r['case']['problem'])]
            variants = ['main'] + ([selected] if selected != 'main' else [])
            jobs += [{**r, 'variant': variant, 'phase': phase} for variant in variants]
    elif phase == 'smoke':
        rows = read(ROOT/'data/development960.json')
        sample = [next(r for r in rows if r['case']['problem']==p and r['case']['n']==n and r['hypothesis']==h)
                  for p,n,h in [(3,10,'H1'),(3,16,'H2'),(4,10,'H1'),(4,16,'H2')]]
        jobs = [{**r,'variant':v,'phase':phase} for r in sample for v in SPECS[r['case']['problem']]]
    else:
        raise ValueError(phase)
    if limit is not None:
        jobs = jobs[:limit]
    current_code = code_digest()
    for j in jobs:
        j['code_sha256'] = current_code
    write(ROOT/'runs'/phase/'execution_plan.json', {'phase':phase,'jobs':len(jobs),'workers':workers,'code_sha256':current_code,
          'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
    results=[]; started=time.monotonic()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_job,j): j for j in jobs}
        for future in as_completed(futures):
            row = future.result();results.append(row)
            if not row['complete']:
                print('FAILED',row['case_id'],row['variant'],row.get('error'),flush=True)
            if len(results)%20==0 or len(results)==len(jobs):
                progress={'phase':phase,'finished':len(results),'planned':len(jobs),'failed':sum(not r['complete'] for r in results),
                          'wall_s':round(time.monotonic()-started,2)}
                write(ROOT/'runs'/phase/'progress.json',progress);print(json.dumps(progress),flush=True)
    results.sort(key=lambda r:(r['problem'],r['case_id'],r['variant']))
    write(ROOT/'runs'/phase/'results.json',results)
    if code_digest()!=current_code:
        raise ValueError('Code changed during a batch')
    return results


def paired_summary(results):
    lookup={(r['world_sha256'],r['variant']):r for r in results}; out=[]
    for p in [3,4]:
        variants=sorted({r['variant'] for r in results if r['problem']==p and r['variant']!='main'})
        for variant in variants:
            rows=[r for r in results if r['problem']==p and r['variant']==variant]
            pairs=[(lookup[(r['world_sha256'],'main')],r) for r in rows]
            deltas=[b['loss_s']-a['loss_s'] for a,b in pairs]
            weights=[r['composition_weight'] for r in rows]
            by_h={h:mean([b['loss_s']-a['loss_s'] for a,b in pairs if b['hypothesis']==h]) for h in ['H1','H2']}
            n=len(rows)
            entry={'problem':p,'variant':variant,'n':n,'complete':sum(r['complete'] for r in rows),
                   'baseline_complete':sum(a['complete'] for a,b in pairs),
                   'mean_main_s':mean([a['loss_s'] for a,b in pairs]),'mean_candidate_s':mean([b['loss_s'] for a,b in pairs]),
                   'mean_delta_s':mean(deltas),'ci95_delta_s':interval(deltas),'H_mean_delta_s':by_h,
                   'regressions':sum(d>1e-5 for d in deltas),'worst_regression_s':max(deltas),
                   'weighted_delta_s':sum(d*w for d,w in zip(deltas,weights))/sum(weights),
                   'weight_effective_sample_size':sum(weights)**2/sum(w*w for w in weights)}
            for k in ['walk_distance_m','measures','switches','clear_attempts','failed_clear_attempts','program_real_time_s']:
                entry[k+'_delta']=mean([b[k]-a[k] for a,b in pairs if k in a and k in b])
            out.append(entry)
    return out


def select_and_prepare_confirmation():
    from bsolver.round_experiments import categorical, make_scenario
    if (ROOT/'selection.json').exists():
        raise FileExistsError('Selection already frozen')
    rows=read(ROOT/'runs/development/results.json')
    assert len(rows)==3460, len(rows)
    summary=paired_summary(rows); selected={}; reasons={}
    for p in [3,4]:
        eligible=[r for r in summary if r['problem']==p and r['complete']==r['n'] and r['baseline_complete']==r['n']
                  and r['mean_delta_s']<=0 and all(d is not None and d<=0 for d in r['H_mean_delta_s'].values())
                  and r['weighted_delta_s']<0]
        winner=min(eligible,key=lambda r:(r['weighted_delta_s'],r['variant'])) if eligible else None
        selected[str(p)]=winner['variant'] if winner else 'main'
        reasons[str(p)]='eligible minimum weighted paired cost' if winner else 'no eligible improvement; retain main'
    selection={'selected':selected,'reasons':reasons,'development_results_sha256':file_sha(ROOT/'runs/development/results.json'),
               'code_sha256':code_digest(),'summary':summary,'frozen_before_confirmation':True}
    write(ROOT/'selection.json',selection)
    models=read(ROOT/'calibration/composition_models.json')['all369']
    excluded=set(read(ROOT/'data/previous_world_hashes.json'))
    for branch in ['selected_dataset_iteration_20260911','synthesis_iteration_20260911']:
        for name in ['confirmation_cases.json','stress_cases.json']:
            for row in read(SIDE/branch/'campaign'/name):excluded.add(world_sha(row['case']))
    fresh=[]
    for p in [3,4]:
        for h,mode in [('H1','correlated'),('H2','extreme')]:
            rng=random.Random(seed('official369-confirm-v1-20260912',p,h))
            for i in range(60):
                n,nd=categorical(rng,models[str(p)]['count_distribution'])
                c=make_scenario(rng.getrandbits(63),p,n,nd,pool='official369_confirmation',model='smoothed_joint_369',
                               error_mode=mode,index=i,partition='confirmation')
                w=world_sha(c)
                if w in excluded:raise ValueError('Fresh world collision')
                excluded.add(w)
                fresh.append({'case':c,'hypothesis':h,'world_sha256':w,'role':'fresh_confirmation',
                              'composition_weight':1.})
    write(ROOT/'data/confirmation240.json',fresh)
    print(json.dumps({'selected':selected,'fresh_worlds':len(fresh),'development_summary':summary},ensure_ascii=False,indent=2),flush=True)


def protocol_report():
    official=read(ROOT/'calibration/official369.json')
    local=read(ROOT/'runs/protocol_checks/results.json')
    old={r['case']['case_code']:r for r in official if r['case']['batch'].replace('\\','/')=='B_solution/results/round1/cases'}
    comparisons=[]
    for p in [3,4]:
        for protocol in ['baseline','survey']:
            for h in ['H1','H2']:
                local_rows=[r for r in local if r['problem']==p and r['protocol']==protocol and r['hypothesis']==h]
                metrics=sorted({k for r in local_rows for k in r['protocol_features']})
                for metric in metrics:
                    pairs=[(old[r['official_case_code']]['features'][metric],r['protocol_features'][metric])
                           for r in local_rows if metric in old[r['official_case_code']]['features'] and metric in r['protocol_features']]
                    if not pairs:continue
                    deltas=[b-a for a,b in pairs]
                    comparisons.append({'problem':p,'protocol':protocol,'hypothesis':h,'metric':metric,
                        'n_matched_compositions':len(pairs),'official_mean':mean([a for a,b in pairs]),
                        'local_mean':mean([b for a,b in pairs]),'mean_local_minus_official':mean(deltas),
                        'ci95_delta':interval(deltas,repeats=600)})
    decision={'models_retained':['H1','H2'],'radius':'uniform1000-1500, unresolved representative',
              'positions':'uniform disk area, unresolved representative','orientations':'uniform, unresolved representative',
              'composition':'updated from all369',
              'meaning':'Exploratory matched-composition protocol checks; intervals cluster by complete official case. No parameter tuned to match time. Persistent discrepancies are retained in the report, not converted to a claim of validation.'}
    write(ROOT/'calibration/protocol_comparisons.json',comparisons)
    write(ROOT/'calibration/model_decision.json',decision)
    important=[r for r in comparisons if not r['metric'].startswith('station_visible__')]
    print(json.dumps(important,ensure_ascii=False,indent=2),flush=True)


def report():
    development=read(ROOT/'runs/development/results.json')
    confirmation=read(ROOT/'runs/confirmation/results.json')
    selected=read(ROOT/'selection.json')
    comparisons=paired_summary(confirmation)
    verdict={}
    for p in [3,4]:
        row=next((r for r in comparisons if r['problem']==p),None)
        accepted=bool(row and row['complete']==row['n'] and row['baseline_complete']==row['n'] and
            row['ci95_delta_s'][1]<0 and all(x<=0 for x in row['H_mean_delta_s'].values()))
        verdict[str(p)]={'tested_candidate':selected['selected'][str(p)],'accepted':accepted,
                         'recommended':selected['selected'][str(p)] if accepted else 'main',
                         'reason':'confirmed improvement under retained synthetic models' if accepted else 'no confirmed improvement; retain main'}
    write(ROOT/'report/summary.json',{'development':paired_summary(development),'confirmation':comparisons,'verdict':verdict})
    headers=['题','阶段','方案','全清','总虚拟时间','平均时间/源','移动米','检测','换频','清除尝试','失败','程序秒']
    table=[]
    for phase,rows in [('开发960',development),('新确认240',confirmation)]:
        for p in [3,4]:
            for v in sorted({r['variant'] for r in rows if r['problem']==p}):
                rs=[r for r in rows if r['problem']==p and r['variant']==v]
                table.append([p,phase,v,f'{sum(r["complete"] for r in rs)}/{len(rs)}']+
                    [round(mean([r[k] for r in rs if k in r]),3) if any(k in r for r in rs) else None for k in
                     ['total_virtual_time_s','mean_time_per_source_s','walk_distance_m','measures','switches','clear_attempts','failed_clear_attempts','program_real_time_s']])
    md=['# 369→960→240 主线实验实际结果','',
        '全部策略运行均为本地合成环境；本轮未新增官方演练。369份官方记录用于更新模型判断，无法恢复精确官方隐藏环境。', '',
        '| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|']
    md += ['| '+' | '.join(map(str,row))+' |' for row in table]
    md += ['', '## 新场景确认', '', '|题|候选|候选−主线/秒|95%配对区间|退步局数|最坏退步/秒|保留|','|---|---|---:|---|---:|---:|---|']
    for r in comparisons:
        md.append(f'|{r["problem"]}|{r["variant"]}|{r["mean_delta_s"]:.2f}|{[round(x,2) for x in r["ci95_delta_s"]]}|{r["regressions"]}/{r["n"]}|{r["worst_regression_s"]:.2f}|{verdict[str(r["problem"])]["accepted"]}|')
    md += ['', 'H1/H2分别检验；选择仅使用开发集。确认集不用于换选第二候选。零失败是本批结果，不是总体故障率证明。',
           '失败的完整结果与退出耗时保留，选择时使用360000秒失败损失，不能用失败局短耗时获取收益。', '',
           '## 文件', '', '- `calibration/official369.json`：逐官方案例来源与可观测特征。',
           '- `calibration/composition_models.json`：旧120、开发拟合、全369组成模型。',
           '- `calibration/composition_checks.json`：不复用旧拟合数据的开发检验。',
           '- `calibration/protocol_comparisons.json`：同协议、同数量组成的320次本地反馈比较。',
           '- `data/development960.json` 与 `data/confirmation240.json`：可运行场景。',
           '- `runs/*/results.json`、`runs/*/cases/*/*/requests.jsonl.gz`：全部结果与原始请求。',
           '- `selection.json`、`report/summary.json`：开发选择与新确认判断。']
    (ROOT/'report/results.md').write_text('\n'.join(md),encoding='utf-8')
    all_rows=development+confirmation
    def html_table(h, rows):
        return '<table><thead><tr>'+''.join('<th>'+html.escape(str(x))+'</th>' for x in h)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table>'
    page='<!doctype html><meta charset="utf-8"><title>369→960→240 实验结果</title><style>body{font:15px system-ui;margin:36px;color:#182536}table{border-collapse:collapse;width:100%;margin:20px 0}th,td{padding:10px;border-bottom:1px solid #ddd;text-align:right}th{background:#eef4fa;position:sticky;top:0}td:nth-child(3){text-align:left}details{margin:24px 0}</style><h1>369→960→240 主线本地实验</h1><p>官方记录用于校准；下表均为实际执行的本地仿真结果。</p>'+html_table(headers,table)
    rawheaders=['场景','题','模型','方案','清除','总虚拟秒','平均秒/源','移动米','检测','换频','清除尝试','失败','程序秒']
    for phase in ['development','confirmation']:
        page+='<details><summary>'+phase+' 全部逐局数据</summary>'+html_table(rawheaders,[[r['case_id'],r['problem'],r['hypothesis'],r['variant'],str(r.get('cleared','?'))+'/'+str(r['n'])]+[round(r[k],3) if isinstance(r.get(k),(int,float)) else r.get(k) for k in ['total_virtual_time_s','mean_time_per_source_s','walk_distance_m','measures','switches','clear_attempts','failed_clear_attempts','program_real_time_s']] for r in all_rows if r['phase']==phase])+'</details>'
    (ROOT/'report/results.html').write_text(page,encoding='utf-8')
    print(json.dumps({'verdict':verdict,'confirmation':comparisons},ensure_ascii=False,indent=2),flush=True)


def smoke_geometry():
    from policies import disk_disjoint
    square=[(0.,0.),(1.,0.),(1.,1.),(0.,1.)]
    assert not disk_disjoint(square,(.5,.5))
    assert not disk_disjoint(square,(21.,.5))
    assert disk_disjoint(square,(21.000001,.5))
    assert not disk_disjoint([(0.,0.)],(20.,0.))
    assert disk_disjoint([(0.,0.),(1.,0.)],(0.,20.001))
    print('Exact disk/hull separation boundary checks passed',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','smoke','protocol_checks','protocol_report','development','select','confirmation','report'])
    parser.add_argument('--workers',type=int,default=6)
    parser.add_argument('--limit',type=int)
    args=parser.parse_args()
    if args.command=='prepare':prepare()
    elif args.command=='select':select_and_prepare_confirmation()
    elif args.command=='protocol_report':protocol_report()
    elif args.command=='report':report()
    else:
        if args.command=='smoke':smoke_geometry()
        execute(args.command,workers=args.workers,limit=args.limit)
