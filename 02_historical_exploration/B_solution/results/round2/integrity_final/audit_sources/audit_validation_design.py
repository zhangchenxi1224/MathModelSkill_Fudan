"""Read-only consistency check of fresh independent-arm official planning.

No HTTP/GUI, no candidate outcomes, no outcome-dependent size changes.
"""
import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
from pathlib import Path
import random


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_plan(plan, precision):
    issues = []
    def fail(code, detail=""):
        issues.append({"code": code, "detail": detail})
    if precision.get("formal_authorized") is not False or plan.get("formal_authorized") is not False:
        fail("plan_not_practice_only")
    if not precision.get("made_before_any_round2_case") or not precision.get("made_before_stage1_performance_review"):
        fail("precision_decision_not_declared_before_outcomes")
    try:
        if dt.datetime.fromisoformat(precision["created_utc"]) > dt.datetime.fromisoformat(plan["created_utc"]):
            fail("precision_decision_after_plan")
    except (KeyError, ValueError, TypeError):
        fail("missing_or_invalid_design_timestamps")
    cases = plan.get("cases", [])
    if len({c.get("case_id") for c in cases}) != len(cases):
        fail("duplicate_planned_case_id")
    if [c.get("sequence") for c in cases] != list(range(len(cases))):
        fail("global_allocation_sequence_mismatch")
    for case in cases:
        if case.get("problem") not in (3, 4) or case.get("arm") not in ("baseline", "candidate"):
            fail("illegal_problem_or_arm", case.get("case_id"))
        if (case.get("protocol") != case.get("arm") or case.get("split") != "validation" or case.get("round") != 2
                or case.get("environment") != "official_practice" or case.get("pilot") is not False):
            fail("case_not_fresh_independent_validation", case.get("case_id"))
    groups = []
    for problem in (3, 4):
        actual = sorted((c for c in cases if c.get("problem") == problem), key=lambda c: c.get("sequence_within_problem", -1))
        frozen = precision.get("sample_sizes", {}).get(str(problem), {})
        sizing = plan.get("sample_sizes", {}).get(str(problem), {})
        for field in ("target_relative_effect", "planned_total", "planned_per_arm", "prior_complete_cases", "prior_mean_s", "prior_sd_s",
                      "target_delta_s", "required_per_arm_normal_approx", "approximate_power_at_target", "capped_below_requested_power", "minimum_detectable_relative_effect"):
            if field not in frozen or sizing.get(field) != frozen[field]:
                fail("frozen_precision_parameter_mismatch", f"P{problem}:{field}")
        if sizing.get("target_relative_effect") != precision.get("relative_effect"):
            fail("relative_effect_does_not_match_prior_decision", problem)
        counts = Counter(c.get("arm") for c in actual)
        if (len(actual) != frozen.get("planned_total") or counts["baseline"] != frozen.get("planned_per_arm")
                or counts["candidate"] != frozen.get("planned_per_arm") or not 40 <= len(actual) <= 60):
            fail("independent_arm_planned_counts_mismatch", problem)
        if [c.get("sequence_within_problem") for c in actual] != list(range(1, len(actual) + 1)):
            fail("within_problem_sequence_mismatch", problem)
        seeds = {c.get("allocation_seed") for c in actual}
        if len(seeds) != 1 or not isinstance(next(iter(seeds), None), int):
            fail("missing_or_mixed_allocation_seed", problem)
        else:
            rng = random.Random(next(iter(seeds)) + problem)
            expected = []
            for block in range(len(actual) // 4):
                arms = ["baseline", "baseline", "candidate", "candidate"]
                rng.shuffle(arms)
                expected += [(block, arm) for arm in arms]
            if [(c.get("block"), c.get("arm")) for c in actual] != expected:
                fail("fixed_block_randomization_does_not_reproduce", problem)
        groups.append({"problem": problem, "planned_cases": len(actual), "arms": dict(counts),
                       "target_relative_effect": sizing.get("target_relative_effect"),
                       "approximate_power_at_target": sizing.get("approximate_power_at_target"),
                       "power_is_planning_proxy_not_guarantee": True})
    return {"issues": issues, "groups": groups, "planned_cases": len(cases),
            "comparison": "fresh independent arms by problem; not same-case pairs; planned cases are not actual attempts"}


def run(plan_path, precision_path, output):
    plan = json.loads(plan_path.read_text(encoding="utf8"))
    precision = json.loads(precision_path.read_text(encoding="utf8"))
    result = check_plan(plan, precision)
    candidate_path = Path(plan.get("candidate_file", ""))
    if not candidate_path.is_file() or sha(candidate_path) != plan.get("candidate_sha256"):
        result["issues"].append({"code": "candidate_file_hash_mismatch", "detail": str(candidate_path)})
    else:
        candidate = json.loads(candidate_path.read_text(encoding="utf8"))
        for case in plan["cases"]:
            if case["arm"] == "candidate" and case.get("policy_spec") != candidate.get("problems", {}).get(str(case["problem"])):
                result["issues"].append({"code": "candidate_policy_assignment_mismatch", "detail": case["case_id"]})
    freeze_path = plan_path.parent / "validation_freeze.json"
    if freeze_path.is_file():
        freeze = json.loads(freeze_path.read_text(encoding="utf8"))
        if freeze.get("plan_sha256") != sha(plan_path) or freeze.get("case_count") != len(plan["cases"]):
            result["issues"].append({"code": "runtime_freeze_plan_reference_mismatch", "detail": ""})
    result.update(status="issues_found" if result["issues"] else "passed_frozen_planning_consistency",
                  issue_count=len(result["issues"]), plan_sha256=sha(plan_path), precision_sha256=sha(precision_path),
                  script_sha256=sha(Path(__file__)), validation_freeze_present=freeze_path.is_file(),
                  scope="Planning only; no official actions or result reads. Execution integrity requires separate audit_round_integrity.py.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--precision", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    r = run(a.plan, a.precision, a.output)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    raise SystemExit(int(bool(r["issues"])))
