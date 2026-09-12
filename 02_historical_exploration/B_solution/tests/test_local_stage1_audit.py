import copy
import gzip
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

PATH = Path(__file__).resolve().parents[1] / "scripts/audit_local_stage1.py"
SPEC = importlib.util.spec_from_file_location("independent_local_stage1", PATH)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def case_fixture(identity="case0"):
    case = {"case_id": identity, "problem": 3, "pool": "broad", "composition_model": "broad_joint",
            "partition": "development", "mechanism_id": "test", "mechanism_status": "unresolved",
            "layout": "random", "radius_mode": "mixed", "error_mode": "correlated", "n": 10, "n_directed": 0,
            "error_field": {"seed": 17, "mode": "correlated", "correlation_length_m": 150., "definition": "FixedErrorField-v1"},
            "sources": [{"channel": c, "position": [100., 0.], "radius": 1200., "direction_deg": None} for c in range(1, 11)]}
    case["scenario_sha256"] = audit.digest(case)
    return case


def manifest_fixture():
    cases = [case_fixture(f"case{i}") for i in range(5)]
    order = sorted(cases, key=lambda c: audit.rank_seed(100, "confirmation_allocation", c["case_id"]))
    order[0]["partition"] = "confirmation"
    for case in cases:
        case.pop("scenario_sha256")
        case["scenario_sha256"] = audit.digest(case)
    manifest = {"stage": 1, "master_seed": 100, "baseline": audit.BASELINE, "frozen_core_sha256": audit.CORE_SHA,
                "official_formal_authorized": False, "distinct_scenarios": 5, "planned_strategy_runs": 20,
                "pool_sizes": {"broad": 5}, "scenarios": cases}
    manifest["manifest_sha256"] = audit.digest(manifest)
    return manifest


def trace_fixture():
    state = {"position": {"x": 0., "y": 0.}, "current_channel": 1, "virtual_time_s": 0.,
             "cleared_count": 0, "cleared_channels": [], "entered": False, "exited": False}
    rows = []
    def action(path, channel=None, position=None, outcome=None):
        before = copy.deepcopy(state)
        body = {"request_id": str(len(rows) + 1)}
        response = {"accepted": True}
        if path == "/enter":
            state["entered"] = True
        elif path == "/exit":
            state["exited"] = True
        else:
            body.update(channel=channel, position={"x": position[0], "y": position[1]})
            old = tuple(state["position"].values())
            state["virtual_time_s"] += math.dist(old, position) / 5
            state["position"] = body["position"]
            if path == "/measure":
                state["virtual_time_s"] += 5 + int(channel != state["current_channel"])
                state["current_channel"] = channel
                response["measure_result"] = outcome
            else:
                state["virtual_time_s"] += 5 if outcome == "success" else 3
                if outcome == "success":
                    state["cleared_channels"].append(channel)
                    state["cleared_count"] += 1
                response["clear_result"] = outcome
        response["virtual_time_s"] = state["virtual_time_s"]
        rows.append({"request": body, "response": response, "path": path, "state_before": before,
                     "state_after": copy.deepcopy(state)})
    action("/enter")
    for channel in range(1, 11):
        action("/measure", channel, (100., 0.), "near")
        action("/clear", channel, (100., 0.), "success")
    action("/exit")
    return rows


def write_arm(directory, case, variant):
    directory.mkdir(parents=True, exist_ok=True)
    requests = trace_fixture()
    replay = audit.replay_module.replay_requests(requests)
    result = {k: case[k] for k in ("case_id", "problem", "pool", "composition_model", "partition", "mechanism_id",
                                  "mechanism_status", "layout", "radius_mode", "error_mode", "n", "n_directed", "scenario_sha256")}
    t = replay["computed_virtual_time_s"]
    result.update(variant=variant, policy_core_sha256=audit.CORE_SHA, runner_sha256="runner",
                  error_field_sha256=audit.digest(case["error_field"]), config=audit.expected_config(3, variant),
                  environment="self_built_fixed_scenario", source_total=10,
                  total_virtual_time_s=t, independently_accounted_time_s=t, status="complete", error=None,
                  hull_invariant_violations=[], evaluation_complete=True, failure_penalized_time_s=t, clear_fraction=1.)
    result.update({k: replay["counts"][k] for k in ("walk_distance_m", "switches", "measures", "clear_attempts", "clear_successes")})
    result["environment_summary"] = {"source_count": 10, "cleared_count": 10, "cleared_channels": list(range(1, 11)),
        "all_cleared": True, "current_channel": 10, "position": [100., 0.], "accepted_actions": 22, "virtual_time_s": t,
        "stats": {"walk_distance": 100., "switches": 9, "measures": 10, "clear_attempts": 10, "successes": 10, "failures": 0}}
    (directory / "result.json").write_text(json.dumps(result), encoding="utf8")
    with gzip.open(directory / "requests.jsonl.gz", "wt", encoding="utf8") as f:
        for row in requests:
            f.write(json.dumps(row) + "\n")
    with gzip.open(directory / "decisions.jsonl.gz", "wt", encoding="utf8") as f:
        f.write(json.dumps({"event": "start", "config": result["config"]}) + "\n")
    return result


