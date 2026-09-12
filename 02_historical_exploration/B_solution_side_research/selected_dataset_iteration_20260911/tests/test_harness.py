"""Independent replay and evaluation checks; no official requests are made."""
from pathlib import Path
from collections import Counter
from types import SimpleNamespace
import hashlib
import json
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "dataset" / "official_composition_H1_H2_360_20260911"
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DATASET / "runtime"))

import experiment as harness
from bsolver.strategy import Solver, SolverConfig
from bsolver.nosignal_sensing import NoSignalSolver, NoSignalConfig
from bsolver.protocol import RobotClient as OriginalClient
from bsolver.simulator import LocalSimulator as OriginalSimulator
from bsolver.simulator import Source as OriginalSource
from bsolver.simulator import FixedErrorField as OriginalField
from bsolver_frozen.protocol import RobotClient as PackagedClient
from bsolver_frozen.simulator import LocalSimulator as PackagedSimulator
from bsolver_frozen.simulator import Source as PackagedSource
from bsolver_frozen.simulator import FixedErrorField as PackagedField
from methods.solver import make_solver


@pytest.fixture(scope="module")
def worlds():
    return harness.input_cases(DATASET)


def pick(worlds, problem, hypothesis):
    return next(r for r in worlds if r['case']['problem'] == problem
                and r['hypothesis'] == hypothesis)


def public_actions(solver):
    actions = []
    for event in solver.decisions:
        if event['event'] not in ('measure', 'clear'):
            continue
        action = {k: event[k] for k in ('event', 'position', 'channel', 'response',
                                       'virtual_time_s') if k in event}
        if 'response' in action:
            action['response'] = {k: v for k, v in action['response'].items()
                                  if k != 'real_timestamp_ms'}
        actions.append(action)
    return json.loads(json.dumps(actions))


def runtime_hashes():
    return {str(Path(name).relative_to('runtime')).replace('\\', '/'): value
            for name, value in harness.read(DATASET / 'manifest.json')['runtime_files_sha256'].items()}


def test_dataset_is_exact_360_balanced_reused_worlds(worlds):
    assert len(worlds) == 360
    assert Counter((r['hypothesis'], r['case']['problem']) for r in worlds) == {
        ('H1', 3): 90, ('H1', 4): 90, ('H2', 3): 90, ('H2', 4): 90}
    assert all(r['role'] == 'development_regression' for r in worlds)
    assert len({harness.world_digest(r['case']) for r in worlds}) == 360
    assert any(r['case']['partition'] == 'confirmation' for r in worlds)
    assert all(r['case']['error_field']['seed'] != r['case']['seed'] for r in worlds)


def test_frozen_engine_and_nosignal_bytes_match_evidence():
    manifest = harness.read(DATASET / 'manifest.json')
    for name, expected in manifest['runtime_files_sha256'].items():
        assert hashlib.sha256((DATASET / name).read_bytes()).hexdigest() == expected
        local = ROOT / 'vendor' / 'bsolver' / Path(name).name
        assert hashlib.sha256(local.read_bytes()).hexdigest() == expected
    assert hashlib.sha256((ROOT / 'vendor/bsolver/nosignal_sensing.py').read_bytes()).hexdigest() == (
        '2073393686e1538ad046965cbc3b4cf1f16b6882a157b6efa5c7855ff50be6c0')


@pytest.mark.parametrize('problem,hypothesis', [(3, 'H1'), (4, 'H2')])
def test_packaged_protocol_and_factory_match_frozen_champion_actions(worlds, problem, hypothesis):
    case = pick(worlds, problem, hypothesis)['case']
    definition = case['error_field']
    env1 = OriginalSimulator([OriginalSource(**s) for s in case['sources']],
        robot_id='local-team', enforce_case_size=True,
        error_field=OriginalField(definition['seed'], definition['mode'], definition['correlation_length_m']))
    env2 = PackagedSimulator([PackagedSource(**s) for s in case['sources']],
        robot_id='local-team', enforce_case_size=True,
        error_field=PackagedField(definition['seed'], definition['mode'], definition['correlation_length_m']))
    client1 = OriginalClient('local-team', transport=env1)
    client2 = PackagedClient('local-team', transport=env2)
    cfg = SolverConfig(problem=problem, local_measure_limit=1)
    reference = (Solver(client1, cfg) if problem == 3 else
                 NoSignalSolver(client1, cfg, nosignal_config=NoSignalConfig()))
    candidate = make_solver(client2, problem, {'implementation': 'champion'})
    first, second = reference.run(), candidate.run()
    assert first['status'] == second['status'] == 'complete'
    assert env1.summary()['all_cleared'] and env2.summary()['all_cleared']
    assert public_actions(reference) == public_actions(candidate)
    for field in ('total_virtual_time_s', 'walk_distance_m', 'measures', 'switches',
                  'clear_attempts', 'clear_successes', 'fallback_targets', 'stop_evidence'):
        assert first[field] == second[field], field


