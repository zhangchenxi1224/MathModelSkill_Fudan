"""Read-only sample-size planning from the previous official P4 validation.

Uses only historical round2 data. Never creates assignments, opens a simulator,
contacts an endpoint, executes a policy, or reads new-round outcomes.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def planning(mean, old_sd, new_sd, n, relative_effect=.05):
    if min(mean, old_sd, new_sd) <= 0 or n < 2 or relative_effect <= 0:
        raise ValueError("Require positive means, SDs, target and at least two cases per arm")
    normal = statistics.NormalDist()
    za, zb = normal.inv_cdf(.975), normal.inv_cdf(.8)
    se = math.sqrt((old_sd**2+new_sd**2)/n)
    ncp = relative_effect*mean/se
    return {"per_arm": n, "total": 2*n,
            "mde_s_normal80": (za+zb)*se,
            "mde_relative_normal80": (za+zb)*se/mean,
            "expected_ci95_half_width_s_normal": za*se,
            "target_relative_effect": relative_effect,
            "approximate_power_at_target": 1-normal.cdf(za-ncp)+normal.cdf(-za-ncp),
            "required_per_arm_normal80": math.ceil((za+zb)**2*(old_sd**2+new_sd**2)/(relative_effect*mean)**2)}


def build():
    paths = {"historical_csv": ROOT/"results/pipeline_summary/official_validation_cases.csv",
             "historical_summary": ROOT/"results/round2/validation_summary.json",
             "historical_plan": ROOT/"results/round2/plan.json",
             "current_candidate": ROOT/"results/round2/candidate.json",
             "refined_source_freeze": ROOT/"results/p4_refinement/source_freeze.json",
             "refined_development_selection": ROOT/"results/p4_refinement/selection.json"}
    summary = read(paths["historical_summary"])
    candidate = read(paths["current_candidate"])
    plan = read(paths["historical_plan"])
    with paths["historical_csv"].open(encoding="utf-8-sig", newline="") as stream:
        rows = [r for r in csv.DictReader(stream) if r["problem"] == "4"]
    if len(rows) != 60 or len({r["case_id"] for r in rows}) != 60:
        raise ValueError("Expected all 60 distinct historical P4 cases")
    arms, audits = {}, []
    for arm in ("baseline", "candidate"):
        group = [r for r in rows if r["protocol"] == arm]
        if len(group) != 30 or any(r["evaluation_complete"] != "True" for r in group):
            raise ValueError("Historical expected 30 completed cases per arm no longer holds")
        values = [float(r["total_virtual_time_s"]) for r in group]
        arms[arm] = {"meaning_this_round": "current planning prior" if arm == "candidate" else "historical L5 background only",
                     "n": len(values), "mean_s": statistics.fmean(values), "sample_sd_s": statistics.stdev(values),
                     "median_s": statistics.median(values), "min_s": min(values), "max_s": max(values),
                     "mean_N": statistics.fmean(int(r["N"]) for r in group),
                     "mean_Ndir": statistics.fmean(int(r["Ndir"]) for r in group),
                     "N16_cases": sum(int(r["N"]) == 16 for r in group),
                     "cases": [{"case_id": r["case_id"], "total_virtual_time_s": float(r["total_virtual_time_s"])} for r in group]}
        prior = summary["problems"]["4"]["arms"][arm]
        if abs(arms[arm]["mean_s"]-prior["completion_time_mean_s"]) > 1e-8 or prior["verified_full_clear"] != 30:
            raise ValueError("CSV and frozen summary disagree")
        for row in group:
            folder = ROOT/"results/round2/cases"/row["case_id"]
            audit_path, assignment_path = folder/"post_exit_audit.json", folder/"assignment.json"
            audit, assignment = read(audit_path), read(assignment_path)
            if (audit["case_id"] != row["case_id"] or audit["protocol"] != arm or audit["problem"] != 4
                    or audit["status"] != "complete" or audit["total_virtual_time_s"] != float(row["total_virtual_time_s"])
                    or audit["source_total_post_exit"] != int(row["N"])
                    or audit["directional_total_post_exit"] != int(row["Ndir"])
                    or audit["cleared"] != int(row["N"])
                    or assignment["arm"] != arm or assignment["problem"] != 4):
                raise ValueError("Historical public post-exit audit or assignment mismatch")
            if arm == "candidate" and assignment["policy_spec"] != candidate["problems"]["4"]:
                raise ValueError("Historical current policy mismatch")
            audits.append({"case_id": row["case_id"], "post_exit_audit_sha256": sha(audit_path),
                           "assignment_sha256": sha(assignment_path)})
    current = arms["candidate"]
    mean, sd = current["mean_s"], current["sample_sd_s"]
    scenarios = [("both_equal_historical_current_sd", sd, sd),
                 ("new_sd_1_25_times_current", sd, 1.25*sd),
                 ("new_sd_1_5_times_current", sd, 1.5*sd),
                 ("both_sd_1_5_times_historical_current", 1.5*sd, 1.5*sd),
                 ("new_sd_equals_historical_L5_sd_sensitivity_only", sd, arms["baseline"]["sample_sd_s"])]
    return {"version": "p4-refined-official-precision-prior-v1", "read_scope": "historical official round2 P4 only; no new-round outcomes",
            "sources": {k: {"path": str(p.relative_to(ROOT)), "sha256": sha(p)} for k, p in paths.items()},
            "script_sha256": sha(Path(__file__)), "historical_groups": arms, "public_audits_checked": audits,
            "current_policy_spec": candidate["problems"]["4"],
            "historical_old_L5_based_plan": plan["sample_sizes"]["4"],
            "recommendation": {"problem": 4, "current_cases": 30, "combined_cover_cases": 30, "analysis_total": 60,
                               "separate_engineering_pilots": 2, "pilot_arms": ["current", "combined_cover"],
                               "block_size": 4, "blocks": 15, "allocation": "2 current + 2 combined_cover per block, frozen random order",
                               "sampling_unit": "fresh independent official practice case", "same_case_pairing": False,
                               "fixed_sample_no_favorable_early_stop": True,
                               "sample_increase_based_on_pilot_or_interim_effect": False},
            "planning_assumptions": "Normal approximation for a difference of independent means, two-sided alpha .05, target power .80, previous current mean as effect scale. No guarantee under unknown new-arm variance, changing official composition, dependence or heavy tails.",
            "equal_variance_sample_size_table": [planning(mean, sd, sd, n) for n in (20, 30, 40, 50, 60)],
            "relative_effect_table_n30": [planning(mean, sd, sd, 30, target) for target in (.03, .05, .075, .10)],
            "variance_sensitivity_n30": [{"assumption": label, "current_sd_s": so, "new_sd_s": sn, **planning(mean, so, sn, 30)} for label, so, sn in scenarios],
            "zero_failures_30_case_two_sided_wilson95_upper": statistics.NormalDist().inv_cdf(.975)**2/(30+statistics.NormalDist().inv_cdf(.975)**2),
            "official_actions_executed": 0, "formal_actions_authorized": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT/"results/p4_refinement_diagnostics/official_design_precision.json")
    args = parser.parse_args()
    value = build()
    if args.output.resolve().is_relative_to(ROOT/"results/round2"):
        raise ValueError("Never overwrite historical official evidence")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "sha256": sha(args.output), "recommendation": value["recommendation"],
                      "n30_planning": value["equal_variance_sample_size_table"][1]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
