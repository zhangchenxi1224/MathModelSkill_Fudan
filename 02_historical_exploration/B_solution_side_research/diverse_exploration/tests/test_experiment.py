import argparse
import copy
import json
from pathlib import Path

import pytest

from diverse.experiment import (digest, expected_assignments, fitted_distribution,
                                make_case, prepare, read, summarize, verify_manifest, world_digest, evaluate_job)
from bsolver.simulator import FixedErrorField, Source


def test_all_legal_compositions_and_fixed_error_pairing():
    combinations = set()
    for index in range(98):
        case = make_case(3200000 + index, 4, 'broad', index)
        combinations.add((len(case['sources']), case['n_directed']))
        assert sum(s['direction_deg'] is not None for s in case['sources']) == case['n_directed']
        for source in case['sources']:
            Source(source['channel'], source['position'], source['radius'], source['direction_deg'])
    assert combinations == {(n, nd) for n in range(10, 17) for nd in range(n + 1)}
    first = FixedErrorField(40, 'correlated')
    second = FixedErrorField(40, 'correlated')
    first(7, (300., 500.))
    assert first(3, (2., 6.)) == second(3, (2., 6.))


def test_plan_is_disjoint_hashed_and_confirmation_runs_selected_only(tmp_path):
    args = argparse.Namespace(output=str(tmp_path / 'plan'), per_cell=2,
                              confirm_per_cell=1, calibration=None,
                              routes='robust,probe,fisher', profile='research',
                              seed=9234523456, options=None)
    prepare(args)
    manifest = read(tmp_path / 'plan' / 'manifest.json')
    verify_manifest(manifest)
    cases = manifest['cases']
    assert len({c['seed'] for c in cases}) == len(cases) == 12
    selected = {'choices': {'3': 'probe', '4': 'robust'}}
    assignments = list(expected_assignments(manifest, 'confirmation', selected))
    assert len(assignments) == 6
    assert all(route in ('robust', 'probe') for _, route in assignments)
    changed = copy.deepcopy(manifest)
    changed['cases'][0]['sources'][0]['radius'] = 1234
    with pytest.raises(ValueError, match='manifest modified'):
        verify_manifest(changed)
    with pytest.raises(ValueError, match='new empty'):
        prepare(args)
    args.output = str(tmp_path / 'second')
    args.profile = 'smoke'
    args.exclude_manifest = [str(tmp_path / 'plan' / 'manifest.json')]
    with pytest.raises(ValueError, match='overlap'):
        prepare(args)


def test_empirical_or_missing_probability_not_accepted_as_calibration():
    with pytest.raises(ValueError):
        fitted_distribution({'model_status': 'exploratory'}, 4)
    model = {'model_status': 'fitted_composition_only', 'problems': {'4': {
        'composition_candidates': {'smoothed_joint': {'count_distribution': [
            {'n': 10, 'n_directed': 11, 'probability': 1.}]}}}}}
    with pytest.raises(ValueError, match='illegal'):
        fitted_distribution(model, 4)


def test_failed_candidate_never_wins_by_short_elapsed_time():
    template = {'case_id': 'same', 'problem': 4, 'pool': 'stress',
                'radius_mode': 'min', 'error_mode': 'extreme', 'layout': 'boundary', 'correlation_length': 150.,
                'audit_complete': True, 'total_virtual_time_s': 1000.,
                'failure_penalized_s': 1000., 'program_real_time_s': .2,
                'hull_invariant_violations': [], 'walk_distance_m': 4000., 'measures': 100}
    baseline = dict(template, route='robust')
    failed = dict(template, route='probe', audit_complete=False, total_virtual_time_s=1.,
                  failure_penalized_s=360000.)
    summary = summarize([baseline, failed])
    comparison = summary['paired'][0]
    assert comparison['mean_penalty_delta_s'] == 359000.
    assert comparison['new_failures'] == 1
    assert comparison['dual_success_n'] == 0
    assert comparison['pointwise_95_ci_s'] is None


def test_world_identity_ignores_labels_and_stress_crosses_radius():
    case = make_case(50, 4, 'stress', 0)
    relabelled = dict(case, partition='different', case_id='different')
    assert world_digest(case) == world_digest(relabelled)
    for index in range(6):
        assert {make_case(50+i, 4, 'stress', i)['radius_mode']
                for i in (index, index+6, index+12)} == {'min', 'mixed', 'max'}


def test_harness_failure_is_persisted_with_unknown_cost_not_fabricated_zero(tmp_path):
    from diverse.experiment import source_files
    from bsolver.experiments import variant_configs
    from dataclasses import asdict
    case = make_case(300, 3, 'broad', 0)
    case.update(case_id='broken', partition='engineering', scenario_sha256='fixture')
    result = evaluate_job((case, 'invalid_route', asdict(variant_configs(3)['joint_triangular']),
                           {}, str(tmp_path), digest(source_files()), 'test'))
    assert result['status'] == 'harness_failed'
    assert result['failure_penalized_s'] == 360000
    assert result['total_virtual_time_s'] is None
    assert not result['audit_complete']
    assert read(tmp_path / 'broken' / 'invalid_route' / 'result.json')['error']


def test_freeze_and_confirmation_tampering_gate(tmp_path):
    from diverse.experiment import freeze, run, write
    args = argparse.Namespace(output=str(tmp_path/'plan'), per_cell=1, confirm_per_cell=1,
                              calibration=None, routes='robust,probe', profile='research',
                              seed=100000000, options=None)
    prepare(args)
    manifest_path = tmp_path/'plan/manifest.json'
    manifest = read(manifest_path)
    records = []
    # Deliberately synthetic selection fixtures, never performance evidence.
    for case, route in expected_assignments(manifest, 'development'):
        records.append(dict(case_id=case['case_id'], route=route, problem=case['problem'], pool=case['pool'],
                            layout=case['layout'], radius_mode=case['radius_mode'], error_mode=case['error_mode'],
                            correlation_length=case['correlation_length'], source_valid=True,
                            source_sha256=digest(manifest['source_files']), scenario_sha256=case['scenario_sha256'],
                            manifest_sha256=manifest['manifest_sha256'], config=manifest['configs'][str(case['problem'])],
                            options=manifest['options'], audit_complete=True, total_virtual_time_s=100 if route=='robust' else 90,
                            failure_penalized_s=100 if route=='robust' else 90, program_real_time_s=.1,
                            walk_distance_m=100., measures=5, hull_invariant_violations=[]))
    write(tmp_path/'plan/development/results.json', records)
    freeze(argparse.Namespace(manifest=str(manifest_path)))
    selection_path = tmp_path/'plan/selection.json'
    selection = read(selection_path)
    assert selection['choices'] == {'3':'probe', '4':'probe'}
    with pytest.raises(ValueError, match='already frozen'):
        freeze(argparse.Namespace(manifest=str(manifest_path)))
    selection['choices']['4'] = 'robust'
    write(selection_path, selection)
    with pytest.raises(ValueError, match='selection modified'):
        run(argparse.Namespace(manifest=str(manifest_path), selection=str(selection_path),
                               partition='confirmation', workers=1, resume=False))
    assert not (tmp_path/'plan/confirmation').exists()
