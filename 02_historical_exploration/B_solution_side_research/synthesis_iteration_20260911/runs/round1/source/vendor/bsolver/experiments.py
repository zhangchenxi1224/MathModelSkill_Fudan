"""External scenario generator/evaluator. Hidden truth never enters Solver."""
from dataclasses import asdict, replace
from pathlib import Path
import csv
import hashlib
import json
import math
import random
import time
from .protocol import RobotClient
from .simulator import LocalSimulator, Source
from .strategy import Solver, SolverConfig


def generate_case(seed, problem=4, n=None, layout="random", radius_mode="mixed", error_mode="deterministic"):
    rng = random.Random(seed)
    n = n if n is not None else rng.randint(10, 16)
    channels = rng.sample(range(1, 21), n)
    sources = []
    for i, channel in enumerate(channels):
        theta = rng.random()*2*math.pi
        r = 1800*math.sqrt(rng.random())
        if layout == "boundary":
            r, theta = 1800., 2*math.pi*i/n
        elif layout == "clustered":
            r, theta = rng.uniform(1000, 1100), rng.uniform(-.04, .04)
        elif layout == "center_and_boundary":
            r = 0. if i == 0 else 1800.
        p = r*math.cos(theta), r*math.sin(theta)
        radius = (1000. if radius_mode == "min" else 1500. if radius_mode == "max"
                  else rng.uniform(1000, 1500))
        orientation = None
        if problem == 4 and (layout == "boundary" or i % 3 != 0):
            orientation = math.degrees(theta) if layout == "boundary" else rng.uniform(0, 360)
        sources.append({"channel": channel, "position": p, "radius": radius,
                        "direction_deg": orientation})
    if layout == "hidden_outward_last" and problem == 4:
        for item in sources[:-1]:
            item["position"] = (rng.uniform(-350, 350), rng.uniform(-350, 350))
            item["direction_deg"] = None
        sources[-1]["position"] = (1800., 0.)
        sources[-1]["direction_deg"] = 0.
        sources[-1]["radius"] = 1000.
    return {"case_id": f"local-p{problem}-{seed}-{layout}-{radius_mode}-{error_mode}",
            "seed": seed, "problem": problem, "layout": layout,
            "radius_mode": radius_mode, "error_mode": error_mode, "sources": sources}


def variant_configs(problem):
    base = SolverConfig(problem=problem, sensing="fixed", scheduling="scan_then_clear",
                        coverage="square", nearest_safe=False)
    return {
        "baseline": base,
        "active": replace(base, sensing="active"),
        "nearest_safe": replace(base, sensing="active", nearest_safe=True),
        "immediate": replace(base, sensing="active", nearest_safe=True, scheduling="immediate"),
        "joint_square": replace(base, sensing="active", nearest_safe=True, scheduling="joint"),
        "joint_triangular": replace(base, sensing="active", nearest_safe=True, scheduling="joint", coverage="triangular"),
    }


def code_digest():
    h = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def run_case(case, config, out_dir=None):
    out_dir = Path(out_dir) if out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir/"case.json").write_text(json.dumps(case, indent=2), encoding="utf-8")
    sources = [Source(channel=s["channel"], position=tuple(s["position"]),
                      radius=s["radius"], direction_deg=s["direction_deg"]) for s in case["sources"]]
    env = LocalSimulator(sources, robot_id="local-team", seed=case["seed"], error_mode=case["error_mode"])
    client = RobotClient(robot_id="local-team", transport=env,
                         log_path=str(out_dir/"requests.jsonl") if out_dir else None)
    solver = Solver(client, config, decision_log=str(out_dir/"decisions.jsonl") if out_dir else None)
    result = solver.run()
    # AFTER policy completion, an external evaluator may inspect truth.
    truth = env.summary()
    cleared = sum(k.status == "cleared" for k in solver.channels.values())
    violations = []
    truth_by_channel = {s["channel"]: tuple(s["position"]) for s in case["sources"]}
    from .geometry import contains
    for event in solver.decisions:
        state = event.get("knowledge")
        if state and state["channel"] in truth_by_channel:
            if not contains(state["hull"], truth_by_channel[state["channel"]], tol=1e-4):
                violations.append({"sequence": event["sequence"], "channel": state["channel"]})
    result.update({"environment": "self_built", "case_id": case["case_id"],
                   "seed": case["seed"], "layout": case["layout"],
                   "radius_mode": case["radius_mode"], "error_mode": case["error_mode"],
                   "source_total": len(sources), "clear_fraction": cleared/len(sources),
                   "hull_invariant_violations": violations, "environment_summary": truth,
                   "code_sha256": code_digest()})
    if out_dir:
        (out_dir/"result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def save_table(results, path):
    fields = ["variant", "case_id", "problem", "seed", "layout", "radius_mode", "error_mode",
              "status", "error", "source_total", "clear_successes", "clear_fraction",
              "total_virtual_time_s", "average_clear_time_s", "walk_distance_m", "measures",
              "switches", "clear_attempts", "fallback_targets", "program_real_time_s", "timing_residual_s"]
    with Path(path).open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)


def run_batch(cases, variants, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    all_results = []
    for case in cases:
        configs = variant_configs(case["problem"])
        for variant in variants:
            result = run_case(case, configs[variant], output/variant/case["case_id"])
            result["variant"] = variant
            all_results.append(result)
            save_table(all_results, output/"results.csv")
            print(json.dumps({"variant": variant, "case": case["case_id"], "status": result["status"],
                              "cleared": result["clear_successes"], "total": result["source_total"],
                              "virtual_s": round(result["total_virtual_time_s"], 3),
                              "real_s": round(result["program_real_time_s"], 3), "error": result["error"]}), flush=True)
    (output/"results.json").write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    return all_results
