"""Fabricated artifact/log fixtures only; never opens real confirmation data."""
from pathlib import Path
import copy
import gzip
import json
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import audit_final as audit


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def compressed(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, 'wt', encoding='utf-8') as fp:
        for row in rows:
            fp.write(json.dumps(row) + '\n')


def complete_log_fixture(directory):
    """Sixteen coincident channels; clear calls never retune the receiver."""
    case = {'case_id': 'fixture-sixteen', 'problem': 3, 'seed': 117,
            'error_field': {'seed': 9182, 'mode': 'zero', 'correlation_length_m': 150},
            'sources': [{'channel': i, 'position': [10., 0.], 'radius': 1000., 'direction_deg': None}
                        for i in range(1, 17)]}
    case['scenario_sha256'] = audit.digest(case)
    state = {'position': {'x': 0., 'y': 0.}, 'current_channel': 1, 'virtual_time_s': 0.,
             'cleared_count': 0, 'cleared_channels': [], 'entered': False, 'exited': False}
    requests, decisions = [], []
    request_index = 0
    def append(path, body, response, newstate):
        nonlocal state, request_index
        request_index += 1
        payload = {'request_id': f'fixture-{request_index}', **body}
        requests.append({'event': 'request', 'path': path, 'request': payload,
            'response': {'accepted': True, 'real_timestamp_ms': request_index, **response},
            'state_before': copy.deepcopy(state), 'state_after': copy.deepcopy(newstate)})
        state = newstate
        if path in ('/measure', '/clear'):
            decisions.append({'sequence': len(decisions), 'event': path[1:],
                'position': [state['position']['x'], state['position']['y']], 'channel': state['current_channel'],
                'virtual_time_s': state['virtual_time_s'], 'response': requests[-1]['response'],
                'knowledge': {'channel': body['channel'], 'hull': [[10., 0.]]}})
    append('/enter', {}, {'virtual_time_s': 0}, {**state, 'entered': True})
    append('/measure', {'channel': 20, 'position': {'x': 5., 'y': 0.}},
        {'virtual_time_s': 7, 'measure_result': 'no_signal'},
        {**state, 'position': {'x': 5., 'y': 0.}, 'current_channel': 20, 'virtual_time_s': 7})
    for channel in range(1, 17):
        seconds = 13 + 5 * (channel - 1)
        append('/clear', {'channel': channel, 'position': {'x': 10., 'y': 0.}},
            {'virtual_time_s': seconds, 'clear_result': 'success'},
            {**state, 'position': {'x': 10., 'y': 0.}, 'virtual_time_s': seconds,
             'cleared_count': channel, 'cleared_channels': list(range(1, channel + 1))})
    append('/exit', {}, {'virtual_time_s': 88}, {**state, 'exited': True})
    compressed(directory / 'requests.jsonl.gz', requests)
    compressed(directory / 'decisions.jsonl.gz', decisions)
    result = {'status': 'complete', 'audit_complete': True, 'loss_s': 88., 'total_virtual_time_s': 88.,
              'walk_distance_m': 10., 'measures': 1, 'switches': 1, 'clear_attempts': 16, 'clear_successes': 16,
              'source_total': 16, 'clear_fraction': 1, 'environment_summary': {'all_cleared': True, 'cleared_count': 16},
              'stop_evidence': {'type': 'known_upper_bound', 'cleared_channels': list(range(1, 17))}}
    return case, result, requests, decisions


def test_independent_log_cost_clear_does_not_change_receiver(tmp_path):
    case, result, _, _ = complete_log_fixture(tmp_path)
    checked = audit.audit_case(tmp_path, {'case': case}, result)
    assert checked['issues'] == []
    assert checked['verified_complete']
    assert checked['request_count'] == 19 and checked['hull_checks'] == 16
    assert checked['costs']['total_virtual_time_s'] == 88
    assert checked['costs']['switches'] == 1
    assert checked['costs']['movement_cost_s'] == 2
    assert checked['costs']['optical_attempt_cost_s'] == 48
    assert checked['costs']['laser_cost_s'] == 32


@pytest.mark.parametrize('corruption', ['time', 'truth', 'hull', 'count'])
def test_corrupted_log_or_result_is_rejected(tmp_path, corruption):
    case, result, requests, decisions = complete_log_fixture(tmp_path)
    if corruption == 'time':
        requests[-1]['response']['virtual_time_s'] = 1
    elif corruption == 'truth':
        case['sources'][0]['position'] = [1000., 0.]
    elif corruption == 'hull':
        decisions[-1]['knowledge']['hull'] = [[1000., 0.]]
    else:
        result['clear_successes'] = 15
    compressed(tmp_path / 'requests.jsonl.gz', requests)
    compressed(tmp_path / 'decisions.jsonl.gz', decisions)
    assert audit.audit_case(tmp_path, {'case': case}, result)['issues']


