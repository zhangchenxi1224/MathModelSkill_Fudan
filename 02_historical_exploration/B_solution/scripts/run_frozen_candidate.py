"""Run a frozen per-problem candidate in LOCAL synthetic environments only.

No official transport, GUI, launch or dispatch function is called. These seeded
engineering reproductions are neither a new tuning stage nor official evidence.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
sys.path.insert(0, str(ROOT/"scripts"))
from run_official_validation import (CORE_SHA256, normalize_spec, read_object,
                                     sha256, verify_baseline)
from bsolver.geometry import contains
from bsolver.protocol import RobotClient
from bsolver.simulator import FixedErrorField, LocalSimulator, generate_sources
from bsolver.strategy import Solver, SolverConfig


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def check_candidate(candidate, *, project_root=ROOT):
    candidate, project_root = Path(candidate).resolve(), Path(project_root).resolve()
    value = read_object(candidate)
    if set(value.get("problems", {})) != {"3", "4"}:
        raise ValueError("Candidate must contain both problems 3 and 4")
    specs = {p: normalize_spec(value["problems"][str(p)], p) for p in (3, 4)}
    canonical(specs)  # Reject nonfinite JSON parameters rather than recording NaN.
    if value.get("baseline_core_sha256", CORE_SHA256) != CORE_SHA256:
        raise ValueError("Candidate declares a different baseline core")
    dependencies = value.get("dependency_hashes")
    if not isinstance(dependencies, dict):
        raise ValueError("Candidate dependency_hashes must be a path-to-SHA256 object")
    if any(s["solver_class"] == "nosignal" for s in specs.values()) and "src/bsolver/nosignal_sensing.py" not in dependencies:
        raise ValueError("NoSignal requires its frozen module dependency hash")
    for relative, expected in dependencies.items():
        path = (project_root/relative).resolve()
        if not path.is_relative_to(project_root) or not path.is_file():
            raise ValueError(f"Candidate dependency is not a project file: {relative}")
        if not isinstance(expected, str) or sha256(path) != expected:
            raise ValueError(f"Candidate dependency changed: {relative}")
    baseline = candidate.parent/"baseline_freeze.json"
    if not baseline.exists():
        baseline = project_root/"results/round1/baseline_freeze.json"
    verify_baseline(baseline, project_root=project_root)
    candidate_sha = sha256(candidate)
    anchors = []
    for filename in ("plan.json", "validation_freeze.json"):
        path = candidate.parent/filename
        if path.exists():
            anchor = read_object(path)
            if anchor.get("candidate_sha256") != candidate_sha:
                raise ValueError(f"Candidate SHA differs from existing {filename}")
            anchors.append({"path": str(path), "sha256": sha256(path)})
    return {"candidate_path": str(candidate), "candidate_sha256": candidate_sha,
            "candidate_dependencies": dependencies, "policy_specs": specs,
            "baseline_source_sha256": CORE_SHA256, "baseline_freeze_path": str(baseline),
            "baseline_freeze_sha256": sha256(baseline), "candidate_hash_anchors": anchors,
            "candidate_hash_scope": "matched existing freeze/plan" if anchors else "explicit input file hash; no adjacent plan/freeze hash anchor"}


def make_case(seed, problem, index):
    token = canonical(["frozen-candidate-local-v1", seed, problem, index])
    case_seed = int.from_bytes(hashlib.sha256(token.encode()).digest()[:8], "big")
    sources = generate_sources(case_seed, directional_fraction=0. if problem == 3 else .5)
    case = {"case_id": f"local-p{problem}-{index+1:04d}-{case_seed:016x}",
            "problem": problem, "seed": case_seed, "environment": "local_synthetic",
            "sources": [asdict(s) for s in sources],
            "error_field": {"seed": case_seed, "mode": "correlated", "correlation_length_m": 150.}}
    case["scenario_sha256"] = hashlib.sha256(canonical(case).encode()).hexdigest()
    return case


def make_solver(client, spec, decision_log):
    config = SolverConfig(**spec["solver_config"])
    if spec["solver_class"] == "nosignal":
        from bsolver.nosignal_sensing import NoSignalConfig, NoSignalSolver
        extra = dict(spec["nosignal_config"])
        extra["radius_samples"] = tuple(extra["radius_samples"])
        return NoSignalSolver(client, config, decision_log=decision_log,
                              nosignal_config=NoSignalConfig(**extra))
    return Solver(client, config, decision_log=decision_log)


def run_case(case, spec, folder, frozen):
    from bsolver.simulator import Source
    folder.mkdir(parents=True, exist_ok=False)
    write_json(folder/"scenario.json", case)  # Harness-only replay input, never passed to the policy.
    error = case["error_field"]
    environment = LocalSimulator([Source(**s) for s in case["sources"]], enforce_case_size=True,
        error_field=FixedErrorField(error["seed"], error["mode"], error["correlation_length_m"]))
    client = RobotClient(robot_id="local-team", transport=environment,
                         request_prefix=case["case_id"], log_path=folder/"requests.jsonl")
    solver = None
    start = time.monotonic()
    try:
        solver = make_solver(client, spec, folder/"decisions.jsonl")
        result = solver.run()
    except Exception as exc:
        result = {"status": "harness_error", "error": f"{type(exc).__name__}: {exc}",
                  "total_virtual_time_s": client.virtual_time,
                  "program_real_time_s": time.monotonic()-start}
    finally:
        client.close_log()
    # Only the post-run evaluator reads source locations and environment summary.
    truth = environment.summary()
    locations = {s["channel"]: s["position"] for s in case["sources"]}
    violations = []
    for event in solver.decisions if solver else []:
        knowledge = event.get("knowledge")
        if knowledge and knowledge["channel"] in locations and not contains(knowledge["hull"], locations[knowledge["channel"]], tol=1e-4):
            violations.append({"sequence": event["sequence"], "channel": knowledge["channel"]})
    complete = result["status"] == "complete" and truth["all_cleared"] and not result.get("error") and not violations
    result.update(environment="local_synthetic", evidence_role="engineering_reproduction_not_tuning_or_official",
                  case_id=case["case_id"], problem=case["problem"], seed=case["seed"],
                  policy_spec=spec, candidate_sha256=frozen["candidate_sha256"],
                  policy_core_sha256=CORE_SHA256, scenario_sha256=case["scenario_sha256"],
                  evaluation_complete=bool(complete), environment_summary=truth,
                  hull_invariant_violations=violations, public_client_stats=client.stats,
                  failure_penalized_time_s=result["total_virtual_time_s"] if complete else 360000.)
    write_json(folder/"result.json", result)
    return result


def execute(candidate, *, problem, seed, count, output):
    if isinstance(problem, bool) or problem not in (3, 4):
        raise ValueError("problem must be 3 or 4")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**64:
        raise ValueError("seed must be an integer in [0, 2**64)")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("count is the positive number of local cases")
    frozen = check_candidate(candidate)
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("Preserve earlier logs: choose a new output directory")
    output.mkdir(parents=True)
    (output/"candidate_input.json").write_bytes(Path(candidate).read_bytes())
    cases = [make_case(seed, problem, i) for i in range(count)]
    manifest = {"environment": "local_synthetic", "problem": problem, "master_seed": seed,
                "planned_local_cases": count, "frozen": frozen, "runner_sha256": sha256(__file__),
                "normalizer_sha256": sha256(ROOT/"scripts/run_official_validation.py"),
                "generator": "N uniform 10..16; source locations uniform by disk area; R uniform 1000..1500; P3 omni, P4 independent directional probability .5 with uniform orientation; fixed correlated150 error",
                "interpretation": "Explicit synthetic reference design, not fitted official population or a new tuning/confirmation allocation",
                "scenario_sha256": {c["case_id"]: c["scenario_sha256"] for c in cases},
                "formal_or_official_requests": False}
    write_json(output/"manifest.json", manifest)
    rows = []
    start = time.monotonic()
    for case in cases:
        try:
            checked = check_candidate(candidate)
        except Exception as exc:
            write_json(output/"interruption.json", {"reason": f"{type(exc).__name__}: {exc}", "finished_cases": len(rows)})
            raise
        if checked != frozen:
            write_json(output/"interruption.json", {"reason": "candidate or dependency freeze changed", "finished_cases": len(rows)})
            raise ValueError("Candidate or dependency freeze changed during local reproduction")
        row = run_case(case, frozen["policy_specs"][problem], output/"cases"/case["case_id"], frozen)
        rows.append(row)
        write_json(output/"results.json", rows)
        print(canonical({"case_id": row["case_id"], "evaluation_complete": row["evaluation_complete"],
                         "virtual_time_s": row["total_virtual_time_s"]}), flush=True)
    summary = {"environment": "local_synthetic", "evidence_role": "engineering_reproduction_not_tuning_or_official",
               "cases": len(rows), "complete": sum(r["evaluation_complete"] for r in rows),
               "failures": sum(not r["evaluation_complete"] for r in rows),
               "hull_invariant_violations": sum(len(r["hull_invariant_violations"]) for r in rows),
               "wall_time_s": time.monotonic()-start, "candidate_sha256": frozen["candidate_sha256"]}
    write_json(output/"summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--problem", type=int, choices=(3, 4), required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--count", type=int, default=1, help="number of local cases, not number of sources")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(execute(args.candidate, problem=args.problem, seed=args.seed, count=args.count,
                             output=args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