@pytest.mark.parametrize('hypothesis', ['H1', 'H2'])
def test_fixed_field_repeats_and_case_seed_is_not_error_seed(worlds, hypothesis):
    case = pick(worlds, 3, hypothesis)['case']
    definition = case['error_field']
    correct = PackagedField(definition['seed'], definition['mode'], definition['correlation_length_m'])
    wrong = PackagedField(case['seed'], definition['mode'], definition['correlation_length_m'])
    positions = [(x, y) for x in (-1200., -125., 0., 72., 1444.) for y in (-1000., 0., 333.)]
    outputs = [correct(1, p) for p in positions]
    assert outputs == [correct(1, p) for p in positions]
    assert all(-1 <= value <= 1 for value in outputs)
    assert outputs != [wrong(1, p) for p in positions]


def test_worker_honors_error_field_and_matches_direct_champion(worlds, tmp_path):
    row = pick(worlds, 3, 'H1')
    case = row['case']
    spec = {'implementation': 'champion'}
    plan = {'output': str(tmp_path), 'dataset_runtime': str(DATASET / 'runtime'),
            'source_files': harness.files(), 'plan_sha256': 'test-worker-replay',
            'dataset_runtime_hashes': runtime_hashes()}
    actual = harness.worker((row, 'champion', spec, plan))
    definition = case['error_field']
    env = PackagedSimulator([PackagedSource(**s) for s in case['sources']], robot_id='local-team',
        enforce_case_size=True, error_field=PackagedField(definition['seed'], definition['mode'],
                                                       definition['correlation_length_m']))
    solver = make_solver(PackagedClient('local-team', transport=env), 3, spec)
    expected = solver.run()
    assert actual['audit_complete'], actual
    assert actual['scenario_sha256'] == case['scenario_sha256']
    assert actual['world_sha256'] == harness.world_digest(case)
    assert actual['total_virtual_time_s'] == expected['total_virtual_time_s']
    import gzip
    path = tmp_path / 'cases' / case['case_id'] / 'champion' / 'decisions.jsonl.gz'
    with gzip.open(path, 'rt', encoding='utf-8') as fp:
        events = [json.loads(line) for line in fp]
    assert public_actions(SimpleNamespace(decisions=events)) == public_actions(solver)


def test_budget_failure_keeps_360000_penalty(worlds, tmp_path):
    row = pick(worlds, 3, 'H1')
    plan = {'output': str(tmp_path), 'dataset_runtime': str(DATASET / 'runtime'),
            'source_files': harness.files(), 'plan_sha256': 'test-budget-penalty',
            'dataset_runtime_hashes': runtime_hashes()}
    actual = harness.worker((row, 'budget_case', {'implementation': 'champion',
        'config': {'max_virtual_s': 1}}, plan))
    assert not actual['audit_complete']
    assert actual['loss_s'] == 360000
    assert actual['total_virtual_time_s'] < actual['loss_s']


def test_summary_penalty_cannot_reward_short_failure():
    base = dict(case_id='one', problem=3, hypothesis='H1', source_total=10,
                program_real_time_s=1, hull_invariant_violations=[])
    rows = [dict(base, variant='champion', audit_complete=True, loss_s=4000,
                 total_virtual_time_s=4000, walk_distance_m=18000),
            dict(base, variant='failed', audit_complete=False, loss_s=360000,
                 total_virtual_time_s=10)]
    group = next(g for g in harness.summarize(rows)['groups']
                 if g['variant'] == 'failed' and g['hypothesis'] == 'H1')
    assert group['mean_difference_s'] == 356000
    assert group['new_failures'] == 1
    assert group['p95_success_s'] is None
    assert group['mean_walk_m'] is None and group['walk_observed_n'] == 0


def test_freeze_rejects_truncated_development(tmp_path):
    stage = tmp_path / 'stage'
    campaign = tmp_path / 'campaign'
    protocol = {'case_file_hashes': {'development': harness.digest([])}}
    harness.write(campaign / 'protocol.json', protocol)
    plan = {'partition': 'development', 'cases': [], 'protocol_sha256': harness.digest(protocol),
            'specs': {'champion': {'implementation': 'champion', 'limit': 1}},
            'case_file_sha256': harness.digest([])}
    plan['plan_sha256'] = harness.digest(plan)
    harness.write(stage / 'plan.json', plan)
    harness.write(stage / 'results.json', [])
    with pytest.raises(ValueError, match='360'):
        harness.freeze(SimpleNamespace(campaign=campaign, stage=stage))


