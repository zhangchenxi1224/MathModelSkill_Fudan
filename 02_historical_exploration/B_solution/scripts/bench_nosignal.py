"""Small deterministic diagnostic, not the staged development/confirmation test."""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from bsolver.geometry import contains  # noqa: E402
from bsolver.nosignal_sensing import NoSignalConfig, NoSignalSolver, choose_measurement_nosignal  # noqa: E402
from bsolver.protocol import RobotClient  # noqa: E402
from bsolver.sensing import choose_measurement  # noqa: E402
from bsolver.simulator import LocalSimulator, Source, generate_sources  # noqa: E402
from bsolver.strategy import Solver, SolverConfig  # noqa: E402


class SnapshotSolver(Solver):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.snapshots = []

    def _record(self, event, **data):
        if event == "choose_measurement":
            self.snapshots.append((copy.deepcopy(self.channels[data["target_channel"]]),
                                   self.client.position))
        super()._record(event, **data)


def percentile(values, q):
    return sorted(values)[min(len(values)-1, math.ceil(q*len(values))-1)] if values else None


def run_one(sources, problem, seed, limit, variant):
    env = LocalSimulator(sources, seed=seed, error_mode="extreme", enforce_case_size=True)
    client = RobotClient(transport=lambda path, body: env(path, body), retry_delay=0)
    config = SolverConfig(problem=problem, local_measure_limit=limit)
    solver = (SnapshotSolver(client, config) if variant == "normal" else
              NoSignalSolver(client, config, nosignal_config=NoSignalConfig()))
    result = solver.run()
    truth = {source.channel: source for source in sources}
    violations = 0
    for event in solver.decisions:
        knowledge = event.get("knowledge")
        if knowledge and knowledge["channel"] in truth:
            violations += not contains(knowledge["hull"], truth[knowledge["channel"]].position, tol=1e-5)
    return solver, {"status": result["status"], "error": result["error"],
        "all_cleared": env.summary()["all_cleared"], "cleared": result["clear_successes"],
        "virtual_time_s": result["total_virtual_time_s"], "real_time_s": result["program_real_time_s"],
        "hull_violations": violations, "measures": result["measures"],
        "fallback_targets": result["fallback_targets"],
        "nosignal_choices": result.get("nosignal_choices"),
        "nosignal_changed_choices": result.get("nosignal_changed_choices")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="results/nosignal_bench")
    parser.add_argument("--local-limit", type=int, default=2,
                        help="diagnostic only; does not select the staged experiment's L")
    parser.add_argument("--max-snapshots", type=int, default=48)
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args(argv)
    if args.local_limit < 0 or args.max_snapshots < 1 or args.repeats < 1:
        parser.error("limit must be nonnegative, snapshots/repeats positive")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    config = NoSignalConfig()
    specs = [(3, 631301, "random", 0.), (3, 631302, "random", 0.),
             (4, 631401, "random", .5), (4, 631402, "random", 1.),
             (4, 631403, "boundary_outward", 1.), (4, 631404, "boundary_tangent", 1.)]
    manifest = {"purpose": "six-case diagnostic, not stage1 selection or stage2 confirmation",
                "local_limit": args.local_limit, "nosignal_config": asdict(config),
                "case_specs": specs, "error_field": "extreme",
                "snapshot_policy": "baseline trajectory; same evidence for both selectors",
                "repeats": args.repeats, "max_snapshots": args.max_snapshots,
                "module_sha256": hashlib.sha256((Path(__file__).resolve().parents[1]/
                                      "src/bsolver/nosignal_sensing.py").read_bytes()).hexdigest()}
    (output/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
    cases, snapshots = [], []
    for problem, seed, layout, fraction in specs:
        if layout == "random":
            sources = generate_sources(seed, count=10, directional_fraction=fraction)
        else:
            sources = []
            for index in range(10):
                theta = 2*math.pi*index/10+.041
                direction = math.degrees(theta)+(90. if layout == "boundary_tangent" else 0.)
                sources.append(Source(index+1, (1800.*math.cos(theta), 1800.*math.sin(theta)),
                                      1000., direction))
        baseline, normal = run_one(sources, problem, seed, args.local_limit, "normal")
        _, aware = run_one(sources, problem, seed, args.local_limit, "nosignal")
        cases.append({"problem": problem, "seed": seed, "layout": layout,
                      "normal": normal, "nosignal": aware,
                      "virtual_delta_s": aware["virtual_time_s"]-normal["virtual_time_s"]})
        snapshots.extend((problem, seed, layout, knowledge, point)
                         for knowledge, point in baseline.snapshots)
    # Evenly select from the whole chronological pool, including the last case.
    if len(snapshots) > args.max_snapshots:
        if args.max_snapshots == 1:
            snapshots = snapshots[:1]
        else:
            snapshots = [snapshots[(i*(len(snapshots)-1))//(args.max_snapshots-1)]
                         for i in range(args.max_snapshots)]
    measurements = []
    for index, (problem, seed, layout, knowledge, point) in enumerate(snapshots):
        times = {"normal": [], "nosignal": []}
        choices = {}
        for repeat in range(args.repeats):
            order = ("normal", "nosignal") if (index+repeat) % 2 == 0 else ("nosignal", "normal")
            for variant in order:
                begin = time.perf_counter()
                choices[variant] = (choose_measurement(knowledge, point) if variant == "normal" else
                                    choose_measurement_nosignal(knowledge, point, config=config))
                times[variant].append((time.perf_counter()-begin)*1000)
        normal_q, _ = choices["normal"]
        aware_q, info = choices["nosignal"]
        measurements.append({"problem": problem, "seed": seed, "layout": layout,
            "channel": knowledge.channel, "current": point,
            "positive_count": len(knowledge.positive_positions), "observation_count": len(knowledge.observations),
            "normal_point": normal_q, "nosignal_point": aware_q,
            "changed": normal_q != aware_q, "baseline_fallback_reason": info.get("baseline_fallback_reason"),
            "normal_median_ms": statistics.median(times["normal"]),
            "nosignal_median_ms": statistics.median(times["nosignal"]),
            "hypothesis_summary": info.get("hypothesis_summary"),
            "no_signal_probability_min": info.get("no_signal_probability_min"),
            "no_signal_probability_max": info.get("no_signal_probability_max"),
            "convex_visibility_mixed_candidates": info.get("convex_visibility_mixed_candidates", False),
            "observations": [asdict(observation) for observation in knowledge.observations],
            "hull": knowledge.hull})
    normal_times = [row["normal_median_ms"] for row in measurements]
    aware_times = [row["nosignal_median_ms"] for row in measurements]
    changed = sum(row["changed"] for row in measurements)
    p4 = [row for row in measurements if row["problem"] == 4]
    summary = {"purpose": manifest["purpose"], "snapshot_count": len(measurements),
        "changed_choices": changed, "changed_fraction": changed/len(measurements) if measurements else None,
        "p4_snapshot_count": len(p4), "p4_changed_choices": sum(row["changed"] for row in p4),
        "p4_changed_fraction": sum(row["changed"] for row in p4)/len(p4) if p4 else None,
        "baseline_fallback_count": sum(row["baseline_fallback_reason"] is not None for row in measurements),
        "convex_visibility_mixed_count": sum(row["convex_visibility_mixed_candidates"] for row in measurements),
        "normal_mean_ms": statistics.mean(normal_times) if normal_times else None,
        "nosignal_mean_ms": statistics.mean(aware_times) if aware_times else None,
        "mean_added_ms": statistics.mean([b-a for a, b in zip(normal_times, aware_times)]) if measurements else None,
        "normal_p95_ms": percentile(normal_times, .95), "nosignal_p95_ms": percentile(aware_times, .95),
        "all_twelve_case_runs_complete": all(c[v]["status"] == "complete" and c[v]["all_cleared"]
                                              for c in cases for v in ("normal", "nosignal")),
        "hull_violations": sum(c[v]["hull_violations"] for c in cases for v in ("normal", "nosignal")),
        "case_virtual_deltas_s": [case["virtual_delta_s"] for case in cases],
        "performance_claim": "diagnostic only; no parameter changes or model selection from these cases"}
    for name, value in (("cases.json", cases), ("summary.json", summary)):
        (output/name).write_text(json.dumps(value, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    (output/"snapshot_metrics.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False)+"\n"
                                                           for row in measurements), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(not summary["all_twelve_case_runs_complete"] or summary["hull_violations"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
