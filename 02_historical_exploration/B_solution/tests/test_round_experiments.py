import copy
import json
import importlib.util
import math
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import pytest

from bsolver.experiments import variant_configs
from bsolver.round_experiments import (BASELINE, ERROR_MODES, FROZEN_CORE_SHA256,
    FixedErrorField, bootstrap_mean_ci, compare_paired, core_digest, frozen_baseline,
    generate_manifest, make_scenario, model_distribution, run_scenario, stage1_configs,
    validate_distribution, validate_manifest)


def calibration():
    return {"schema_version": 1, "model_id": "test-fixture-not-official", "model_status": "test_fixture",
            "provenance": {"n_official_cases": 20}, "problems": {
                str(p): {"n_fit_known_joint": 10, "composition_candidates": {
                    m: {"status": "fit", "count_distribution": [
                        {"n": 10, "n_directed": 0 if p == 3 else 7, "probability": .4},
                        {"n": 16, "n_directed": 0 if p == 3 else 16, "probability": .6}]}
                    for m in ["empirical_joint", "smoothed_joint"]}} for p in [3, 4]}}


def test_baseline_is_frozen_and_only_limit_changes():
    assert core_digest() == FROZEN_CORE_SHA256
    for p in [3, 4]:
        baseline = asdict(frozen_baseline(p))
        assert baseline == asdict(variant_configs(p)["joint_triangular"])
        for variant, config in stage1_configs(p).items():
            changed = {key for key, val in asdict(config).items() if baseline[key] != val}
            assert changed == (set() if variant == BASELINE else {"local_measure_limit"})


def test_1000_distinct_stratified_scenarios_are_reproducible():
    a = generate_manifest(calibration())
    b = generate_manifest(calibration())
    assert a == b and len(a["scenarios"]) == 1000
    assert len({r["scenario_sha256"] for r in a["scenarios"]}) == 1000
    counts = Counter((r["pool"], r["problem"]) for r in a["scenarios"])
    assert counts == {("calibrated", 3): 200, ("calibrated", 4): 200,
                      ("broad", 3): 200, ("broad", 4): 200, ("stress", 3): 100, ("stress", 4): 100}
    support = Counter((r["n"], r["n_directed"]) for r in a["scenarios"] if r["pool"] == "broad" and r["problem"] == 4)
    assert len(support) == 98 and min(support.values()) >= 2
    assert Counter(r["partition"] for r in a["scenarios"]) == {"development": 800, "confirmation": 200}
    validate_manifest(a)


def test_calibrated_compositions_use_model_and_not_heuristic_direction_fraction():
    manifest = generate_manifest(calibration(), sizes=(40, 0, 0))
    for r in manifest["scenarios"]:
        expected = {(10, 0), (16, 0)} if r["problem"] == 3 else {(10, 7), (16, 16)}
        assert (r["n"], r["n_directed"]) in expected
        assert sum(s["direction_deg"] is not None for s in r["sources"]) == r["n_directed"]


def test_unknown_joint_counts_cannot_masquerade_as_calibration():
    bad = calibration()
    bad["problems"]["4"]["n_fit_known_joint"] = 0
    with pytest.raises(ValueError, match="no official"):
        generate_manifest(bad)
    with pytest.raises(ValueError):
        generate_manifest(None)
    # No model is necessary for a clearly labelled broad-only benchmark.
    assert generate_manifest(sizes=(0, 4, 0))["calibration_model_id"] is None


def test_illegal_probability_and_support_are_rejected():
    for rows in [[{"n": 10, "n_directed": 11, "probability": 1}],
                 [{"n": 10, "n_directed": 0, "probability": .7}],
                 [{"n": 10, "n_directed": 0, "probability": math.nan}]]:
        with pytest.raises(ValueError):
            validate_distribution(rows, 4)
    with pytest.raises(ValueError):
        validate_distribution([{"n": 10, "n_directed": 1, "probability": 1}], 3)


def test_fixed_error_field_reproduces_locations_across_arm_order():
    for mode in ERROR_MODES:
        case = make_scenario(1234, 4, 16, 16, error_mode=mode)
        specification = case["error_field"]
        a = FixedErrorField(specification["seed"], mode, specification["correlation_length_m"])
        b = FixedErrorField(specification["seed"], mode, specification["correlation_length_m"])
        points = [(0., 0.), (100., -37.), (1e-9, 1500.)]
        expected = {p: a(5, p) for p in points}
        assert {p: b(5, p) for p in reversed(points)} == expected
        assert all(-1 <= value <= 1 for value in expected.values())