def test_confirmation_requires_frozen_selection(tmp_path):
    campaign = tmp_path / 'campaign'
    harness.write(campaign / 'confirmation_cases.json', [])
    harness.write(campaign / 'protocol.json', {
        'case_file_hashes': {'confirmation': harness.digest([])},
        'dataset_runtime_hashes': runtime_hashes(), 'dataset_runtime': str(DATASET / 'runtime')})
    specs = tmp_path / 'specs.json'
    harness.write(specs, {'champion': {'implementation': 'champion', 'limit': 1}})
    with pytest.raises(FileNotFoundError, match='frozen_selection'):
        harness.make_stage(SimpleNamespace(output=tmp_path / 'stage', campaign=campaign,
            partition='confirmation', specs=specs, group_limit=0, workers=1))


def test_registered_world_change_rejected_before_stage(tmp_path):
    campaign = tmp_path / 'campaign'
    harness.write(campaign / 'development_cases.json', [{'replaced': True}])
    harness.write(campaign / 'protocol.json', {'case_file_hashes': {'development': harness.digest([])}})
    with pytest.raises(ValueError, match='registered campaign worlds changed'):
        harness.make_stage(SimpleNamespace(output=tmp_path / 'stage', campaign=campaign,
            partition='development', specs=tmp_path / 'unread_specs.json', group_limit=0, workers=1))


def test_runtime_tamper_detected(tmp_path):
    runtime = tmp_path / 'runtime'
    relative = 'bsolver_frozen/simulator.py'
    path = runtime / relative
    path.parent.mkdir(parents=True)
    original = (DATASET / 'runtime' / relative).read_bytes()
    path.write_bytes(original)
    plan = {'dataset_runtime': str(runtime), 'dataset_runtime_hashes': {
        relative: hashlib.sha256(original).hexdigest()}}
    assert harness.runtime_valid(plan)
    path.write_bytes(original + b'\n# altered test copy\n')
    assert not harness.runtime_valid(plan)


def test_stage_copies_environment_and_policy_before_execution(worlds, tmp_path, monkeypatch):
    campaign = tmp_path / 'campaign'
    rows = worlds[:1]
    protocol = {'case_file_hashes': {'development': harness.digest(rows)},
                'dataset_runtime_hashes': runtime_hashes(), 'dataset_runtime': str(DATASET / 'runtime')}
    harness.write(campaign / 'development_cases.json', rows)
    harness.write(campaign / 'protocol.json', protocol)
    specs = tmp_path / 'specs.json'
    harness.write(specs, {'champion': {'implementation': 'champion', 'limit': 1}})
    calls = []
    monkeypatch.setattr(harness.subprocess, 'run', lambda args: (
        calls.append(args) or SimpleNamespace(returncode=0)))
    stage = tmp_path / 'stage'
    harness.make_stage(SimpleNamespace(output=stage, campaign=campaign,
        partition='development', specs=specs, group_limit=0, workers=1))
    plan = harness.read(stage / 'plan.json')
    assert Path(plan['dataset_runtime']).resolve() == (stage / 'runtime').resolve()
    assert harness.runtime_valid(plan)
    assert Path(calls[0][1]).resolve() == (stage / 'source/run.py').resolve()
    assert plan['protocol_sha256'] == harness.digest(protocol)
    assert plan['plan_sha256'] == harness.digest({k: v for k, v in plan.items() if k != 'plan_sha256'})
    for relative, expected in plan['source_files'].items():
        assert hashlib.sha256((stage / 'source' / relative).read_bytes()).hexdigest() == expected


def test_confirmation_rejects_replaced_protocol_and_matching_world_hash(tmp_path):
    """Changing both the worlds and their checksum cannot reset a frozen holdout."""
    campaign = tmp_path / 'campaign'
    original_worlds = [{'case_id': 'frozen-world'}]
    protocol = {'case_file_hashes': {'confirmation': harness.digest(original_worlds)},
                'dataset_runtime_hashes': runtime_hashes(), 'dataset_runtime': str(DATASET / 'runtime')}
    specs = {'champion': {'implementation': 'champion', 'limit': 1}}
    selection = {'validation_specs': specs, 'source_files': harness.files(),
                 'protocol_sha256': harness.digest(protocol)}
    selection['selection_sha256'] = harness.digest(selection)
    harness.write(campaign / 'frozen_selection.json', selection)
    replacement = [{'case_id': 'replacement-world'}]
    protocol['case_file_hashes']['confirmation'] = harness.digest(replacement)
    harness.write(campaign / 'protocol.json', protocol)
    harness.write(campaign / 'confirmation_cases.json', replacement)
    spec_path = tmp_path / 'specs.json'
    harness.write(spec_path, specs)
    with pytest.raises(ValueError, match='campaign protocol changed after selection'):
        harness.make_stage(SimpleNamespace(output=tmp_path / 'stage', campaign=campaign,
            partition='confirmation', specs=spec_path, group_limit=0, workers=1))
    assert not (tmp_path / 'stage').exists()


