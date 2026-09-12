"""Independent audit against saved public official and local fixture journals.

No new official or simulator actions are executed. Mutations are in-memory
counterexamples; original experiment files are never edited.
"""
import copy
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('p4_official_independent_audit', ROOT/'scripts/audit_p4_refined_official.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def historical():
    folder = ROOT/'results/round2/cases/r2-p4-validation-052'
    if not folder.is_dir():
        raise unittest.SkipTest('saved historical official fixture is absent')
    return folder, audit.load(folder/'assignment.json'), audit.load(folder/'result.json'), \
        audit.json_rows(folder/'requests.jsonl'), audit.json_rows(folder/'decisions.jsonl')


def local_fixture(index='0000-eb020c3d71c4b04b'):
    folder = ROOT/'results/p4_refinement/development/runs/calibrated'/(
        'p4-refinement-development-round-calibrated-p4-'+index)/'combined_cover'
    if not folder.is_dir():
        raise unittest.SkipTest('saved local refinement fixture is absent')
    def lines(name):
        with gzip.open(folder/(name+'.jsonl.gz'), 'rt', encoding='utf-8') as stream:
            return [json.loads(line) for line in stream if line.strip()]
    result = audit.load(folder/'result.json')
    requests, decisions = lines('requests'), lines('decisions')
    accepted = audit.replay_requests(requests)['accepted_actions']
    expected = audit.load(ROOT/'results/p4_refinement/source_freeze.json')['refinement']
    return result, requests, decisions, accepted, expected


class RefinedOfficialAuditTests(unittest.TestCase):
    def test_existing_official_p4_candidate_whole_case_passes_without_relabeling(self):
        folder, assignment, result, requests, decisions = historical()
        expected = next(e['coverage_points'] for e in decisions if e['event'] == 'start')
        checked = audit.audit_case(folder, assignment, expected, 'current')
        self.assertEqual(checked['issues'], [])
        self.assertEqual(checked['official_N'], 10)
        self.assertEqual(checked['official_Ndir'], 9)
        self.assertEqual(checked['accepted_request_count'], 519)
        self.assertEqual(checked['clear_attempts'], 147)
        self.assertAlmostEqual(checked['post_clear_stop_tail_s'], 3297.982218, places=6)

    def test_saved_combined_25_station_actual_negative_certificate_passes(self):
        result, _, decisions, accepted, expected = local_fixture()
        checked = audit.verify_stop(result, decisions, accepted, expected['refined_route'], 'combined_cover', expected['coverage_certificate'])
        self.assertEqual(checked['issues'], [])
        self.assertEqual(checked['actual_station_count'], 25)

    def test_25_station_case_is_rejected_when_a_31_station_route_is_substituted(self):
        result, _, decisions, accepted, expected = local_fixture()
        old = [list(p) for p in audit.order_route(audit.directional_points('triangular', None), (0., 0.))]
        checked = audit.verify_stop(result, decisions, accepted, old, 'combined_cover', expected['coverage_certificate'])
        codes = {i['code'] for i in checked['issues']}
        self.assertIn('invalid_frozen_station_route', codes)
        self.assertIn('start_does_not_match_frozen_actual_route', codes)

    def test_omitting_one_actual_negative_cannot_be_hidden_by_complete_indices(self):
        result, _, decisions, accepted, expected = local_fixture()
        channel = result['stop_evidence']['absent_channels'][0]
        altered = copy.deepcopy(accepted)
        index = next(i for i, a in enumerate(altered) if a['path'] == '/measure'
                     and a['request']['channel'] == channel and a['response']['measure_result'] == 'no_signal')
        altered.pop(index)
        checked = audit.verify_stop(result, decisions, altered, expected['refined_route'], 'combined_cover', expected['coverage_certificate'])
        self.assertTrue(any(i['code'] == 'actual_negative_station_missing' for i in checked['issues']))

    def test_real_sixteen_clear_stop_passes_without_full_station_negatives(self):
        result, _, decisions, accepted, expected = local_fixture('0004-d0130335b0388135')
        self.assertEqual(result['stop_evidence']['type'], 'known_upper_bound')
        checked = audit.verify_stop(result, decisions, accepted, expected['refined_route'], 'combined_cover', expected['coverage_certificate'])
        self.assertEqual(checked['issues'], [])

    def test_false_sixteen_clear_stop_is_rejected(self):
        result, _, decisions, accepted, expected = local_fixture()
        changed, logs = copy.deepcopy(result), copy.deepcopy(decisions)
        changed['stop_evidence'] = {'type': 'known_upper_bound', 'cleared_channels': result['stop_evidence']['cleared_channels']}
        next(e for e in logs if e['event'] == 'finish')['result']['stop_evidence'] = changed['stop_evidence']
        checked = audit.verify_stop(changed, logs, accepted, expected['refined_route'], 'combined_cover', expected['coverage_certificate'])
        self.assertTrue(any(i['code'] == 'sixteen_source_stop_without_sixteen_distinct_successes' for i in checked['issues']))

    def test_response_loss_same_body_retry_preserves_unique_action_counts(self):
        _, _, _, requests, _ = historical()
        index = next(i for i, r in enumerate(requests) if r['path'] == '/clear')
        failure = copy.deepcopy(requests[index])
        failure.pop('response')
        failure['outcome'] = 'transport_error'
        failure['state_after'] = copy.deepcopy(failure['state_before'])
        fixture = copy.deepcopy(requests)
        fixture.insert(index, failure)
        checked = audit.replay_requests(fixture)
        self.assertEqual(checked['issues'], [])
        self.assertEqual(checked['counts']['unique_accepted_actions'], 519)
        self.assertEqual(checked['counts']['unconfirmed_transport_rows'], 1)
        self.assertFalse(checked['unresolved_last_request'])

    def test_new_id_after_unknown_execution_is_rejected(self):
        _, _, _, requests, _ = historical()
        index = next(i for i, r in enumerate(requests) if r['path'] == '/clear')
        failure = copy.deepcopy(requests[index])
        failure.pop('response')
        failure['state_after'] = copy.deepcopy(failure['state_before'])
        failure['request']['request_id'] += '-ambiguous-fixture'
        fixture = copy.deepcopy(requests)
        fixture.insert(index, failure)
        checked = audit.replay_requests(fixture)
        self.assertTrue(any(i['code'] == 'new_id_while_prior_response_unresolved' for i in checked['issues']))

    def test_rejected_placeholder_zero_never_rewinds_position_or_time(self):
        _, _, _, requests, _ = historical()
        index = next(i for i, r in enumerate(requests) if r['path'] == '/clear')
        rejected = copy.deepcopy(requests[index])
        rejected['request']['request_id'] += '-rejected-fixture'
        rejected['response'] = {'accepted': False, 'virtual_time_s': 0, 'real_timestamp_ms': 1}
        rejected['state_after'] = copy.deepcopy(rejected['state_before'])
        fixture = copy.deepcopy(requests)
        fixture.insert(index, rejected)
        checked = audit.replay_requests(fixture)
        self.assertEqual(checked['issues'], [])
        self.assertEqual(checked['counts']['rejected_response_rows'], 1)

    def test_failed_clear_cannot_change_receiver_channel(self):
        _, _, _, requests, _ = historical()
        fixture = copy.deepcopy(requests)
        record = next(r for r in fixture if r['path'] == '/clear' and r['response']['clear_result'] == 'no_target_in_range')
        record['state_after']['current_channel'] = record['state_before']['current_channel'] % 20+1
        checked = audit.replay_requests(fixture)
        self.assertTrue(any(i['code'] == 'state_mismatch' and i['detail'] == 'state_after.current_channel' for i in checked['issues']))

    def test_large_timing_error_is_detected(self):
        _, _, _, requests, _ = historical()
        fixture = copy.deepcopy(requests)
        fixture[-1]['response']['virtual_time_s'] += .01
        checked = audit.replay_requests(fixture)
        self.assertTrue(any(i['code'] == 'response_time_mismatch' for i in checked['issues']))

    def test_jlog_hash_and_public_case_identity_are_checked(self):
        folder, _, _, _, _ = historical()
        document = audit.load(folder/'post_exit_audit.json')
        session = audit.load(folder/'session.json')
        ui = audit.load(folder/'post_exit_ui.json')
        document['original_log_sha256'] = '0'*64
        checked = audit.verify_jlog(folder, document, session, ui)
        self.assertTrue(any(i['code'] == 'original_jlog_copy_hash_or_size_mismatch' for i in checked['issues']))
        session['case_code'] = 'WRNG-CASE-IDXX-0000'
        checked = audit.verify_jlog(folder, document, session, ui)
        self.assertTrue(any(i['code'] == 'invalid_public_original_jlog_identity' for i in checked['issues']))

    def test_started_missing_result_is_an_issue_not_a_planned_case(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder/'requests.jsonl').write_text('', encoding='utf-8')
            checked = audit.audit_case(folder, {'case_id': 'started-fixture', 'arm': 'current', 'stage': 'pilot'}, [], 'current')
        self.assertFalse(checked['complete'])
        self.assertEqual(checked['status'], 'issues_found')
        self.assertTrue(checked['started'])


if __name__ == '__main__':
    unittest.main()