@pytest.mark.parametrize("nd", [0, 1, 8, 16])
def test_hidden_last_layout_keeps_exact_composition(nd):
    case = make_scenario(202, 4, 16, nd, pool="stress", layout="hidden_outward_last")
    assert sum(s["direction_deg"] is not None for s in case["sources"]) == nd
    assert case["sources"][-1]["position"] == [1800., 0.]
    if nd:
        assert case["sources"][-1]["direction_deg"] == 0


def test_manifest_rejects_tampered_environment():
    manifest = generate_manifest(sizes=(0, 2, 0))
    manifest["scenarios"][0]["sources"][0]["radius"] += 1
    with pytest.raises(ValueError, match="modified"):
        validate_manifest(manifest)


def fake_result(variant, complete, t):
    return {"case_id": "fixture", "variant": variant, "problem": 4, "pool": "broad", "composition_model": "broad_joint",
        "partition": "development", "error_mode": "extreme", "scenario_sha256": "same-scene",
        "error_field_sha256": "same-field", "policy_core_sha256": "same-core", "evaluation_complete": complete,
        "failure_penalized_time_s": t if complete else 360000., "total_virtual_time_s": t}


def test_early_failure_is_never_short_completed_time():
    pairs, summary = compare_paired([fake_result(BASELINE, True, 10000), fake_result("joint_triangular_l1", False, 1)], bootstrap_draws=20)
    assert pairs[0]["penalized_time_delta_s"] == 350000
    assert pairs[0]["completed_time_delta_s"] is None
    assert pairs[0]["completion_delta"] == -1
    assert all(r["candidate_failures"] == 1 and r["both_complete_n"] == 0 for r in summary)


def test_mismatched_fixed_field_refuses_paired_comparison():
    base = fake_result(BASELINE, True, 10000)
    candidate = fake_result("joint_triangular_l1", True, 9000)
    candidate["error_field_sha256"] = "different"
    with pytest.raises(ValueError, match="Unpaired"):
        compare_paired([base, candidate])


def test_paired_bootstrap_is_repeatable():
    a = bootstrap_mean_ci([-10., 5., -4., -3.], seed=22, draws=300)
    assert a == bootstrap_mean_ci([-10., 5., -4., -3.], seed=22, draws=300)
    assert a[0] <= -3 <= a[1]


def test_actual_local_run_repeats_and_keeps_truth_external(tmp_path):
    case = make_scenario(213, 3, 10, 0, layout="clustered")
    a = run_scenario(case, "joint_triangular_l1", tmp_path / "a")
    b = run_scenario(case, "joint_triangular_l1", tmp_path / "b")
    for key in ["total_virtual_time_s", "clear_successes", "measures", "switches", "clear_attempts", "scenario_sha256", "error_field_sha256"]:
        assert a[key] == b[key]
    assert a["evaluation_complete"] and b["evaluation_complete"]
    assert a["hull_invariant_violations"] == []
    assert (tmp_path / "a/requests.jsonl.gz").exists()
    assert (tmp_path / "a/decisions.jsonl.gz").exists()


