"""Collector tests run without a desktop simulator or network calls."""
import importlib.util
import json
from pathlib import Path
from collections import Counter

import pytest

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/collect_official_round.py'
spec=importlib.util.spec_from_file_location('collection_for_test',SCRIPT)
collector=importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def test_preregistered_whole_case_allocation_and_resume(tmp_path):
    plan=collector.prepare(tmp_path)
    counts=Counter((r['problem'],r['protocol'],r['split']) for r in plan['cases'])
    for p in (3,4):
        assert counts[p,'baseline','fit']==45
        assert counts[p,'baseline','development']==15
        assert counts[p,'survey','fit']==15
        assert counts[p,'survey','development']==5
    assert len({r['case_id'] for r in plan['cases']})==160
    assert not plan['formal_authorized']
    assert collector.prepare(tmp_path,seed=9)==plan
    for r in plan['cases']:
        assert json.loads((tmp_path/'cases'/r['case_id']/'assignment.json').read_text())==r


def sample_files(folder, cleared=10):
    collector.write_json(folder/'session.json',{'case_code':'ABCD-EFGH-IJKL-MNOP'})
    collector.write_json(folder/'result.json',{'clear_successes':cleared,'status':'complete','total_virtual_time_s':1500})
    case={'case_id':'test','problem':4,'protocol':'survey','split':'development'}
    return case


def snapshot(*text):
    return {'items':[{'name':s} for s in text]}


def test_post_exit_only_known_counts(tmp_path):
    case=sample_files(tmp_path)
    s=snapshot('ABCD-EFGH-IJKL-MNOP','测试已结束',
               '本次案例含干扰源10个，其中全向3个、定向7个。')
    audit=collector.audit_finished(case,tmp_path,s)
    assert audit['source_total_post_exit']==10
    assert audit['directional_total_post_exit']==7
    assert audit['original_log_filename'] is None
    original=json.loads((tmp_path/'result.json').read_text())
    assert 'source_total_post_exit' not in original


def test_missing_truth_stays_unknown(tmp_path):
    case=sample_files(tmp_path)
    audit=collector.audit_finished(case,tmp_path,snapshot('ABCD-EFGH-IJKL-MNOP','测试已结束'))
    assert audit['source_total_post_exit'] is None
    assert audit['clear_fraction'] is None


def test_wrong_case_and_false_completion_fail_closed(tmp_path):
    case=sample_files(tmp_path,cleared=9)
    with pytest.raises(RuntimeError,match='does not match'):
        collector.audit_finished(case,tmp_path,snapshot('ZZZZ-EFGH-IJKL-MNOP','测试已结束'))
    with pytest.raises(RuntimeError,match='False complete'):
        collector.audit_finished(case,tmp_path,snapshot('ABCD-EFGH-IJKL-MNOP','测试已结束',
                         '本次案例含干扰源10个，其中全向3个、定向7个。'))
    assert (tmp_path/'post_exit_audit.json').exists()
