"""One-factor sensitivity on four preselected tune cases, never on test cases.

Runs the production default policy with one factor changed at a time. All
scenario JSON, request logs, decisions and outcomes are retained per run.
This script reports comparisons; it never changes SolverConfig defaults.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bsolver.experiments import code_digest, run_case  # noqa: E402
from bsolver.strategy import SolverConfig  # noqa: E402

CASE_IDS = (
    "local-p3-11300-random-min-deterministic",
    "local-p3-11301-random-max-correlated",
    "local-p4-11400-random-min-deterministic",
    "local-p4-11401-random-max-correlated",
)


def variants(problem, include_coverage=True):
    baseline = SolverConfig(problem=problem)
    values = {
        "default": baseline,
        "epsilon_1.02": replace(baseline, epsilon_deg=1.02),
        "epsilon_1.06": replace(baseline, epsilon_deg=1.06),
        "bin_2": replace(baseline, bin_width_deg=2),
        "bin_8": replace(baseline, bin_width_deg=8),
        "weight_1": replace(baseline, radius_weight=1),
        "weight_4": replace(baseline, radius_weight=4),
        "detour_0": replace(baseline, joint_detour_m=0),
        "detour_1600": replace(baseline, joint_detour_m=1600),
    }
    if problem == 4 and include_coverage:
        values.update({
            "triangular_990": replace(baseline, spacing=990),
            "square_650": replace(baseline, coverage="square", spacing=650),
            "square_700": replace(baseline, coverage="square", spacing=700),
        })
    return values


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def compact(result):
    return {
        "variant": result["sensitivity_variant"], "case_id": result["case_id"],
        "problem": result["problem"], "seed": result["seed"], "status": result["status"],
        "all_cleared": result["environment_summary"]["all_cleared"],
        "source_total": result["source_total"], "clear_successes": result["clear_successes"],
        "clear_fraction": result["clear_fraction"],
        "virtual_time_s": result["total_virtual_time_s"],
        "average_clear_time_s": result["average_clear_time_s"],
        "program_real_time_s": result["program_real_time_s"],
        "walk_distance_m": result["walk_distance_m"], "measures": result["measures"],
        "clear_attempts": result["clear_attempts"], "fallback_targets": result["fallback_targets"],
        "hull_violations": len(result["hull_invariant_violations"]),
        "timing_residual_s": result["timing_residual_s"],
        "epsilon_deg": result["config"]["epsilon_deg"],
        "bin_width_deg": result["config"]["bin_width_deg"],
        "radius_weight": result["config"]["radius_weight"],
        "joint_detour_m": result["config"]["joint_detour_m"],
        "coverage": result["config"]["coverage"], "spacing": result["config"]["spacing"],
        "code_sha256": result["code_sha256"], "error": result["error"],
    }


def summarize(results):
    by_key = {(r["problem"], r["case_id"], r["sensitivity_variant"]): r for r in results}
    summaries = []
    for problem in (3, 4):
        selected = [r for r in results if r["problem"] == problem]
        for name in dict.fromkeys(r["sensitivity_variant"] for r in selected):
            group = [r for r in selected if r["sensitivity_variant"] == name]
            baselines = [by_key[(problem, r["case_id"], "default")] for r in group]
            summaries.append({
                "problem": problem, "variant": name, "case_count": len(group),
                "complete_runs": sum(r["status"] == "complete" and
                                     r["environment_summary"]["all_cleared"] for r in group),
                "mean_virtual_time_s": statistics.mean(r["total_virtual_time_s"] for r in group),
                "mean_paired_delta_s": statistics.mean(r["total_virtual_time_s"] - b["total_virtual_time_s"]
                                                       for r, b in zip(group, baselines)),
                "mean_paired_delta_percent": statistics.mean(100 * (r["total_virtual_time_s"] /
                                                              b["total_virtual_time_s"] - 1)
                                                             for r, b in zip(group, baselines)),
                "mean_program_real_time_s": statistics.mean(r["program_real_time_s"] for r in group),
                "mean_measures": statistics.mean(r["measures"] for r in group),
                "mean_fallback_targets": statistics.mean(r["fallback_targets"] for r in group),
                "hull_violations": sum(len(r["hull_invariant_violations"]) for r in group),
            })
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "sensitivity")
    parser.add_argument("--skip-coverage", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cases = []
    provenance = []
    for identity in CASE_IDS:
        path = ROOT / "results" / "tune" / "nearest_safe" / identity / "case.json"
        raw = path.read_bytes()
        case = json.loads(raw)
        if case["case_id"] != identity or case["seed"] not in (11300, 11301, 11400, 11401):
            raise ValueError("unexpected tune case identity")
        cases.append(case)
        provenance.append({"case_id": identity, "source_file": str(path),
                           "sha256": hashlib.sha256(raw).hexdigest()})
    manifest = {
        "study": "one-factor sensitivity on preselected tune cases",
        "environment": "self_built", "uses_official_simulator": False,
        "test_results_used_to_choose_parameters": False,
        "defaults_changed_by_study": False,
        "case_provenance": provenance,
        "start_code_sha256": code_digest(),
        "variants": {str(p): {name: asdict(cfg) for name, cfg in variants(p, not args.skip_coverage).items()}
                     for p in (3, 4)},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    results = []
    started = time.monotonic()
    for case in cases:
        for name, config in variants(case["problem"], not args.skip_coverage).items():
            directory = output / name / case["case_id"]
            result = run_case(case, config, directory)
            result["sensitivity_variant"] = name
            result["split"] = "tune"
            (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            results.append(result)
            write_csv(output / "cases.csv", [compact(r) for r in results])
            (output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"completed": len(results), "variant": name, "seed": case["seed"],
                              "status": result["status"], "cleared": result["clear_successes"],
                              "total": result["source_total"],
                              "virtual_s": round(result["total_virtual_time_s"], 3),
                              "real_s": round(result["program_real_time_s"], 3)}), flush=True)
    summary = summarize(results)
    write_csv(output / "summary.csv", summary)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["elapsed_wall_s"] = time.monotonic() - started
    manifest["run_count"] = len(results)
    manifest["all_complete"] = all(r["status"] == "complete" and
                                    r["environment_summary"]["all_cleared"] for r in results)
    manifest["run_code_sha256_values"] = sorted(set(r["code_sha256"] for r in results))
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"finished": True, "run_count": len(results), "all_complete": manifest["all_complete"],
                      "elapsed_wall_s": round(manifest["elapsed_wall_s"], 3)}), flush=True)


if __name__ == "__main__":
    main()
