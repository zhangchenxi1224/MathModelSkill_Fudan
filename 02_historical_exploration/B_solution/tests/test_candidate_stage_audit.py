import copy
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts/audit_local_candidate_stages.py"
SPEC = importlib.util.spec_from_file_location("later_stage_audit", PATH)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def manifests():
    cases = []
    for i in range(5):
        c = {"case_id": f"case{i}", "problem": 3, "pool": "broad", "composition_model": "broad_joint", "seed": i,
             "n": 10, "n_directed": 0, "partition": "development", "error_field": {"seed": i, "mode": "correlated", "correlation_length_m": 150},
             "sources": [{"channel": channel, "position": [100.+i, 0.], "radius": 1200., "direction_deg": None} for channel in range(1, 11)]}
        c["scenario_sha256"] = audit.digest(c)
        cases.append(c)
    prior = {"stage": 1, "master_seed": 100, "scenarios": copy.deepcopy(cases)}
    prior["manifest_sha256"] = audit.digest(prior)
    for i, case in enumerate(cases):
        case.update(seed=1000+i, stage=2)
        for source in case["sources"]:
            source["position"][0] += 100.
    ordered = sorted(cases, key=lambda c: audit.shared.rank_seed(200, "confirmation_allocation", c["case_id"]))
    ordered[0]["partition"] = "confirmation"
    for case in cases:
        case["case_id"] = "stage2-" + case["case_id"]
        case.pop("scenario_sha256")
        case["scenario_sha256"] = audit.digest(case)
    definitions = {str(p): [{"variant": mode, "solver_class": mode, "solver_config": audit.shared.expected_config(p, "joint_triangular_l1"),
                           "nosignal_config": {} if mode == "nosignal" else None} for mode in ("normal", "nosignal")] for p in (3, 4)}
    m = {"stage": 2, "master_seed": 200, "baseline": "normal", "frozen_core_sha256": audit.shared.CORE_SHA,
         "official_formal_authorized": False, "distinct_scenarios": 5, "planned_strategy_runs": 10, "pool_sizes": {"broad": 5},
         "arm_definitions": definitions, "selected_local_limits": {"3": 1, "4": 1}, "scenarios": cases,
         "previous_manifests": [{"stage": 1, "manifest_sha256": prior["manifest_sha256"], "master_seed": 100}]}
    m["manifest_sha256"] = audit.digest(m)
    return prior, m


class CandidateStageAuditTests(unittest.TestCase):
    def test_baseline_fallback_does_not_claim_branch_probability_schema(self):
        events = [{"event": "start", "nosignal_config": {}}, {"event": "choose_measurement", "selection": {
            "baseline_fallback_reason": "no_compatible_grid_states", "selection_changed_from_baseline": False,
            "evaluations": [{"point": [3, 4], "score_s": 15.}]}}]
        result = {"nosignal_choices": 1, "nosignal_changed_choices": 0, "nosignal_baseline_fallbacks": 1, "nosignal_convex_mixed_choices": 0}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "decisions.jsonl.gz"
            with gzip.open(path, "wt", encoding="utf8") as f:
                f.write("\n".join(json.dumps(e) for e in events))
            self.assertEqual(audit.check_nosignal_log(path, result, {"nosignal_config": {}})["issues"], [])

    def test_stage_prefix_does_not_change_preallocated_partition(self):
        previous, current = manifests()
        self.assertEqual(audit.check_design(current, [previous]), [])

    def test_config_change_outside_single_factor_detected(self):
        previous, current = manifests()
        current["arm_definitions"]["4"][1]["solver_config"]["clear_radius"] = 30
        codes = {r["code"] for r in audit.check_design(current, [previous])}
        self.assertIn("single_factor_config_mismatch", codes)

    def test_overlap_with_earlier_world_rejected(self):
        previous, current = manifests()
        current["scenarios"][0]["seed"] = 0
        self.assertIn("fresh_world_or_seed_overlaps_earlier_stage", {r["code"] for r in audit.check_design(current, [previous])})

    def test_branch_score_and_next_observable_action(self):
        evaluation = {"point": [3., 4.], "score_s": 58.5, "branch_probabilities": {"direction": .5, "near": 0., "no_signal": .5},
                      "cost_terms_s": {"movement": 2., "measurement": 5., "direction_branch_proxy": 3., "near_branch_clear": 5., "no_signal_immediate_fallback": 100.}}
        events = [{"event": "start", "nosignal_config": {}},
                  {"event": "choose_measurement", "target_channel": 7, "selection": {"evaluations": [evaluation], "selected": evaluation, "selection_changed_from_baseline": True}},
                  {"event": "measure", "knowledge": {"channel": 7}, "position": [3., 4.]}]
        result = {"nosignal_choices": 1, "nosignal_changed_choices": 1, "nosignal_baseline_fallbacks": 0, "nosignal_convex_mixed_choices": 0}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "decisions.jsonl.gz"
            with gzip.open(path, "wt", encoding="utf8") as f:
                f.write("\n".join(json.dumps(e) for e in events))
            self.assertEqual(audit.check_nosignal_log(path, result, {"nosignal_config": {}})["issues"], [])
            events[2]["position"] = [30., 40.]
            evaluation["score_s"] = 3.
            with gzip.open(path, "wt", encoding="utf8") as f:
                f.write("\n".join(json.dumps(e) for e in events))
            codes = {r["code"] for r in audit.check_nosignal_log(path, result, {"nosignal_config": {}})["issues"]}
            self.assertTrue({"nosignal_reported_score_mismatch", "nosignal_selected_point_not_actual_next_measure"} <= codes)
