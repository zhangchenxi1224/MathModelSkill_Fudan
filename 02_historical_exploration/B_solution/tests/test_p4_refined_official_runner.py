"""Offline dispatch contract tests: fake collector or explicit LocalSimulator only."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("p4_refined_official_test", ROOT/"scripts/run_p4_refined_official.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
from bsolver.protocol import RobotClient, TransportError
from bsolver.simulator import LocalSimulator, Source


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    # Any accidental official/UI call in an offline test fails immediately.
    def forbidden(*args, **kwargs):
        raise AssertionError("Official interface is forbidden in offline tests")
    monkeypatch.setattr(runner.collector, "ui", forbidden)
    monkeypatch.setattr(runner.collector, "start_bridge", forbidden)
    return runner.prepare(tmp_path/"study", per_arm=2)


def sources():
    return [Source(i+1, (80.+30*i, 40.+10*i), 1100., None) for i in range(10)]


def run_local(checked, case):
    folder = checked["out"]/"cases"/case["case_id"]
    env = LocalSimulator(sources(), enforce_case_size=True)
    def factory():
        return RobotClient(transport=env, log_path=folder/"requests.jsonl", request_prefix=case["case_id"])
    result = runner.run_policy(case, folder, "local-only", "unused://offline", client_factory=factory)
    assert env.summary()["all_cleared"]
    code = "AAAA-BBBB-CCCC-DDDD"
    runner.collector.write_json(folder/"session.json", {"case_code": code, "problem": 4})
    runner.collector.write_json(folder/"post_exit_ui.json", {"items": [{"name": code}, {"name": "测试已结束"}]})
    logname = "practice-p4-123-"+code+".jlog"
    target = folder/"original_logs"/logname
    target.parent.mkdir()
    target.write_bytes(b"explicit local fixture; not an official behavior log")
    audit = {"case_id": case["case_id"], "problem": 4, "protocol": case["arm"], "split": case["stage"],
        "status": result["status"], "case_code": code, "source_total_post_exit": 10,
        "directional_total_post_exit": 0, "omnidirectional_total_post_exit": 10,
        "cleared": result["clear_successes"], "total_virtual_time_s": result["total_virtual_time_s"],
        "original_log_filename": logname, "original_log_sha256": runner.sha(target),
        "original_log_bytes": target.stat().st_size}
    runner.collector.write_json(folder/"post_exit_audit.json", audit)
    return result, folder


def test_design_has_2_separate_pilots_and_15_balanced_randomized_blocks():
    rows = runner.allocations()
    assert len(rows) == 62 and sum(r["pilot"] for r in rows) == 2
    assert rows == runner.allocations() and rows != runner.allocations(seed=1)
    for block in range(1, 16):
        group = [r["arm"] for r in rows if r["stage"] == "validation" and r["block"] == block]
        assert sorted(group) == sorted([*runner.ARMS, *runner.ARMS])
    assert all(r["source_truth"] is None and r["problem"] == 4 and r["protocol"] == r["arm"] for r in rows)


@pytest.mark.parametrize("kwargs", [{"per_arm": 3}, {"per_arm": 0}, {"per_arm": True}, {"pilot_per_arm": 0}])
def test_illegal_design_rejected(kwargs):
    with pytest.raises(ValueError):
        runner.allocations(**kwargs)


def test_offline_prepare_freezes_18_sources_and_truth_free_bundle(prepared):
    assert len(prepared["frozen"]["files"]) == 18
    assert prepared["bundle"]["current_spec"]["solver_config"]["local_measure_limit"] == 1
    assert prepared["bundle"]["current_spec"]["solver_config"]["joint_detour_m"] == 800
    assert len(prepared["bundle"]["refinement"]["refined_route"]) == 25
    assert "scenarios" not in prepared["bundle"] and "sources" not in prepared["bundle"]
    assert runner.preflight(prepared["out"])["frozen"] == prepared["frozen"]
    assert (prepared["out"]/"baseline_freeze.json").read_bytes() == (ROOT/"results/round1/baseline_freeze.json").read_bytes()
    with pytest.raises(FileExistsError):
        runner.prepare(prepared["out"])


@pytest.mark.parametrize("target", ["plan.json", "policy_bundle.json", "source_snapshot.zip", "local_selection_input.json", "assignment"])
def test_artifact_tampering_is_rejected_offline(prepared, target):
    path = (prepared["out"]/"cases"/prepared["plan"]["cases"][0]["case_id"]/"assignment.json"
            if target == "assignment" else prepared["out"]/target)
    if target == "assignment":
        row = runner.read(path)
        row["arm"] = "combined_cover" if row["arm"] == "current" else "current"
        runner.collector.write_json(path, row)
    else:
        path.write_bytes(path.read_bytes()+b" ")
    with pytest.raises((ValueError, json.JSONDecodeError)):
        runner.preflight(prepared["out"])


@pytest.mark.parametrize("arm", runner.ARMS)
def test_exact_frozen_policy_local_end_to_end_and_mechanical_gate(prepared, arm):
    case = next(c for c in prepared["plan"]["cases"] if c["pilot"] and c["arm"] == arm)
    result, folder = run_local(prepared, case)
    assert result["status"] == "complete" and result["clear_successes"] == 10
    assert result["source_total"] is None and result["source_truth"] is None
    assert result["coverage_point_count"] == (25 if arm == "combined_cover" else 31)
    gate = runner.mechanical_gate(case, folder, prepared)
    assert gate["passed"], gate
    assert gate["max_request_cost_residual_s"] <= 2.1e-6
    with pytest.raises(RuntimeError, match="Attempt artifacts"):
        runner.run_policy(case, folder, "local-only", "unused://offline", client_factory=lambda: None)
    # A missing original behavior log cannot silently count as a successful pilot.
    audit = runner.read(folder/"post_exit_audit.json")
    audit["original_log_sha256"] = None
    runner.collector.write_json(folder/"post_exit_audit.json", audit)
    assert not runner.mechanical_gate(case, folder, prepared)["passed"]


def test_pending_request_failure_retained_without_second_action(prepared):
    case = prepared["plan"]["cases"][0]
    folder = prepared["out"]/"cases"/case["case_id"]
    calls = []
    def broken(path, body):
        calls.append((path, copy.deepcopy(body)))
        raise TransportError("local fault fixture")
    result = runner.run_policy(case, folder, "local", "unused://", client_factory=lambda:
        RobotClient(transport=broken, log_path=folder/"requests.jsonl", retry_delay=0))
    assert result["status"] != "complete" and result["pending_request"] is not None
    assert len(calls) == 4 and all(c == calls[0] for c in calls)
    assert (folder/"result.json").exists() and (folder/"requests.jsonl").exists()


def test_clear_other_target_channel_is_legal_and_does_not_retune():
    env = LocalSimulator([Source(2, (0., 0.))])
    client = RobotClient(transport=env)
    client.enter()
    response = client.clear(2, (0., 0.))
    assert response["accepted"] is True and client.current_channel == 1
    position, channel, cost = runner.public_action_cost((0., 0.), 1, client.history[-1])
    assert position == (0., 0.) and channel == 1 and cost == 5.
    client.exit()


class FakeCollector:
    def __init__(self, fail=False, stop=False):
        self.locks, self.calls, self.statuses = [], [], []
        self.started = self.closed = False
        self.fail, self.stop = fail, stop
    def collection_lock(self, path):
        lock = SimpleNamespace(path=path, closed=False)
        lock.close = lambda: setattr(lock, "closed", True)
        self.locks.append(lock)
        return lock
    def start_bridge(self):
        self.started = True
    def close_bridge(self):
        self.closed = True
    def status(self, out, status, **data):
        self.statuses.append(status)
    def collect(self, plan, out, robot_id, base_url, maximum, adopt=False, policy_runner=None):
        case = plan["cases"][0]
        assert maximum == 1 and adopt is False and callable(policy_runner)
        self.calls.append(case["case_id"])
        folder = out/"cases"/case["case_id"]
        runner.collector.write_json(folder/"launch_intent.json", {"fixture": True})
        if self.fail:
            raise RuntimeError("fixture collector failure")
        runner.collector.write_json(folder/"post_exit_audit.json", {"fixture": True})
        if self.stop:
            (out/"STOP_AFTER_CASE").touch()


def mocked_gate(case, out, checked):
    if not (Path(out)/"cases"/case["case_id"]/"post_exit_audit.json").exists():
        raise RuntimeError("Pilot not complete")
    return {"passed": True, "case_id": case["case_id"]}


def test_validation_gate_blocks_before_bridge_and_releases_three_locks(prepared):
    fake = FakeCollector()
    with pytest.raises(RuntimeError, match="Integrity pause"):
        runner.run_frozen_plan(prepared["out"], "fixture", stage="validation", collect_module=fake)
    assert len(fake.locks) == 3 and all(lock.closed for lock in fake.locks)
    assert not fake.started and not fake.calls


def test_completed_pilots_gate_validation_and_resume_preserves_allocation(prepared, monkeypatch):
    monkeypatch.setattr(runner, "enforce_finished", mocked_gate)
    first = FakeCollector()
    assert runner.run_frozen_plan(prepared["out"], "fixture", stage="pilot", collect_module=first)["new_cases"] == 2
    second = FakeCollector()
    answer = runner.run_frozen_plan(prepared["out"], "fixture", stage="validation", maximum=1, collect_module=second)
    assert answer["new_cases"] == 1 and len(second.calls) == 1
    third = FakeCollector()
    answer = runner.run_frozen_plan(prepared["out"], "fixture", stage="validation", collect_module=third)
    assert answer["new_cases"] == 3 and not set(second.calls).intersection(third.calls)
    assert runner.read(prepared["out"]/"pilot_gate.json")["passed"] is True


def test_collector_exception_pauses_and_preserves_launch(prepared, monkeypatch):
    fake = FakeCollector(fail=True)
    with pytest.raises(RuntimeError, match="fixture collector failure"):
        runner.run_frozen_plan(prepared["out"], "fixture", stage="pilot", collect_module=fake)
    assert len(fake.calls) == 1 and fake.closed and all(lock.closed for lock in fake.locks)
    assert fake.statuses[-1] == "needs_attention"
    assert (prepared["out"]/"cases"/fake.calls[0]/"launch_intent.json").exists()


def test_previous_bad_audit_never_skipped(prepared):
    case = prepared["plan"]["cases"][0]
    runner.collector.write_json(prepared["out"]/"cases"/case["case_id"]/"post_exit_audit.json", {"status": "incomplete"})
    fake = FakeCollector()
    with pytest.raises(RuntimeError, match="Integrity pause"):
        runner.run_frozen_plan(prepared["out"], "fixture", stage="pilot", collect_module=fake)
    assert not fake.started and not fake.calls


def test_empty_summary_separates_pilot_and_validation_and_keeps_not_started(prepared):
    report = runner.summarize(prepared["out"])
    assert report["bootstrap_repetitions"] == 10000
    assert "combined_cover_minus_current" not in report["stages"]["pilot"]
    for arm in runner.ARMS:
        assert report["stages"]["validation"]["arms"][arm]["not_started"] == 2
        assert report["stages"]["validation"]["arms"][arm]["failures_or_unverified"] == 0
    assert runner.wilson(0, 30)[1] > .11
    assert runner.independent_bootstrap([10., 10.], [7., 7.], repetitions=100)["ci95"] == [-3., -3.]
