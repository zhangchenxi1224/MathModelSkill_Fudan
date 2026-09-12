"""Study-integrity tests, independent of unreleased refinement defaults."""
from __future__ import annotations

import copy
import gzip
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("p4_refinement_runner", ROOT/"scripts/run_p4_refinement.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def calibration():
    return {"schema_version": 1, "model_id": "engineering-fixture", "model_status": "fixture", "problems": {
        str(p): {"n_fit_known_joint": 1, "composition_candidates": {
            m: {"status": "fit", "count_distribution": [{"n": 10, "n_directed": 0 if p == 3 else 5, "probability": 1.}]}
            for m in ("smoothed_joint", "broad_joint")}} for p in (3, 4)}}


def mechanisms():
    return [{"id": "fixture_correlated", "status": "unresolved", "radius_mode": "mixed", "error_mode": "correlated", "correlation_length": 150.},
            {"id": "fixture_extreme", "status": "unresolved", "radius_mode": "mixed", "error_mode": "extreme", "correlation_length": 150.}]


def candidate():
    return runner.read(ROOT/"results/round2/candidate.json")["problems"]["4"]


def manifest(**kwargs):
    return runner.build_manifest(calibration(), mechanisms(), candidate(),
        development_sizes=kwargs.pop("development_sizes", (2, 2, 2)),
        confirmation_sizes=kwargs.pop("confirmation_sizes", (2, 2, 2)), **kwargs)


def rehash(obj, key):
    obj.pop(key, None)
    obj[key] = runner.digest(obj)
    return obj


def test_full_design_has_200_development_800_independent_confirmation_worlds():
    m = runner.build_manifest(calibration(), mechanisms(), candidate())
    assert len(m["scenarios"]) == 1000
    counts = runner.Counter((c["partition"], c["pool"]) for c in m["scenarios"])
    assert counts == {("development", "calibrated"): 80, ("development", "broad"): 80,
        ("development", "stress"): 40, ("confirmation", "calibrated"): 320,
        ("confirmation", "broad"): 320, ("confirmation", "stress"): 160}
    assert all(c["problem"] == 4 for c in m["scenarios"])
    assert len({runner.world_signature(c) for c in m["scenarios"]}) == 1000
    assert len({c["seed"] for c in m["scenarios"]}) == 1000
    assert m["planned_development_runs"] == 800
    assert m["arms"] == list(runner.DEFAULT_ARMS)
    assert m["current_spec"] == candidate()
    runner.validate_manifest(m)
    assert runner.build_manifest(calibration(), mechanisms(), candidate()) == m


def test_adaptive_is_explicit_independent_arm_and_confirm_not_counted_as_all_four():
    m = manifest(include_adaptive=True)
    assert m["arms"][-1] == "local_adaptive" and "combined_adaptive" not in m["arms"]
    assert m["planned_development_runs"] == 6*5
    assert isinstance(m["confirmation_runs"], str)


def test_partition_seed_reuse_and_old_world_reuse_are_rejected():
    with pytest.raises(ValueError, match="independent"):
        manifest(development_seed=1, confirmation_seed=1)
    old = manifest()
    with pytest.raises(ValueError, match="overlaps"):
        manifest(previous=[old])


def test_scene_tampering_is_detected_before_dispatch():
    m = manifest()
    m["scenarios"][0]["sources"][0]["radius"] = 1001
    with pytest.raises(ValueError, match="modified"):
        runner.validate_manifest(m)


def test_fixed_error_field_shared_by_arms_and_same_point_queries():
    m = manifest()
    case = m["scenarios"][0]
    f = case["error_field"]
    a = runner.FixedErrorField(f["seed"], f["mode"], f["correlation_length_m"])
    b = runner.FixedErrorField(f["seed"], f["mode"], f["correlation_length_m"])
    source = runner.Source(**case["sources"][0])
    # Public error function has no arm/order dependence.
    assert a(source, (10., 20.)) == b(source, (10., 20.)) == a(source, (10., 20.))


def test_current_exact_candidate_and_coverage_replacement_precedes_ledger():
    m = manifest()
    frozen = {"refinement": {"refined_route": [(0., 0.), (1., 1.)], "coverage_certificate": {"kind": "engineering_fixture"}}}
    env = runner.LocalSimulator([runner.Source(**s) for s in m["scenarios"][0]["sources"]])
    current = runner.make_policy(runner.RobotClient(transport=env), "current", m, frozen, None)
    other = runner.make_policy(runner.RobotClient(transport=env), "coverage", m, frozen, None)
    assert runner.asdict(current.config) == candidate()["solver_config"]
    assert runner.canonical(runner.asdict(current.nosignal_config)) == runner.canonical(candidate()["nosignal_config"])
    assert current.points != other.points and other.points == [(0., 0.), (1., 1.)]
    assert all(not k.coverage_indices for k in other.channels.values())
    for knowledge in other.channels.values():
        knowledge.coverage_indices.update(range(len(other.points)))
    evidence = other._complete_evidence()
    assert evidence["construction"] == "refined_directional_triangular"
    assert evidence["refined_coverage_certificate"] == frozen["refinement"]["coverage_certificate"]
    assert evidence["coverage_points_sha256"] == runner.digest(other.points)
    assert evidence["required_point_count"] == 2
    other._record("start")
    other._record("finish", result={})
    assert all(e["coverage_variant"] == "refined_directional_triangular" for e in other.decisions)
    assert other.decisions[-1]["result"]["coverage_points_sha256"] == runner.digest(other.points)


def test_stopping_tail_only_evaluator_knows_total_and_incomplete_tail_is_unknown():
    events = [{"sequence": 1, "event": "clear", "virtual_time_s": 10., "response": {"clear_result": "success"}},
              {"sequence": 2, "event": "measure", "virtual_time_s": 20.}]
    assert runner.stopping_tail(events, 1, 25., True) == {
        "last_source_clear_time_s": 10., "post_clear_stop_tail_s": 15., "post_clear_measurements": 1}
    assert runner.stopping_tail(events, 2, 25., False)["post_clear_stop_tail_s"] is None


def fixture_results(m, frozen, deltas=None):
    deltas = deltas or {"coverage": -10., "local_cover": -20., "combined_cover": -30.}
    rows = []
    for c in m["scenarios"]:
        if c["partition"] != "development":
            continue
        for arm in m["arms"]:
            t = 100.+deltas.get(arm, 0.)
            rows.append({"case_id": c["case_id"], "variant": arm, "partition": "development", "pool": c["pool"],
                "manifest_sha256": m["manifest_sha256"], "source_freeze_sha256": frozen["source_freeze_sha256"],
                "evaluation_complete": True, "hull_invariant_violations": [], "total_virtual_time_s": t,
                "failure_penalized_time_s": t})
    return rows


def test_selection_uses_only_development_and_retains_eligible_coverage_comparator():
    m = manifest()
    f = {"source_freeze_sha256": "fixture"}
    rows = fixture_results(m, f)
    selection = runner.choose_selection(m, f, rows)
    assert selection["selected_variants"] == ["combined_cover", "coverage"]
    assert selection["confirmation_used"] is False
    rows[0]["partition"] = "confirmation"
    with pytest.raises(ValueError, match="development"):
        runner.choose_selection(m, f, rows)


@pytest.mark.parametrize("bad", ["failure", "hull", "regression", "missing"])
def test_selection_never_forces_ineligible_candidate(bad):
    m = manifest()
    f = {"source_freeze_sha256": "fixture"}
    rows = fixture_results(m, f)
    for row in rows:
        if row["variant"] == "current":
            continue
        if bad == "failure":
            row.update(evaluation_complete=False, failure_penalized_time_s=360000.)
        elif bad == "hull":
            row["hull_invariant_violations"] = [{"sequence": 1}]
        elif bad == "regression" and row["pool"] == "stress":
            row.update(total_virtual_time_s=101., failure_penalized_time_s=101.)
    if bad == "missing":
        rows.pop()
        with pytest.raises(ValueError, match="exactly"):
            runner.choose_selection(m, f, rows)
    else:
        selection = runner.choose_selection(m, f, rows)
        assert selection["selected_variants"] == [] and selection["status"] == "no_eligible_candidate"


def test_release_and_confirmation_require_matching_frozen_selection():
    m = manifest()
    f = {"source_freeze_sha256": "fixture"}
    release = {"authorized": True, "manifest_sha256": m["manifest_sha256"],
        "source_freeze_sha256": "fixture", "partitions": ["development", "confirmation"]}
    assert runner.selected_arms(m, f, "development", release) == list(runner.DEFAULT_ARMS)
    selection = runner.choose_selection(m, f, fixture_results(m, f))
    with pytest.raises(ValueError, match="frozen development"):
        runner.selected_arms(m, f, "confirmation", release, selection)
    release["selection_sha256"] = selection["selection_sha256"]
    assert runner.selected_arms(m, f, "confirmation", release, selection) == ["current", "combined_cover", "coverage"]
    release["authorized"] = False
    with pytest.raises(ValueError, match="Explicit release"):
        runner.selected_arms(m, f, "development", release)


def test_harness_failure_is_saved_with_penalty_and_both_log_artifacts(tmp_path, monkeypatch):
    m = manifest(development_sizes=(0, 1, 0), confirmation_sizes=(0, 0, 0))
    f = {"source_freeze_sha256": "engineering-fixture"}
    monkeypatch.setattr(runner, "validate_freeze", lambda *_: None)
    def fail(*_):
        raise RuntimeError("intentional constructor failure fixture")
    monkeypatch.setattr(runner, "make_policy", fail)
    folder = tmp_path/"failed"
    row = runner.run_arm(m["scenarios"][0], "current", m, f, folder)
    assert row["evaluation_complete"] is False and row["failure_penalized_time_s"] == 360000.
    assert row["status"] == "harness_error" and "intentional" in row["error"]
    assert row["total_virtual_time_s"] == 0. and row["post_clear_stop_tail_s"] is None
    assert row["fallback_targets"] is None
    assert set(row["artifact_sha256"]) == {"requests.jsonl.gz", "decisions.jsonl.gz"}
    assert (folder/"result.json").exists()
    for name, digest in row["artifact_sha256"].items():
        assert runner.sha(folder/name) == digest
        with gzip.open(folder/name, "rt", encoding="utf-8") as fp:
            assert fp.read() == ""


def test_engineering_current_run_preserves_complete_logs_and_resume_hash_check(tmp_path, monkeypatch):
    # One existing-policy integration fixture, not a refinement performance experiment.
    from bsolver.round_experiments import make_scenario
    m = manifest(development_sizes=(0, 0, 0), confirmation_sizes=(0, 0, 0))
    case = make_scenario(202609239901, 4, 10, 0, radius_mode="max", error_mode="correlated")
    f = {"source_freeze_sha256": "engineering-fixture", "refinement": {}}
    monkeypatch.setattr(runner, "validate_freeze", lambda *_: None)
    row = runner.run_case(case, ["current"], m, f, tmp_path, False)[0]
    assert row["evaluation_complete"] and row["clear_successes"] == 10
    assert not row["hull_invariant_violations"]
    assert row["post_clear_stop_tail_s"] >= 0
    folder = tmp_path/"runs"/case["pool"]/case["case_id"]/"current"
    with gzip.open(folder/"requests.jsonl.gz", "rt", encoding="utf-8") as fp:
        requests = [json.loads(x) for x in fp]
    with gzip.open(folder/"decisions.jsonl.gz", "rt", encoding="utf-8") as fp:
        decisions = [json.loads(x) for x in fp]
    assert len(requests) == row["public_client_stats"]["transport_attempts"]
    assert decisions[0]["event"] == "start" and decisions[-1]["event"] == "finish"
    assert runner.run_case(case, ["current"], m, f, tmp_path, True)[0] == row
    (folder/"requests.jsonl.gz").write_bytes(b"corrupted fixture")
    with pytest.raises(ValueError, match="hash changed"):
        runner.run_case(case, ["current"], m, f, tmp_path, True)


def test_completed_quantiles_exclude_short_failure_but_penalty_retains_it():
    base = {"partition": "development", "pool": "broad", "composition_model": "broad_joint",
        "mechanism_id": "fixture", "variant": "current", "hull_invariant_violations": [],
        "walk_distance_m": 100., "measures": 2, "switches": 1, "failed_clear_attempts": 0,
        "failed_clear_cost_s": 0., "fallback_targets": 0, "post_clear_stop_tail_s": None,
        "post_clear_measurements": None, "movement_s": 20., "measurement_s": 10., "optical_s": 0.,
        "laser_s": 0., "program_real_time_s": .1}
    rows = [dict(base, total_virtual_time_s=100., evaluation_complete=True, failure_penalized_time_s=100.),
            dict(base, total_virtual_time_s=1., evaluation_complete=False, failure_penalized_time_s=360000.)]
    for group in runner.descriptive_summary(rows):
        assert group["complete"] == 1 and group["failures"] == 1
        assert group["total_virtual_time_s_mean"] == 100.
        assert group["penalized_time_mean_s"] == 180050.