@pytest.mark.parametrize('same_winner', [False, True])
def test_freeze_restricts_validation_candidates_to_selected_problems(tmp_path, monkeypatch, same_winner):
    """A both-problem development arm must not become an unselected holdout arm."""
    campaign, stage = tmp_path / 'campaign', tmp_path / 'stage'
    cases = [{'case': {'case_id': f'fixture-{p}-{h}-{i}', 'problem': p}, 'hypothesis': h}
             for p in (3, 4) for h in ('H1', 'H2') for i in range(90)]
    specs = {'champion': {'implementation': 'champion', 'limit': 1},
             'both_scope': {'problems': [3, 4], 'limit': 0},
             'p4_winner': {'problems': [4], 'limit': 2}}
    protocol = {'case_file_hashes': {'development': harness.digest(cases)},
                'selection': 'synthetic unit-test choice fixture'}
    harness.write(campaign / 'protocol.json', protocol)
    plan = {'partition': 'development', 'cases': cases, 'specs': specs,
            'source_files': harness.files(), 'protocol_sha256': harness.digest(protocol),
            'case_file_sha256': harness.digest(cases)}
    plan['plan_sha256'] = harness.digest(plan)
    harness.write(stage / 'plan.json', plan)
    results = [{'case_id': row['case']['case_id'], 'variant': name, 'spec': spec,
                'plan_sha256': plan['plan_sha256'], 'source_valid': True}
               for row in cases for name, spec in specs.items()
               if row['case']['problem'] in spec.get('problems', [3, 4])]
    harness.write(stage / 'results.json', results)
    # Isolate the dispatch boundary from time statistics; these are invented
    # unit-test fixture losses, not any development or confirmation outcomes.
    groups = []
    for problem in (3, 4):
        for name, loss in [('champion', 100), ('both_scope', 80 if problem == 3 else (60 if same_winner else 110)),
                           ('p4_winner', 70)]:
            if problem not in specs[name].get('problems', [3, 4]):
                continue
            for h in ('H1', 'H2', 'design_mixture'):
                groups.append({'problem': problem, 'variant': name, 'hypothesis': h,
                    'n': 90, 'complete': 90, 'mean_difference_s': loss - 100, 'mean_loss_s': loss})
    monkeypatch.setattr(harness, 'summarize', lambda rows: {'groups': groups})
    harness.freeze(SimpleNamespace(campaign=campaign, stage=stage))
    selection = harness.read(campaign / 'frozen_selection.json')
    assert selection['choices'] == {'3': 'both_scope', '4': 'both_scope' if same_winner else 'p4_winner'}
    assert selection['validation_specs']['both_scope']['problems'] == ([3, 4] if same_winner else [3])
    if same_winner:
        assert 'p4_winner' not in selection['validation_specs']
    else:
        assert selection['validation_specs']['p4_winner']['problems'] == [4]
    assert selection['protocol_sha256'] == harness.digest(protocol)


@pytest.mark.parametrize('entrypoint', ['stage', 'freeze'])
@pytest.mark.parametrize('bad_champion', [
    {'implementation': 'champion', 'limit': 1, 'problems': [3]},
    {'implementation': 'legacy', 'limit': 5},
    {'implementation': 'champion', 'limit': 2},
    {'implementation': 'champion', 'limit': 1, 'config': {'joint_detour_m': 1600}},
])
def test_stage_and_freeze_reject_incomplete_or_changed_strong_control(tmp_path, entrypoint, bad_champion):
    campaign, stage = tmp_path / 'campaign', tmp_path / 'stage'
    specs = {'champion': bad_champion}
    if entrypoint == 'stage':
        harness.write(campaign / 'development_cases.json', [])
        harness.write(campaign / 'protocol.json', {
            'case_file_hashes': {'development': harness.digest([])},
            'dataset_runtime_hashes': runtime_hashes(), 'dataset_runtime': str(DATASET / 'runtime')})
        path = tmp_path / 'specs.json'
        harness.write(path, specs)
        invoke = lambda: harness.make_stage(SimpleNamespace(output=stage, campaign=campaign,
            partition='development', specs=path, group_limit=0, workers=1))
    else:
        harness.write(stage / 'plan.json', {'specs': specs})
        harness.write(stage / 'results.json', [])
        invoke = lambda: harness.freeze(SimpleNamespace(campaign=campaign, stage=stage))
    with pytest.raises(ValueError, match='exact L1 strong champion control required for both problems'):
        invoke()
    assert not (campaign / 'frozen_selection.json').exists()


@pytest.mark.parametrize('problems', [None, [4, 3]])
def test_strong_control_accepts_default_and_explicit_both_problem_scope(problems):
    spec = {'implementation': 'champion', 'limit': 1}
    if problems is not None:
        spec['problems'] = problems
    harness.validate_specs({'champion': spec})