def test_idempotent_replay_not_double_counted(tmp_path):
    case, _, requests, _ = complete_log_fixture(tmp_path)
    requests.insert(4, copy.deepcopy(requests[3]))
    compressed(tmp_path / 'requests.jsonl.gz', requests)
    result = audit.audit_requests(tmp_path / 'requests.jsonl.gz', case)
    assert result['issues'] == []
    assert result['stats']['total_virtual_time_s'] == 88
    assert result['stats']['clear_successes'] == 16


def test_case_error_seed_controls_prediction_and_wrong_seed_disagrees():
    case = {'error_field': {'seed': 173, 'mode': 'correlated', 'correlation_length_m': 150}}
    sources = {1: {'position': [1000., 10.], 'radius': 1500., 'direction_deg': None}}
    first = audit.expected_measurement(case, sources, set(), 1, (0., 0.))
    assert first['measure_result'] == 'direction'
    assert first == audit.expected_measurement(case, sources, set(), 1, (0., 0.))
    case['error_field']['seed'] = 999999
    assert first != audit.expected_measurement(case, sources, set(), 1, (0., 0.))


@pytest.mark.parametrize('mode', ['zero', 'positive', 'negative', 'deterministic', 'correlated', 'extreme'])
def test_independent_field_transcription_matches_frozen_runtime_on_constructed_points(mode):
    # Only self-invented coordinates and seeds. No stage or dataset cases opened.
    sys.path.insert(0, str(ROOT / 'vendor'))
    from bsolver.simulator import FixedErrorField
    definition = {'seed': 98182734132, 'mode': mode, 'correlation_length_m': 150.}
    reference = FixedErrorField(definition['seed'], mode, 150.)
    for channel in (1, 7, 20):
        for point in [(0., -0.), (-1000., 9.), (75., 1000.01), (150., -150.)]:
            assert audit.fixed_error(definition, channel, point) == reference(channel, point)


def test_bootstrap_constant_delta_and_missing_single_pair_ci():
    result = audit.bootstrap([-10.] * 20, seed=183, draws=10000)
    assert result['ci975_s'] == [-10., -10.]
    assert result['ci95_s'] == [-10., -10.]
    assert audit.bootstrap([-10], seed=183)['ci975_s'] is None
    assert audit.bootstrap([3., -4., 1.], seed=183, draws=50) == audit.bootstrap([3., -4., 1.], seed=183, draws=50)


def test_preflight_does_not_read_partial_results(tmp_path, monkeypatch):
    def forbidden(path):
        raise AssertionError('partial results were opened')
    monkeypatch.setattr(audit, 'read', forbidden)
    with pytest.raises(RuntimeError, match='no results opened'):
        audit.audit_final(tmp_path / 'campaign/frozen_selection.json', {
            'development': tmp_path / 'full', 'confirmation': tmp_path / 'confirmation', 'stress': tmp_path / 'stress'})


