import copy
import importlib.util
from pathlib import Path
import random
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts/audit_validation_design.py"
SPEC = importlib.util.spec_from_file_location("validation_design_audit", PATH)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def fixture():
    sizing = {"target_relative_effect": .05, "planned_total": 60, "planned_per_arm": 30, "prior_complete_cases": 60,
              "prior_mean_s": 1000., "prior_sd_s": 100., "target_delta_s": 50., "required_per_arm_normal_approx": 63,
              "approximate_power_at_target": .49, "capped_below_requested_power": True, "minimum_detectable_relative_effect": .07}
    precision = {"formal_authorized": False, "made_before_any_round2_case": True, "made_before_stage1_performance_review": True,
                 "created_utc": "2026-09-11T00:00:00+00:00", "relative_effect": .05, "sample_sizes": {"3": copy.deepcopy(sizing), "4": copy.deepcopy(sizing)}}
    cases = []
    for problem in (3, 4):
        rng = random.Random(123 + problem)
        for block in range(15):
            arms = ["baseline", "baseline", "candidate", "candidate"]
            rng.shuffle(arms)
            for offset, arm in enumerate(arms):
                sequence = block * 4 + offset + 1
                cases.append({"case_id": f"p{problem}-{sequence}", "problem": problem, "arm": arm, "protocol": arm,
                              "split": "validation", "round": 2, "environment": "official_practice", "pilot": False,
                              "sequence_within_problem": sequence, "block": block, "allocation_seed": 123})
    cases.sort(key=lambda c: (c["sequence_within_problem"], c["problem"]))
    for i, case in enumerate(cases):
        case["sequence"] = i
    return {"cases": cases, "sample_sizes": copy.deepcopy(precision["sample_sizes"]), "formal_authorized": False,
            "created_utc": "2026-09-11T01:00:00+00:00"}, precision


class ValidationPlanningTests(unittest.TestCase):
    def test_frozen_120_case_balanced_plan(self):
        plan, precision = fixture()
        self.assertEqual(audit.check_plan(plan, precision)["issues"], [])

    def test_effect_change_or_rerandomization_detected(self):
        plan, precision = fixture()
        plan["sample_sizes"]["3"]["target_relative_effect"] = .10
        plan["cases"][0]["arm"] = "candidate" if plan["cases"][0]["arm"] == "baseline" else "baseline"
        codes = {r["code"] for r in audit.check_plan(plan, precision)["issues"]}
        self.assertTrue({"frozen_precision_parameter_mismatch", "fixed_block_randomization_does_not_reproduce"} <= codes)