def load_script(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_protocol_mechanism_arms_share_base_geometry_and_exact_protocols():
    module = load_script("run_protocol_checks")
    plan = module.prepare(None, models=module.parse_models("min:hash,mixed:correlated,max:extreme"),
                          counts=(1, 1), fixture_broad=True)
    assert plan["planned_runs"] == 12
    grouped = {}
    for item in plan["cases"]:
        a, scene = item["assignment"], item["scenario"]
        assert a["environment"] == "local" and a["split"] == "development"
        assert a["protocol"] in ("baseline", "survey")
        assert a["composition_model"] == "broad_assumption"
        assert a["correlation_length"] == scene["error_field"]["correlation_length_m"] == 150.
        geometry = [(s["channel"], s["position"], s["direction_deg"]) for s in scene["sources"]]
        if a["case_id"] in grouped:
            assert geometry == grouped[a["case_id"]]
        grouped[a["case_id"]] = geometry
    assert len(grouped) == 4


def test_real_protocol_adapter_surveys_before_clearing_and_emits_readable_audit(tmp_path):
    module = load_script("run_protocol_checks")
    plan = module.prepare(None, models=module.parse_models("mixed:hash"), counts=(0, 1), fixture_broad=True)
    item = next(c for c in plan["cases"] if c["assignment"]["problem"] == 3)
    result = module.run_one(item, tmp_path)
    assert result["evaluation_complete"] and result["phase_stats"]["coverage"]["measures"] == 140
    assert result["phase_stats"]["survey"]["measures"] == 32
    folder = tmp_path / "models" / item["assignment"]["model_candidate"] / "cases" / item["assignment"]["case_id"]
    events = [json.loads(line) for line in (folder / "decisions.jsonl").read_text(encoding="utf8").splitlines()]
    assert all(e["phase"] == "cleanup" for e in events if e["event"] == "clear")
    audit = json.loads((folder / "post_exit_audit.json").read_text(encoding="utf8"))
    assert audit["N"] == item["scenario"]["n"] and audit["Ndir"] == 0
    from bsolver.calibration import extract_case
    extracted = extract_case(folder)
    assert extracted["case"]["source_kind"] == "local"
    assert extracted["case"]["protocol"] == "survey" and extracted["case"]["split"] == "development"
    assert extracted["case"]["issues"] == []


@pytest.mark.parametrize("count", [40, 42, 60])
def test_official_validation_plan_is_balanced_independent_and_only_design(tmp_path, count):
    module = load_script("run_calibrated_experiments")
    design = module.validation_design(tmp_path, cases_per_problem=count)
    assert design["practice_validation_in_authorized_plan"] and not design["dispatch_ready"]
    assert not design["formal_authorized"]
    for p in (3, 4):
        rows = [r for r in design["assignments"] if r["problem"] == p]
        assert [r["sequence"] for r in rows] == list(range(1, count + 1))
        assert Counter(r["arm"] for r in rows) == {"baseline": count // 2, "candidate": count // 2}
        assert all(not r["case_reuse"] for r in rows)


def retained_mechanisms():
    return [{"id": f"mechanism-{i}", "radius_mode": radius, "error_mode": error,
             "correlation_length": 150., "status": "unresolved" if i else "matched"}
            for i, (radius, error) in enumerate([("mixed", "deterministic"), ("mixed", "correlated"),
                ("mixed", "extreme"), ("min", "deterministic"), ("max", "deterministic")])]


def test_retained_mechanisms_only_control_calibrated_pool_and_partition_is_not_aliased():
    manifest = generate_manifest(calibration(), mechanism_candidates=retained_mechanisms())
    calibrated = [c for c in manifest["scenarios"] if c["pool"] == "calibrated"]
    assert {c["mechanism_id"] for c in calibrated} == {f"mechanism-{i}" for i in range(5)}
    assert {c["mechanism_status"] for c in calibrated} == {"matched", "unresolved"}
    assert len({c["mechanism_id"] for c in calibrated if c["partition"] == "confirmation"}) == 5
    assert all(c["error_field"]["correlation_length_m"] == 150 for c in calibrated)
    broad = [c for c in manifest["scenarios"] if c["pool"] == "broad"]
    assert {c["radius_mode"] for c in broad} == {"min", "max", "mixed"}
    assert {c["error_mode"] for c in broad} == set(ERROR_MODES)
    assert len({c["mechanism_id"] for c in broad}) == 27
    bad = retained_mechanisms()
    bad[0]["status"] = "biased"
    with pytest.raises(ValueError, match="Biased"):
        generate_manifest(calibration(), mechanism_candidates=bad)


def test_unspecified_mechanisms_are_explicit_assumptions_and_reported_separately():
    manifest = generate_manifest(calibration(), sizes=(20, 0, 0))
    assert manifest["calibrated_pool_mechanism_scope"] == "composition_only_assumptions"
    assert all(c["mechanism_status"] == "composition_only_assumptions" for c in manifest["scenarios"])
    a = fake_result(BASELINE, True, 10000)
    b = fake_result("joint_triangular_l1", True, 9000)
    a["mechanism_id"] = b["mechanism_id"] = "candidate-mechanism"
    a["mechanism_status"] = b["mechanism_status"] = "unresolved"
    _, groups = compare_paired([a, b], bootstrap_draws=20)
    assert {r["mechanism_id"] for r in groups} == {"__design_mixture__", "candidate-mechanism"}


def later_stage_manifests():
    module = load_script("run_candidate_stages")
    first = generate_manifest(sizes=(0, 10, 0), master_seed=22201)
    second = module.prepare_manifest(None, stage=2, limits=(1, 3), previous_manifests=[first], sizes=(0, 10, 0))
    return module, first, second


def test_stage2_and_stage3_are_single_factor_and_use_new_worlds():
    module, first, second = later_stage_manifests()
    for arms in second["arm_definitions"].values():
        assert arms[0]["solver_config"] == arms[1]["solver_config"]
        assert [a["solver_class"] for a in arms] == ["normal", "nosignal"]
    third = module.prepare_manifest(None, stage=3, limits=(1, 3), previous_manifests=[first, second],
                                    sizes=(0, 10, 0), modes=("normal", "nosignal"))
    for p, arms in third["arm_definitions"].items():
        reference = arms[0]["solver_config"]
        for arm in arms:
            differences = {k for k, v in arm["solver_config"].items() if reference[k] != v}
            assert differences <= {"scheduling"}
            assert arm["solver_class"] == ("normal" if p == "3" else "nosignal")
            assert arm["nosignal_config"] == arms[0]["nosignal_config"]
    seeds = [set(c["seed"] for c in m["scenarios"]) for m in [first, second, third]]
    assert not seeds[0] & seeds[1] and not seeds[0] & seeds[2] and not seeds[1] & seeds[2]
    with pytest.raises(ValueError, match="new predeclared"):
        module.prepare_manifest(None, stage=2, limits=(1, 1), previous_manifests=[first], sizes=(0, 2, 0), master_seed=first["master_seed"])


def test_later_stages_inherit_latest_composition_models_without_default_reset():
    module = load_script("run_candidate_stages")
    model = calibration()
    for p in ("3", "4"):
        candidates = model["problems"][p]["composition_candidates"]
        candidates["broad_joint"] = copy.deepcopy(candidates["smoothed_joint"])
    first = generate_manifest(model, sizes=(20, 0, 0), calibrated_models=("smoothed_joint", "broad_joint"))
    second = module.prepare_manifest(model, stage=2, limits=(1, 3), previous_manifests=[first], sizes=(20, 0, 0))
    # Reverse input order: inheritance must use the highest stage, not list order.
    third = module.prepare_manifest(model, stage=3, limits=(1, 3), previous_manifests=[second, first], sizes=(20, 0, 0))
    for manifest in (second, third):
        assert manifest["calibrated_models"] == ["smoothed_joint", "broad_joint"]
        assert {c["composition_model"] for c in manifest["scenarios"]} == {"smoothed_joint", "broad_joint"}


def test_later_stage_confirmation_requires_complete_development_and_frozen_choice():
    module, _, manifest = later_stage_manifests()
    rows = [{"case_id": c["case_id"], "variant": a["variant"], "partition": "development",
             "manifest_sha256": manifest["manifest_sha256"]}
            for c in manifest["scenarios"] if c["partition"] == "development"
            for a in manifest["arm_definitions"][str(c["problem"])]]
    with pytest.raises(ValueError, match="requires a previously frozen"):
        module.validate_selection(manifest, None)
    with pytest.raises(ValueError, match="complete development"):
        module.create_selection(manifest, rows[:-1], ("normal", "nosignal"))
    selected = module.create_selection(manifest, rows, ("normal", "nosignal"))
    module.validate_selection(manifest, selected)
    selected["selected_variants"]["3"] = "nosignal"
    with pytest.raises(ValueError, match="hash"):
        module.validate_selection(manifest, selected)


def test_stage2_actual_normal_and_nosignal_adapter_share_case_and_certificates(tmp_path):
    module, _, manifest = later_stage_manifests()
    case = next(c for c in manifest["scenarios"] if c["problem"] == 3)
    results = [module.run_arm(case, arm, manifest, tmp_path / arm["variant"]) for arm in manifest["arm_definitions"]["3"]]
    assert all(r["evaluation_complete"] and r["hull_invariant_violations"] == [] for r in results)
    assert len({r["scenario_sha256"] for r in results}) == len({r["error_field_sha256"] for r in results}) == 1
    assert results[1]["nosignal_choices"] >= 1
    assert results[0]["config"] == results[1]["config"]
    pairs, _ = compare_paired(results, baseline="normal", bootstrap_draws=20)
    assert len(pairs) == 1 and pairs[0]["both_complete"]
