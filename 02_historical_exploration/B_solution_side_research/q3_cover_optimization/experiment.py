"""Local-only paired evaluation; no HTTP adapter is constructed without transport."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import time
import zipfile
from pathlib import Path

from coverage_opt import (BASE, ROOT, ORIGINAL_RING, ContractedCoverageSolver,
                          covering_radius, parent_source_hashes)
from bsolver.experiments import generate_case, code_digest
from bsolver.geometry import contains
from bsolver.protocol import RobotClient
from bsolver.simulator import LocalSimulator, Source
from bsolver.strategy import Solver, SolverConfig

OUT = ROOT / "results"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
                    encoding="utf-8")


def source_digest():
    h = hashlib.sha256()
    for p in sorted(ROOT.glob("*.py")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def old_cases(split):
    paths = sorted((BASE / "results" / split / "baseline").glob("*/case.json"))
    cases = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    return [c for c in cases if c["problem"] == 3]


def fresh_cases(split, count):
    start = 911300 if split == "development" else 921300
    cases = []
    for i in range(count):
        case = generate_case(start + i, problem=3, n=10 if i % 2 == 0 else 16,
                             layout=["random", "boundary", "clustered", "center_and_boundary"][i % 4],
                             radius_mode=["min", "max", "mixed"][(i // 2) % 3],
                             error_mode=["deterministic", "correlated", "extreme",
                                         "positive", "negative"][i % 5])
        # Exact constructed boundary sectors test the limiting holes, not only
        # uniformly distributed perimeter targets. They remain unseen by policy.
        if split != "development" and i in (0, 1, 10, 11):
            for j, source in enumerate(case["sources"]):
                if j < 6:
                    angle = (j + .5) * math.pi / 3
                    source["position"] = [1800 * math.cos(angle), 1800 * math.sin(angle)]
                source["radius"] = 1000.
            case["layout"] = "six_worst_boundary_sectors"
            case["radius_mode"] = "min"
            case["case_id"] += "-constructed-boundary"
        cases.append(case)
    return cases


def run_one(case, ring, variant, split):
    destination = OUT / split / variant / case["case_id"]
    destination.mkdir(parents=True, exist_ok=True)
    write_json(destination / "case.json", case)
    sources = [Source(s["channel"], tuple(s["position"]), s["radius"], s["direction_deg"])
               for s in case["sources"]]
    env = LocalSimulator(sources, robot_id="side-local", seed=case["seed"],
                         error_mode=case["error_mode"])
    client = RobotClient(robot_id="side-local", transport=env,
                         log_path=str(destination / "requests.jsonl"))
    kwargs = dict(config=SolverConfig(problem=3),
                  decision_log=str(destination / "decisions.jsonl"))
    solver = (Solver(client, **kwargs) if ring == ORIGINAL_RING else
              ContractedCoverageSolver(client, **kwargs, ring=ring))
    result = solver.run()
    # Environment truth is inspected only by the evaluator AFTER policy exit.
    truth = env.summary()
    by_channel = {s["channel"]: tuple(s["position"]) for s in case["sources"]}
    violations = []
    for event in solver.decisions:
        state = event.get("knowledge")
        if state and state["channel"] in by_channel:
            if not contains(state["hull"], by_channel[state["channel"]], tol=1e-4):
                violations.append({"sequence": event["sequence"], "channel": state["channel"]})
    count = len(sources)
    result.update(environment="self_built_side_research", variant=variant, split=split,
                  ring_radius_m=ring, case_id=case["case_id"], seed=case["seed"],
                  layout=case["layout"], radius_mode=case["radius_mode"], error_mode=case["error_mode"],
                  source_total=count, clear_fraction=result["clear_successes"] / count,
                  hull_invariant_violations=violations, environment_summary=truth,
                  parent_code_sha256=code_digest(), experiment_code_sha256=source_digest())
    write_json(destination / "result.json", result)
    print(json.dumps({"split": split, "variant": variant, "case": case["case_id"],
                      "status": result["status"], "cleared": result["clear_successes"],
                      "total": count, "virtual_s": round(result["total_virtual_time_s"], 3),
                      "violations": len(violations)}, ensure_ascii=False), flush=True)
    return result


def save_rows(rows, split):
    write_json(OUT / split / "results.json", rows)
    fields = ["split", "variant", "case_id", "seed", "layout", "radius_mode", "error_mode",
              "ring_radius_m", "status", "error", "source_total", "clear_successes", "clear_fraction",
              "total_virtual_time_s", "average_clear_time_s", "walk_distance_m", "measures",
              "switches", "clear_attempts", "fallback_targets", "program_real_time_s",
              "timing_residual_s"]
    with (OUT / split / "results.csv").open("w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def batches(cases, variants, split):
    rows = []
    for c in cases:
        for label, ring in variants.items():
            rows.append(run_one(c, ring, label, split))
            save_rows(rows, split)
    return rows


def percentile(values, p):
    x = sorted(values)
    t = (len(x) - 1) * p
    lo = int(t)
    return x[lo] + (x[min(lo + 1, len(x) - 1)] - x[lo]) * (t - lo)


def summarize(rows):
    groups = {}
    for r in rows:
        groups.setdefault(r["variant"], []).append(r)
    out = {}
    for label, group in groups.items():
        times = [r["total_virtual_time_s"] for r in group]
        out[label] = {
            "runs": len(group), "complete": sum(r["status"] == "complete" and r["clear_fraction"] == 1 for r in group),
            "hull_violations": sum(len(r["hull_invariant_violations"]) for r in group),
            "mean_total_s": statistics.mean(times), "median_total_s": statistics.median(times),
            "p95_total_s": percentile(times, .95), "max_total_s": max(times),
            "mean_average_clear_s": statistics.mean(r["average_clear_time_s"] for r in group),
            "mean_walk_m": statistics.mean(r["walk_distance_m"] for r in group),
            "mean_measures": statistics.mean(r["measures"] for r in group),
            "mean_switches": statistics.mean(r["switches"] for r in group),
            "mean_clear_attempts": statistics.mean(r["clear_attempts"] for r in group),
            "mean_fallbacks": statistics.mean(r["fallback_targets"] for r in group),
            "max_real_s": max(r["program_real_time_s"] for r in group),
        }
    return out


def run_development():
    variants = {"original": ORIGINAL_RING, "ring1125": 1125., "ring1200": 1200., "ring1300": 1300.}
    cases = old_cases("tune") + fresh_cases("development", 8)
    write_json(OUT / "development_plan.json", {
        "created_unix_s": time.time(), "variants": variants, "case_ids": [c["case_id"] for c in cases],
        "selection_rule": "minimum mean total virtual time, requiring all complete and zero hull violations",
        "parent_source_hashes": parent_source_hashes(), "experiment_code_sha256": source_digest(),
    })
    with zipfile.ZipFile(OUT / "parent_source_snapshot.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted((BASE / "src" / "bsolver").glob("*.py")):
            z.write(p, p.name)
    rows = batches(cases, variants, "development")
    summary = summarize(rows)
    eligible = [k for k, v in summary.items() if v["complete"] == v["runs"] and v["hull_violations"] == 0]
    selected = min(eligible, key=lambda k: summary[k]["mean_total_s"])
    write_json(OUT / "frozen_selection.json", {
        "created_unix_s": time.time(), "variant": selected, "ring_radius_m": variants[selected],
        "selection_rule": "predeclared development mean total virtual time among complete variants",
        "development_summary": summary, "test_has_not_run": True,
        "parent_source_hashes": parent_source_hashes(),
    })
    print(json.dumps({"frozen_selection": selected, "summary": summary}, ensure_ascii=False), flush=True)


def run_test():
    choice = json.loads((OUT / "frozen_selection.json").read_text(encoding="utf-8"))
    if choice["parent_source_hashes"] != parent_source_hashes():
        raise RuntimeError("Parent source changed; refusing to mix source versions")
    variants = {"original": ORIGINAL_RING, "selected": choice["ring_radius_m"]}
    cases = old_cases("test") + old_cases("stress") + fresh_cases("holdout", 20)
    write_json(OUT / "test_plan.json", {"created_unix_s": time.time(), "frozen_selection": choice,
                                       "case_ids": [c["case_id"] for c in cases], "variants": variants})
    rows = batches(cases, variants, "test")
    write_json(OUT / "test_summary.json", summarize(rows))


def report():
    choice = json.loads((OUT / "frozen_selection.json").read_text(encoding="utf-8"))
    data = json.loads((OUT / "test" / "results.json").read_text(encoding="utf-8"))
    groups = {label: {r["case_id"]: r for r in data if r["variant"] == label}
              for label in ("original", "selected")}
    paired = []
    for case, a in groups["original"].items():
        b = groups["selected"][case]
        paired.append({"case_id": case, "seed": a["seed"], "layout": a["layout"],
                       "old_total_s": a["total_virtual_time_s"], "new_total_s": b["total_virtual_time_s"],
                       "saved_s": a["total_virtual_time_s"] - b["total_virtual_time_s"],
                       "relative_saving": 1 - b["total_virtual_time_s"] / a["total_virtual_time_s"],
                       "walk_saved_s": (a["walk_distance_m"] - b["walk_distance_m"]) / 5,
                       "measure_saved_s": 5 * (a["measures"] - b["measures"]),
                       "switch_saved_s": a["switches"] - b["switches"],
                       "clear_saved_s": 3 * (a["clear_attempts"] - b["clear_attempts"]),
                       "old_fallbacks": a["fallback_targets"], "new_fallbacks": b["fallback_targets"],
                       "both_complete": a["status"] == b["status"] == "complete"})
    write_json(OUT / "paired_test.json", paired)
    with (OUT / "paired_test.csv").open("w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(paired[0]))
        w.writeheader(); w.writerows(paired)
    summary = summarize(data)
    summary["paired"] = {
        "wins": sum(p["saved_s"] > 1e-6 for p in paired),
        "losses": sum(p["saved_s"] < -1e-6 for p in paired),
        "ties": sum(abs(p["saved_s"]) <= 1e-6 for p in paired),
        "mean_total_reduction_ratio": 1 - summary["selected"]["mean_total_s"] / summary["original"]["mean_total_s"],
        "worst_regression": min(paired, key=lambda p: p["saved_s"]),
        "best_improvement": max(paired, key=lambda p: p["saved_s"]),
    }
    summary["fresh_holdout_only"] = summarize([r for r in data if r["seed"] >= 921300])
    summary["parent_source_unchanged"] = choice["parent_source_hashes"] == parent_source_hashes()
    write_json(OUT / "summary.json", summary)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Circle
        import numpy as np
        fig, axes = plt.subplots(1, 2, figsize=(11, 5), constrained_layout=True)
        for ax, ring, title in zip(axes, [ORIGINAL_RING, choice["ring_radius_m"]], ["Original", "Selected"]):
            points = [(0., 0.)] + [(ring * math.cos(k * math.pi / 3), ring * math.sin(k * math.pi / 3)) for k in range(6)]
            ax.add_patch(Circle((0, 0), 1800, fill=False, color="black", linewidth=1.5))
            for p in points:
                ax.add_patch(Circle(p, 1000, color="tab:blue", alpha=.10))
                ax.plot(*p, ".", color="tab:blue")
            ax.set(xlim=(-2700,2700), ylim=(-2700,2700), aspect="equal", xlabel="x / m", ylabel="y / m",
                   title=f"{title}: ring {ring:.2f} m; cover {covering_radius(ring):.2f} m")
        fig.savefig(OUT / "coverage_comparison.png", dpi=170); plt.close(fig)
        fig, ax = plt.subplots(figsize=(11, 4), constrained_layout=True)
        ax.bar(range(len(paired)), [p["saved_s"] for p in paired],
               color=["tab:green" if p["saved_s"] >= 0 else "tab:red" for p in paired])
        ax.axhline(0, color="black", linewidth=.8)
        ax.set(xlabel="Paired test case index", ylabel="Total virtual time saved / s",
               title=f"Selected ring {choice['ring_radius_m']:.0f} m vs original; negative means worse")
        fig.savefig(OUT / "paired_results.png", dpi=170); plt.close(fig)
        fig, ax = plt.subplots(figsize=(7, 4), constrained_layout=True)
        x = np.linspace(1100, 1750, 500)
        ax.plot(x, [covering_radius(float(r)) for r in x], label="Exact worst coverage distance")
        ax.axhline(1000, color="tab:red", linestyle="--", label="Guaranteed reception radius")
        ax.scatter([ORIGINAL_RING, choice["ring_radius_m"]],
                   [covering_radius(ORIGINAL_RING), covering_radius(choice["ring_radius_m"])])
        ax.set(xlabel="Ring radius / m", ylabel="Worst coverage distance / m")
        ax.legend(); fig.savefig(OUT / "radius_tradeoff.png", dpi=170); plt.close(fig)
    except ImportError:
        pass
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["development", "test", "report"])
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    {"development": run_development, "test": run_test, "report": report}[args.action]()
