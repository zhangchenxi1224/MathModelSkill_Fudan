"""Synthetic saved-record tests only; no official dispatch or policy execution."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import statistics

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('p4_report_under_test', ROOT/'scripts/report_p4_refined_official.py')
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def fixture(tmp_path, *, failed_arm=None):
    folder = tmp_path/'round'
    cases = []
    allocation = [('pilot', 0, arm) for arm in report.ARMS]
    allocation += [('validation', block, arm) for block in range(1, 16)
                   for arm in ('current', 'combined_cover', 'current', 'combined_cover')]
    for sequence, (stage, block, arm) in enumerate(allocation):
        case = {'case_id': f'case-{sequence:03d}', 'problem': 4, 'environment': 'official_practice',
                'arm': arm, 'protocol': arm, 'sequence': sequence, 'stage': stage, 'split': stage,
                'pilot': stage == 'pilot', 'block': block}
        cases.append(case)
        d = folder/'cases'/case['case_id']
        complete = not (stage == 'validation' and arm == failed_arm)
        total = (350000. if arm == 'current' else 60.) if stage == 'pilot' else (1000. if arm == 'current' else 800.)+sequence
        successes = 10 if complete else 0
        status = 'complete' if complete else 'incomplete'
        result = {**case, 'status': status, 'total_virtual_time_s': total, 'walk_distance_m': (total-5*successes)*5,
                  'measures': 0, 'switches': 0, 'clear_attempts': successes, 'clear_successes': successes,
                  'program_real_time_s': .02, 'error': None if complete else 'budget_exhausted',
                  'stop_evidence': {'type': 'per_channel_coverage'} if complete else None}
        audit = {'case_id': case['case_id'], 'problem': 4, 'status': status, 'case_code': f'CODE-{sequence}',
                 'source_total_post_exit': 10, 'directional_total_post_exit': 4,
                 'cleared': successes, 'total_virtual_time_s': total,
                 'original_log_filename': f'{sequence}.jlog', 'original_log_sha256': 'fixture-only'}
        events = [{'path': '/enter', 'request': {'request_id': f'{sequence}-enter'},
                   'response': {'accepted': True, 'virtual_time_s': 0.}}]
        for channel in range(1, successes+1):
            events.append({'path': '/clear', 'request': {'request_id': f'{sequence}-{channel}', 'channel': channel,
                                                       'position': {'x': 0., 'y': 0.}},
                           'response': {'accepted': True, 'clear_result': 'success',
                                        'virtual_time_s': total if channel == successes else 5.*channel}})
        events.append({'path': '/exit', 'request': {'request_id': f'{sequence}-exit'},
                       'response': {'accepted': True, 'virtual_time_s': total}})
        write(d/'assignment.json', case)
        write(d/'result.json', result)
        write(d/'post_exit_audit.json', audit)
        (d/'requests.jsonl').write_text('\n'.join(json.dumps(e) for e in events)+'\n', encoding='utf-8')
    write(folder/'plan.json', {'cases': cases, 'formal_authorized': False, 'practice_authorized_by_round_plan': True})
    write(folder/'integrity/overall.json', {'n_issues': 0, 'complete_prefix_only': False,
          'n_audited_cases': len(cases), 'n_planned_unstarted': 0, 'n_skipped_in_progress_or_incomplete': 0})
    return folder


def edit(path, function):
    obj = report.read(path)
    function(obj)
    write(path, obj)


def test_independent_bootstrap_is_not_a_paired_difference_interval():
    a, b = [100., 200., 300., 400.], [90., 190., 290., 390.]
    result = report.independent_bootstrap(a, b)
    assert result == report.independent_bootstrap(a, b)
    assert result['difference_s'] == -10.
    assert result['difference_ci95_s'][0] < -10 < result['difference_ci95_s'][1]
    assert result['reduction_percent'] == pytest.approx(4.)
    assert result['draws'] == 10000 and result['seed'] == 2026091137


def test_zero_failure_wilson_does_not_claim_zero_risk():
    interval = report.wilson(0, 30)
    assert interval == pytest.approx([0., .11351339317396876])
    difference = report.independent_failure_difference(0, 30, 0, 30)
    assert difference['ci95'] == pytest.approx([-.11351339317396876, .11351339317396876])


@pytest.mark.parametrize('values', [([], [1.]), ([0.], [1.]), ([float('nan')], [1.]), ([1.], [float('inf')])])
def test_invalid_bootstrap_inputs_rejected(values):
    with pytest.raises(ValueError):
        report.independent_bootstrap(*values)


def test_report_excludes_extreme_pilots_and_keeps_all_62_rows(tmp_path):
    folder = fixture(tmp_path)
    result = report.build(folder)
    validation = [r for r in result['cases'] if r['stage'] == 'validation']
    means = {a: statistics.fmean(r['penalized_time_s'] for r in validation if r['arm'] == a) for a in report.ARMS}
    assert len(result['cases']) == 62 and result['validation_cases'] == 60 and result['pilot_cases'] == 2
    assert result['comparison_penalized']['difference_s'] == means['combined_cover']-means['current']
    assert result['all_complete'] and result['validation_all_complete']
    assert result['validation_failure_rate_difference']['ci95'][1] > .11
    assert '10000' in (folder/'report/official_report.md').read_text(encoding='utf-8')


def test_whole_arm_failure_is_penalized_and_report_remains_renderable(tmp_path):
    folder = fixture(tmp_path, failed_arm='combined_cover')
    result = report.build(folder)
    failures = result['failed_or_unverified_cases']
    assert len(failures) == 30 and all(r['stage'] == 'validation' for r in failures)
    assert all(r['penalized_time_s'] == 360000. and r['completion_time_s'] is None for r in failures)
    current = [r['penalized_time_s'] for r in result['cases'] if r['stage'] == 'validation' and r['arm'] == 'current']
    assert result['comparison_penalized']['difference_s'] == 360000.-statistics.fmean(current)
    assert '无完整案例' in (folder/'report/official_report.md').read_text(encoding='utf-8')


def test_public_clear_mismatch_is_retained_as_unverified_failure(tmp_path):
    folder = fixture(tmp_path)
    edit(folder/'cases/case-003/post_exit_audit.json', lambda a: a.update(cleared=9))
    result = report.build(folder)
    row = next(r for r in result['cases'] if r['case_id'] == 'case-003')
    assert not row['complete'] and row['penalized_time_s'] == 360000.
    assert 'public_total_clear_count_mismatch' in row['failed_or_unverified_reasons']


def test_complete_prefix_or_skipped_audit_cannot_produce_fixed_sample_report(tmp_path):
    folder = fixture(tmp_path)
    edit(folder/'integrity/overall.json', lambda a: a.update(complete_prefix_only=True, n_audited_cases=61))
    with pytest.raises(ValueError, match='Full audit'):
        report.build(folder)
    assert not (folder/'report/results.json').exists()


def test_duplicate_case_and_changed_assignment_rejected(tmp_path):
    folder = fixture(tmp_path)
    edit(folder/'plan.json', lambda p: p['cases'].__setitem__(3, p['cases'][2]))
    with pytest.raises(ValueError, match='Duplicate'):
        report.build(folder)


def test_same_id_retry_counts_once_and_rejected_attempt_does_not_occupy_id():
    body = {'request_id': 'x', 'channel': 2, 'position': {'x': 1., 'y': 2.}}
    accepted = {'path': '/clear', 'request': body, 'attempt': 2,
                'response': {'accepted': True, 'clear_result': 'no_target_in_range', 'virtual_time_s': 3.}}
    rows = [{'path': '/clear', 'request': body, 'response': {'accepted': False}}, accepted, accepted]
    result = report.accepted_feedback(rows)
    assert result['clear_attempts'] == 1 and len(result['failures']) == 1
    assert result['unique_accepted_actions'] == 1


def test_missing_id_and_conflicting_retry_rejected():
    missing = {'path': '/measure', 'request': {}, 'response': {'accepted': True, 'measure_result': 'no_signal'}}
    with pytest.raises(ValueError, match='request_id'):
        report.accepted_feedback([missing])
    first = {'path': '/measure', 'request': {'request_id': 'x', 'channel': 1},
             'response': {'accepted': True, 'measure_result': 'no_signal', 'virtual_time_s': 5.}}
    second = {**first, 'request': {'request_id': 'x', 'channel': 2}}
    with pytest.raises(ValueError, match='conflicting'):
        report.accepted_feedback([first, second])