class LocalStage1AuditTests(unittest.TestCase):
    def test_bundle_hash_is_file_mapping_not_zip_container_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            (p / "code.py").write_text("frozen code", encoding="utf8")
            files = {"code.py": audit.sha(p / "code.py")}
            (p / "manifest.json").write_text(json.dumps({"manifest_sha256": "manifest"}), encoding="utf8")
            frozen = {"files": files, "inputs": {}, "source_bundle_sha256": audit.digest(files), "manifest_sha256": "manifest"}
            (p / "source_manifest.json").write_text(json.dumps(frozen), encoding="utf8")
            with zipfile.ZipFile(p / "source_snapshot.zip", "w") as z:
                z.write(p / "code.py", "code.py")
            result = audit.check_freeze(p, p)
            # Deliberately tiny fixture lacks contest core, but archive members
            # and the correctly defined source mapping must pass.
            self.assertEqual({r["code"] for r in result["issues"]}, {"current_core_aggregate_mismatch"})
            self.assertEqual(result["files"][0]["current_sha256"], result["files"][0]["archived_sha256"])

    def test_manifest_hash_partition_and_source_counts(self):
        m = manifest_fixture()
        self.assertEqual(audit.check_manifest(m), [])
        m["scenarios"][0]["n"] = 11
        codes = {r["code"] for r in audit.check_manifest(m)}
        self.assertTrue({"manifest_content_hash_mismatch", "scenario_content_hash_mismatch", "source_composition_mismatch"} <= codes)

    def test_partition_tamper_detected_even_if_rehashed(self):
        m = manifest_fixture()
        c = next(c for c in m["scenarios"] if c["partition"] == "confirmation")
        c["partition"] = "development"
        c.pop("scenario_sha256")
        c["scenario_sha256"] = audit.digest(c)
        m.pop("manifest_sha256")
        m["manifest_sha256"] = audit.digest(m)
        self.assertIn("predeclared_partition_hash_rank_mismatch", {r["code"] for r in audit.check_manifest(m)})

    def test_complete_trace_counts_exact_time_and_feedback(self):
        with tempfile.TemporaryDirectory() as temp:
            p, case = Path(temp), case_fixture()
            write_arm(p, case, audit.BASELINE)
            result = audit.audit_arm(case, audit.BASELINE, p, "runner")
            self.assertEqual(result["issues"], [])
            self.assertEqual(result["computed_virtual_time_s"], 129.)
            self.assertEqual(result["measures"], 10)
            self.assertEqual(result["clear_feedbacks_checked"], 10)

    def test_result_count_and_error_hash_tamper_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            p, case = Path(temp), case_fixture()
            result = write_arm(p, case, audit.BASELINE)
            result.update(measures=0, error_field_sha256="different")
            (p / "result.json").write_text(json.dumps(result), encoding="utf8")
            codes = {r["code"] for r in audit.audit_arm(case, audit.BASELINE, p, "runner")["issues"]}
            self.assertTrue({"result_action_count_mismatch", "result_frozen_design_mismatch"} <= codes)

    def test_fixed_field_replayed_and_tampered_angle_rejected(self):
        case = case_fixture()
        case["sources"][0]["position"] = [0., 0.]
        position = (100., 0.)
        expected = round((180 + audit.fixed_error(case["error_field"], 1, position)) % 360, 2)
        action = {"line": 2, "path": "/measure", "request": {"channel": 1, "position": {"x": 100., "y": 0.}},
                  "response": {"measure_result": "direction", "svd_deg": expected, "virtual_time_s": 25.}}
        self.assertEqual(audit.check_feedback(case, [action])["issues"], [])
        action["response"]["svd_deg"] += .01
        self.assertIn("direction_contradicts_declared_fixed_error_field", {r["code"] for r in audit.check_feedback(case, [action])["issues"]})

    def test_clear_success_uses_actual_source_not_reported_success(self):
        case = case_fixture()
        action = {"line": 2, "path": "/clear", "request": {"channel": 1, "position": {"x": 0., "y": 0.}},
                  "response": {"clear_result": "success", "virtual_time_s": 3.}}
        result = audit.check_feedback(case, [action])
        self.assertEqual(result["truth_cleared_channels"], [])
        self.assertIn("clear_feedback_contradicts_local_truth", {r["code"] for r in result["issues"]})

    def test_confirmation_invalid_results_are_never_opened(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            manifest = manifest_fixture()
            (p / "manifest.json").write_text(json.dumps(manifest), encoding="utf8")
            for case in manifest["scenarios"]:
                for arm in audit.LIMITS:
                    directory = p / "runs" / case["pool"] / case["case_id"] / arm
                    if case["partition"] == "development":
                        write_arm(directory, case, arm)
                    else:
                        directory.mkdir(parents=True)
                        (directory / "result.json").write_text("SEALED INVALID JSON; MUST NOT OPEN", encoding="utf8")
            with patch.object(audit, "check_freeze", return_value={"issues": [], "runner_sha256": "runner"}):
                result = audit.audit_stage(p, p / "audit")
            self.assertEqual(result["issue_count"], 0)
            self.assertEqual(result["audited_development_runs"], 16)
            self.assertEqual(result["confirmation_result_paths_opened"], 0)

    def test_prefix_missing_arms_separate_from_failed_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            (p / "manifest.json").write_text(json.dumps(manifest_fixture()), encoding="utf8")
            with patch.object(audit, "check_freeze", return_value={"issues": [], "runner_sha256": "runner"}):
                result = audit.audit_stage(p, p / "audit", complete_only=True)
            self.assertEqual(result["issue_count"], 0)
            self.assertEqual(result["pending_case_count"], 4)
            self.assertEqual(result["evaluation_complete_runs"], 0)
            self.assertEqual(result["status"], "no_complete_development_cases")

    def test_confirmation_requires_frozen_selection_before_result_access(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            (p / "manifest.json").write_text(json.dumps(manifest_fixture()), encoding="utf8")
            with patch.object(audit, "check_freeze", return_value={"issues": [], "runner_sha256": "runner"}):
                with self.assertRaisesRegex(ValueError, "previously frozen selection"):
                    audit.audit_stage(p, p / "audit", partition="confirmation")

    def test_confirmation_only_frozen_two_arms_opened(self):
        with tempfile.TemporaryDirectory() as temp:
            p, manifest = Path(temp), manifest_fixture()
            (p / "manifest.json").write_text(json.dumps(manifest), encoding="utf8")
            (p / "development").mkdir()
            (p / "development/results.json").write_text("[]", encoding="utf8")
            choice = {"manifest_sha256": manifest["manifest_sha256"], "frozen_before_confirmation_review": True,
                      "selection_basis": "development_only", "development_file_sha256": audit.sha(p / "development/results.json"),
                      "selected_variants": {"3": "joint_triangular_l1", "4": "joint_triangular_l1"}}
            choice["selection_sha256"] = audit.digest(choice)
            (p / "selection.json").write_text(json.dumps(choice), encoding="utf8")
            for case in manifest["scenarios"]:
                for arm in audit.LIMITS:
                    directory = p / "runs" / case["pool"] / case["case_id"] / arm
                    if case["partition"] == "confirmation" and arm in (audit.BASELINE, "joint_triangular_l1"):
                        write_arm(directory, case, arm)
                    else:
                        directory.mkdir(parents=True)
                        (directory / "result.json").write_text("UNAUTHORIZED RESULT; MUST NOT OPEN", encoding="utf8")
            with patch.object(audit, "check_freeze", return_value={"issues": [], "runner_sha256": "runner"}):
                result = audit.audit_stage(p, p / "audit", partition="confirmation", selection=p / "selection.json")
            self.assertEqual(result["issue_count"], 0)
            self.assertEqual(result["audited_selected_runs"], 2)
            self.assertEqual(result["unselected_confirmation_result_paths_opened"], 0)


if __name__ == "__main__":
    unittest.main()
