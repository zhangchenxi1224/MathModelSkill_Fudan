"""Regression explanations must preserve failures and reconcile real actions."""
import gzip
import importlib.util
import json
from pathlib import Path

import pytest

from bsolver.protocol import RobotClient
from bsolver.simulator import LocalSimulator, Source
from bsolver.strategy import Solver, SolverConfig

PATH = Path(__file__).resolve().parents[1]/"scripts/diagnose_paired_regressions.py"
SPEC = importlib.util.spec_from_file_location("diagnose_paired_regressions", PATH)
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)


def result(case, variant, cost, *, complete=True, pool="calibrated", problem=3):
    return {"case_id": case, "variant": variant, "problem": problem, "pool": pool,
            "partition": "development", "scenario_sha256": case, "error_field_sha256": case,
            "policy_core_sha256": "frozen", "evaluation_complete": complete,
            "failure_penalized_time_s": cost if complete else 360000., "total_virtual_time_s": cost}


def test_largest_signed_delta_and_all_failures_not_only_selected_variant():
    rows = []
    for name, cost in (("a", 90), ("b", 95)):
        rows += [result(name, diagnostic.BASELINE, 100), result(name, "joint_triangular_l1", cost)]
    rows.append(result("a", "joint_triangular_l2", 10, complete=False))
    selected = diagnostic.select_pairs(rows, {"3": "joint_triangular_l1", "4": "joint_triangular_l1"})
    assert {(r["case_id"], r["variant"]) for r in selected} == {("b", "joint_triangular_l1"), ("a", "joint_triangular_l2")}
    worst = next(r for r in selected if r["case_id"] == "b")
    assert worst["delta_penalized_s"] == -5


def test_confirmation_unpaired_duplicate_or_missing_selection_rejected():
    base, other = result("a", diagnostic.BASELINE, 100), result("a", "joint_triangular_l1", 120)
    with pytest.raises(ValueError, match="DEVELOPMENT"):
        diagnostic.paired_rows([{**base, "partition": "confirmation"}])
    with pytest.raises(ValueError, match="Duplicate"):
        diagnostic.paired_rows([base, base])
    with pytest.raises(ValueError, match="scenario_sha256"):
        diagnostic.paired_rows([base, {**other, "scenario_sha256": "different"}])
    with pytest.raises(ValueError, match="selected arm"):
        diagnostic.select_pairs([base, other], {"3": "joint_triangular_l2", "4": diagnostic.BASELINE})


def test_selection_requires_explicit_development_freeze():
    with pytest.raises(ValueError, match="development_only"):
        diagnostic.normalized_selection({"selected_local_limits": {"3": 1, "4": 2}})
    chosen = diagnostic.normalized_selection({"selection_basis": "development_only", "manifest_sha256": "frozen",
                                             "selected_local_limits": {"3": 1, "4": 2}})
    assert chosen == {"3": "joint_triangular_l1", "4": "joint_triangular_l2"}


def test_l5_selection_has_no_fabricated_self_regression():
    rows = [result("a", diagnostic.BASELINE, 100), result("a", "joint_triangular_l1", 120)]
    assert diagnostic.select_pairs(rows, {"3": diagnostic.BASELINE, "4": diagnostic.BASELINE}) == []
    rows[1]["evaluation_complete"] = False
    rows[1]["failure_penalized_time_s"] = 360000
    entries = diagnostic.select_pairs(rows, {"3": diagnostic.BASELINE, "4": diagnostic.BASELINE})
    assert len(entries) == 1 and entries[0]["selection_reasons"] == ["actual_failure_in_candidate_or_paired_baseline"]


def test_replay_clear_target_uses_knowledge_not_current_receiver_channel():
    def req(i, kind, channel, p, time, outcome):
        return {"path": "/"+kind, "outcome": "accepted", "request": {"request_id": str(i), "channel": channel,
                "position": {"x": p[0], "y": p[1]}}, "response": {"accepted": True, "virtual_time_s": time,
                 "measure_result" if kind == "measure" else "clear_result": outcome}}
    rows = [req(1, "measure", 2, (5, 0), 7, "no_signal"), req(2, "clear", 1, (5, 0), 12, "success")]
    decisions = [{"sequence": 0, "event": "measure", "channel": 2, "position": [5, 0], "reason": "local_active_sensing",
                  "knowledge": {"channel": 2}, "response": rows[0]["response"]},
                 {"sequence": 1, "event": "clear", "channel": 2, "position": [5, 0], "certificate": {"type": "optical_grid_attempt"},
                  "knowledge": {"channel": 1}, "response": rows[1]["response"]}]
    replay = diagnostic.replay_costs([rows[0], rows[0], rows[1]], decisions)
    assert replay["total_replayed_s"] == 12
    assert replay["components_s"] == {"move_s": 1, "switch_s": 1, "measure_s": 5, "optical_s": 3, "laser_s": 2}
    assert replay["diagnostics"]["fallback_channels"] == [1]
    assert replay["diagnostics"]["no_signal_by_reason"] == {"local_active_sensing": 1}


def test_actual_local_fixture_end_to_end_without_confirmation_read(tmp_path):
    run_root = tmp_path/"local"
    records = []
    for variant, limit in ((diagnostic.BASELINE, 5), ("joint_triangular_l1", 1)):
        directory = run_root/"runs/calibrated/fixture"/variant
        directory.mkdir(parents=True)
        environment = LocalSimulator([Source(1, (600, 100), 1200)], error_mode="zero")
        client = RobotClient(robot_id="local-team", transport=environment, log_path=str(directory/"requests.jsonl"))
        solver = Solver(client, SolverConfig(problem=3, local_measure_limit=limit))
        value = solver.run()
        client.close_log()
        assert value["status"] == "complete"
        value.update(result("fixture", variant, value["total_virtual_time_s"]))
        diagnostic.save(directory/"result.json", value)
        for name, rows in (("requests", [json.loads(line) for line in (directory/"requests.jsonl").read_text().splitlines()]),
                           ("decisions", solver.decisions)):
            with gzip.open(directory/(name+".jsonl.gz"), "wt", encoding="utf-8") as stream:
                stream.write("".join(json.dumps(row)+"\n" for row in rows))
        records.append(value)
    diagnostic.save(run_root/"development/results.json", records)
    # Deliberately invalid content here: any attempted read would fail.
    (run_root/"results.json").write_text("DO NOT READ CONFIRMATION AGGREGATE")
    selection = tmp_path/"selection.json"
    diagnostic.save(selection, {"selection_basis": "development_only", "manifest_sha256": "fixture",
                               "selected_local_limits": {"3": 1, "4": 1}})
    summary = diagnostic.run(run_root, selection, tmp_path/"diagnostic")
    assert summary["diagnosed_pairs"] == 1 and not summary["errors"]
    assert summary["confirmation_read"] is False
    explanation = diagnostic.load(tmp_path/"diagnostic/cases/fixture__joint_triangular_l1.json")
    assert sum(explanation["component_deltas_s"].values()) == pytest.approx(explanation["delta_observed_virtual_s"], abs=1e-5)