def artifact_fixture(root):
    """Synthetic metadata fixture; no actual stage results are ever loaded."""
    campaign = root / 'campaign'
    stages = {p: root / p for p in ('development', 'confirmation', 'stress')}
    source = b'# fictional unit-test source\n'
    runtime = b'# fictional unit-test environment\n'
    source_hashes = {'run.py': audit.hashlib.sha256(source).hexdigest()}
    runtime_hashes = {'bsolver_frozen/simulator.py': audit.hashlib.sha256(runtime).hexdigest()}
    full_specs = {'champion': {'implementation': 'champion', 'limit': 1}, 'addon': {'limit': 0}}
    valid_specs = {'champion': full_specs['champion'], 'addon': {'limit': 0, 'problems': [3]}}
    all_cases = {}
    for partition, group_size in [('development', 90), ('confirmation', 1), ('stress', 12)]:
        rows = []
        for problem in (3, 4):
            for h in ('H1', 'H2'):
                for index in range(group_size):
                    case_id = f'fixture-{partition}-{problem}-{h}-{index}'
                    case = {'case_id': case_id, 'seed': index, 'problem': problem,
                        'sources': [{'channel': c, 'position': [c * 10., 0.], 'radius': 1000., 'direction_deg': None}
                                    for c in range(1, 11)],
                        'error_field': {'seed': int(audit.digest(case_id)[:16], 16), 'mode': 'correlated', 'correlation_length_m': 150}}
                    case['scenario_sha256'] = audit.digest(case)
                    rows.append({'case': case, 'hypothesis': h if partition != 'stress' else 'stress',
                                 'role': 'fixture_' + partition})
        all_cases[partition] = rows
        write(campaign / f'{partition}_cases.json', rows)
    protocol = {'case_file_hashes': {p: audit.digest(rows) for p, rows in all_cases.items()},
                'dataset_runtime_hashes': runtime_hashes, 'fresh_per_group': 1, 'failure_loss_seconds': 360000}
    write(campaign / 'protocol.json', protocol)
    selection = {'choices': {'3': 'addon', '4': 'champion'}, 'validation_specs': valid_specs,
                 'source_files': source_hashes, 'protocol_sha256': audit.digest(protocol),
                 'development_stage': str(stages['development'].resolve())}
    def stage(partition):
        directory, cases = stages[partition], all_cases[partition]
        specs = full_specs if partition == 'development' else valid_specs
        (directory / 'source').mkdir(parents=True)
        (directory / 'source/run.py').write_bytes(source)
        (directory / 'runtime/bsolver_frozen').mkdir(parents=True)
        (directory / 'runtime/bsolver_frozen/simulator.py').write_bytes(runtime)
        plan = {'partition': partition, 'output': str(directory.resolve()), 'cases': cases, 'specs': specs,
                'source_files': source_hashes, 'protocol_sha256': audit.digest(protocol),
                'case_file_sha256': protocol['case_file_hashes'][partition], 'dataset_runtime_hashes': runtime_hashes,
                'dataset_runtime': str((directory / 'runtime').resolve()),
                'selection_sha256': None if partition == 'development' else selection['selection_sha256']}
        plan['plan_sha256'] = audit.digest(plan)
        write(directory / 'plan.json', plan)
        results = []
        for row in cases:
            c = row['case']; problem = c['problem']
            for name, spec in specs.items():
                if problem not in spec.get('problems', [3, 4]):
                    continue
                seconds = 100. if name == 'champion' else (90. if problem == 3 else 110.)
                result = {'case_id': c['case_id'], 'variant': name, 'spec': spec, 'problem': problem,
                    'hypothesis': row['hypothesis'], 'role': row['role'], 'scenario_sha256': c['scenario_sha256'],
                    'world_sha256': audit.worldhash(c), 'source_valid': True, 'source_total': 10,
                    'plan_sha256': plan['plan_sha256'], 'audit_complete': True, 'loss_s': seconds,
                    'total_virtual_time_s': seconds, 'program_real_time_s': .1}
                results.append(result)
                write(directory / 'cases' / c['case_id'] / name / 'result.json', result)
        write(directory / 'results.json', results)
        write(directory / 'summary.json', {'runs': len(results), 'expected_runs': len(results),
            'all_complete': len(results), 'plan_sha256': plan['plan_sha256']})
        return results
    full_results = stage('development')
    selection['development_results_sha256'] = audit.digest(full_results)
    selection['selection_sha256'] = audit.digest(selection)
    write(campaign / 'frozen_selection.json', selection)
    stage('confirmation'); stage('stress')
    return campaign / 'frozen_selection.json', stages


def test_final_metadata_chain_selection_and_independent_metrics(tmp_path):
    selection, stages = artifact_fixture(tmp_path)
    report = audit.audit_final(selection, stages, draws=10, audit_logs=False)
    assert report['audit_passed'], report['issues']
    assert report['independently_recomputed_choices'] == {'3': 'addon', '4': 'champion'}
    contrast = next(r for r in report['comparisons'] if r['partition'] == 'confirmation'
                    and r['problem'] == 3 and r['hypothesis'] == 'design_mixture')
    assert contrast['mean_delta_s'] == -10
    assert contrast['ratio_of_means_reduction_pct'] == pytest.approx(10)
    assert contrast['candidate']['mean_penalized_T_per_N_s'] == 9
    assert contrast['candidate']['p95_success_T_s'] == 90
    assert contrast['regressions'] == 0 and contrast['bootstrap']['ci975_s'] == [-10., -10.]
    assert '最终实验独立审计' in audit.render_markdown(report)
    # Losing one selected-arm record must fail the audit without crashing or
    # silently reducing the paired sample size.
    path = stages['confirmation'] / 'results.json'
    rows = audit.read(path)
    rows.remove(next(r for r in rows if r['variant'] == 'addon'))
    write(path, rows)
    broken = audit.audit_final(selection, stages, draws=10, audit_logs=False)
    assert not broken['audit_passed']
    assert any('missing/extra/duplicate' in issue for issue in broken['issues'])


def test_source_tampering_marks_final_audit_failed(tmp_path):
    selection, stages = artifact_fixture(tmp_path)
    (stages['confirmation'] / 'source/run.py').write_text('# changed', encoding='utf-8')
    report = audit.audit_final(selection, stages, draws=10, audit_logs=False)
    assert not report['audit_passed']
    assert any('source bytes mismatch' in item for item in report['issues'])


def test_short_failed_run_never_looks_faster():
    base = {'case_id': 'fixture', 'problem': 3, 'hypothesis': 'H1', 'source_total': 10,
            'program_real_time_s': .1, '_audit': {'costs': None}}
    rows = [{**base, 'variant': 'champion', 'audit_complete': True, 'loss_s': 100, 'total_virtual_time_s': 100},
            {**base, 'variant': 'candidate', 'audit_complete': False, 'loss_s': 360000, 'total_virtual_time_s': 1}]
    result = audit.comparison(rows, 'candidate', partition='fixture', problem=3, hypothesis='H1', draws=10)
    assert result['mean_delta_s'] == 359900
    assert result['new_failures'] == 1 and result['candidate']['failures'] == 1
    assert result['candidate']['p95_success_T_s'] is None
