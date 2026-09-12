"""Local adapter for the EXACT baseline/survey protocols; no official actions.

Examples:
  python scripts/run_protocol_checks.py --fixture-broad --models mixed:deterministic --cases-per-protocol 2,1 --workers 2 --output results/round1/protocol_bench
  python scripts/run_protocol_checks.py --calibration MODEL --models min:deterministic,max:deterministic --cases-per-protocol 60,20 --workers 8 --output OUT
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import copy
import hashlib
import json
from pathlib import Path
import random
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bsolver.geometry import contains
from bsolver.protocol import RobotClient
from bsolver.round_experiments import (ERROR_MODES, RADIUS_MODES, FROZEN_CORE_SHA256,
    assert_frozen_core, canonical, categorical, core_digest, digest, frozen_baseline,
    make_scenario, model_distribution, seed_for, write_csv, write_json)
from bsolver.simulator import FixedErrorField, LocalSimulator, Source
from bsolver.strategy import Solver
from bsolver.survey import SurveySolver, SURVEY_PROTOCOL_VERSION


def survey_digest():
    return hashlib.sha256((ROOT / "src/bsolver/survey.py").read_bytes()).hexdigest()


def adapter_digest():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def parse_models(text):
    tokens = [f"{r}:{e}" for r in RADIUS_MODES for e in ERROR_MODES] if text == "all" else text.split(",")
    models = []
    for token in tokens:
        radius, error = token.strip().split(":")
        if radius == "uniform":
            radius = "mixed"
        if error == "hash":
            error = "deterministic"
        if radius not in RADIUS_MODES or error not in ERROR_MODES:
            raise ValueError("models require min/mixed/max : deterministic/correlated/extreme")
        if (radius, error) in models:
            raise ValueError("Duplicate model")
        models.append((radius, error))
    return models


def broad_distribution(problem):
    return [{"n": n, "n_directed": nd, "probability": 1 / 7 / (n + 1 if problem == 4 else 1)}
            for n in range(10, 17) for nd in (range(n + 1) if problem == 4 else [0])]


def prepare(calibration, *, models, counts=(60, 20), master_seed=2026091131,
            composition_model="smoothed_joint", fixture_broad=False, allow_exploratory=False):
    if len(counts) != 2 or any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in counts) or not sum(counts):
        raise ValueError("cases-per-protocol requires baseline,survey counts with positive total")
    if fixture_broad and calibration is not None:
        raise ValueError("A broad fixture and official calibration cannot be mixed")
    distributions = {p: broad_distribution(p) if fixture_broad else model_distribution(
        calibration, p, composition_model, allow_exploratory=allow_exploratory) for p in (3, 4)}
    source_status = "synthetic_fixture_no_official_data" if fixture_broad else calibration.get("model_status", "unknown")
    effective_composition = "broad_assumption" if fixture_broad else composition_model
    cases = []
    for radius_mode, error_mode in models:
        model_name = f"{effective_composition}__radius_{radius_mode}__noise_{error_mode}"
        specification = {"composition_model": effective_composition,
            "composition_model_sha256": digest(distributions), "radius_mode": radius_mode,
            "noise_model": error_mode, "correlation_length_m": 150.,
            "position_model": "uniform_area_disk_1800", "orientation_model": "uniform_direction_independent_of_position_given_Ndir",
            "model_calibration_scope": "joint composition only; all other mechanisms are candidates"}
        for problem in (3, 4):
            for protocol, count in zip(("baseline", "survey"), counts):
                for index in range(count):
                    common_id = f"local-check-p{problem}-{protocol}-{index+1:04d}"
                    seed = seed_for(master_seed, problem, protocol, index)
                    n, nd = categorical(random.Random(seed_for(seed, "composition")), distributions[problem])
                    # Same g/u and mixed radius variates in each mechanism arm;
                    # only R transformation and deterministic field mode differ.
                    scenario = make_scenario(seed, problem, n, nd, radius_mode="mixed", error_mode="deterministic")
                    scenario["case_id"] = common_id
                    scenario["radius_mode"] = radius_mode
                    scenario["error_mode"] = error_mode
                    scenario["error_field"]["mode"] = error_mode
                    if radius_mode in ("min", "max"):
                        for source in scenario["sources"]:
                            source["radius"] = 1000. if radius_mode == "min" else 1500.
                    scenario.pop("scenario_sha256")
                    scenario["scenario_sha256"] = digest(scenario)
                    allocation = hashlib.sha256(f"{master_seed}:{common_id}".encode()).hexdigest()
                    assignment = {"case_id": common_id, "common_case_id": common_id,
                        "scenario_id": f"{model_name}:{common_id}", "case_key": f"{model_name}:{common_id}",
                        "problem": problem, "protocol": protocol, "environment": "local",
                        "survey_seed": int(allocation[:8], 16), "allocation_hash": allocation,
                        "split": "development", "split_method": "all local cases are evaluation only; never official composition fit",
                        "model_candidate": model_name, "model_id": digest(specification),
                        "composition_model": specification["composition_model"], "radius_mode": radius_mode,
                        "error_mode": error_mode, "correlation_length": specification["correlation_length_m"],
                        "layout": "random", "model_source_status": source_status,
                        "survey_protocol_version": SURVEY_PROTOCOL_VERSION,
                        "frozen_core_sha256": FROZEN_CORE_SHA256, "survey_sha256": survey_digest(),
                        "adapter_sha256": adapter_digest(), "scenario_sha256": scenario["scenario_sha256"],
                        "phase": "mechanism_validation_before_policy_selection"}
                    cases.append({"assignment": assignment, "scenario": scenario})
    plan = {"schema_version": 1, "source_status": source_status,
        "calibration_model_id": calibration.get("model_id") if calibration else None,
        "calibration_sha256": digest(calibration) if calibration else None,
        "composition_model": "broad_assumption" if fixture_broad else composition_model,
        "models": [f"{r}:{e}" for r, e in models], "counts_per_problem": dict(zip(("baseline", "survey"), counts)),
        "master_seed": master_seed, "planned_runs": len(cases), "frozen_core_sha256": FROZEN_CORE_SHA256,
        "survey_sha256": survey_digest(), "adapter_sha256": adapter_digest(),
        "formal_authorized": False, "case_truth_not_passed_to_policy": True, "cases": cases}
    plan["plan_sha256"] = digest(plan)
    return plan


def run_one(case, output, resume=False):
    assert_frozen_core()
    assignment, scenario = case["assignment"], case["scenario"]
    if survey_digest() != assignment["survey_sha256"] or adapter_digest() != assignment["adapter_sha256"]:
        raise ValueError("Survey/adapter changed after local protocol plan was frozen")
    directory = Path(output) / "models" / assignment["model_candidate"] / "cases" / assignment["case_id"]
    directory.mkdir(parents=True, exist_ok=True)
    result_path = directory / "result.json"
    if result_path.exists():
        if not resume:
            raise ValueError(f"Existing result; use --resume: {result_path}")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("assignment_sha256") != digest(assignment):
            raise ValueError("Existing result differs from frozen assignment")
        return result
    write_json(directory / "assignment.json", assignment)  # persisted BEFORE entering
    write_json(directory / "case.json", scenario)
    field_spec = scenario["error_field"]
    field = FixedErrorField(field_spec["seed"], field_spec["mode"], field_spec["correlation_length_m"])
    environment = LocalSimulator([Source(**s) for s in scenario["sources"]], robot_id="local-team",
                                 error_field=field, enforce_case_size=True)
    client = RobotClient(robot_id="local-team", transport=environment, log_path=directory / "requests.jsonl")
    config = frozen_baseline(assignment["problem"])
    if assignment["protocol"] == "baseline":
        policy = Solver(client, config, decision_log=directory / "decisions.jsonl")
    elif assignment["protocol"] == "survey":
        policy = SurveySolver(client, config, survey_seed=assignment["survey_seed"], sample_count=2,
                              decision_log=directory / "decisions.jsonl")
    else:
        raise ValueError("Only exact baseline/survey protocols may enter these checks")
    try:
        result = policy.run()
    finally:
        client.close_log()
    # External truth audit begins only after the policy run and exit attempt.
    truth = environment.summary()
    sources = {s["channel"]: s["position"] for s in scenario["sources"]}
    violations = []
    for event in policy.decisions:
        knowledge = event.get("knowledge")
        if knowledge and knowledge["channel"] in sources and not contains(knowledge["hull"], sources[knowledge["channel"]], tol=1e-4):
            violations.append({"sequence": event["sequence"], "channel": knowledge["channel"]})
    complete = result["status"] == "complete" and truth["all_cleared"] and not result["error"] and not violations
    result.update({k: assignment[k] for k in ("case_id", "problem", "protocol", "environment", "model_candidate",
                    "model_id", "composition_model", "survey_seed", "radius_mode", "error_mode", "correlation_length", "layout")})
    result.update({"assignment_sha256": digest(assignment), "source_total": scenario["n"],
        "clear_fraction": truth["clearance_ratio"], "evaluation_complete": complete,
        "environment_summary": truth, "hull_invariant_violations": violations,
        "policy_core_sha256": core_digest(), "survey_sha256": survey_digest(), "adapter_sha256": adapter_digest(),
        "failure_penalized_time_s": result["total_virtual_time_s"] if complete else 360000.})
    write_json(directory / "result.json", result)
    audit = {"case_id": assignment["case_id"], "problem": assignment["problem"], "protocol": assignment["protocol"],
        "environment": "local", "N": scenario["n"], "Ndir": scenario["n_directed"],
        "Nomni": scenario["n"] - scenario["n_directed"], "audit_source": "immutable_local_scenario_after_policy_run",
        "exit_confirmed": client.exited, "all_cleared": truth["all_cleared"], "cleared_count": truth["cleared_count"],
        "model_candidate": assignment["model_candidate"], "truth_was_passed_to_policy": False}
    write_json(directory / "post_exit_audit.json", audit)
    return result


def execute(plan, output, *, workers=4, resume=False):
    assert_frozen_core()
    if workers < 1:
        raise ValueError("workers must be positive")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "plan.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing["plan_sha256"] != plan["plan_sha256"]:
            raise ValueError("Output contains another frozen protocol plan")
    write_json(path, plan)
    for case in plan["cases"]:
        a = case["assignment"]
        folder = output / "models" / a["model_candidate"] / "cases" / a["case_id"]
        write_json(folder / "assignment.json", a)
    started, results = time.monotonic(), []
    def accept(result):
        results.append(result)
        print(canonical({"done": len(results), "planned": len(plan["cases"]), "model": result["model_candidate"],
            "problem": result["problem"], "protocol": result["protocol"], "complete": result["evaluation_complete"]}), flush=True)
    if workers == 1:
        for case in plan["cases"]:
            accept(run_one(case, output, resume))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            jobs = [pool.submit(run_one, case, str(output), resume) for case in plan["cases"]]
            for job in as_completed(jobs):
                accept(job.result())
    results.sort(key=lambda r: (r["model_candidate"], r["problem"], r["protocol"], r["case_id"]))
    write_json(output / "results.json", results)
    groups = defaultdict(list)
    for row in results:
        groups[row["model_candidate"], row["problem"], row["protocol"]].append(row)
    summary = []
    for (model, problem, protocol), rows in sorted(groups.items()):
        summary.append({"model_candidate": model, "problem": problem, "protocol": protocol, "n_cases": len(rows),
            "complete_cases": sum(r["evaluation_complete"] for r in rows),
            "hull_invariant_violations": sum(len(r["hull_invariant_violations"]) for r in rows),
            "total_virtual_time_s_mean": statistics.fmean(r["total_virtual_time_s"] for r in rows),
            "failure_penalized_time_s_mean": statistics.fmean(r["failure_penalized_time_s"] for r in rows),
            "program_real_time_s_mean": statistics.fmean(r["program_real_time_s"] for r in rows),
            "model_comparison_status": "not_yet_checked_against_official_observables"})
    write_json(output / "summary.json", summary)
    write_csv(output / "summary.csv", summary)
    write_json(output / "failures.json", [r for r in results if not r["evaluation_complete"]])
    metadata = {"planned": len(plan["cases"]), "completed_runs": len(results), "workers": workers,
        "wall_time_s": time.monotonic() - started, "runtime_sum_s": sum(r["program_real_time_s"] for r in results),
        "source_status": plan["source_status"], "case_source": "local_simulator", "frozen_core_sha256": core_digest(),
        "survey_sha256": survey_digest(), "adapter_sha256": adapter_digest(),
        "comparison_pending": "calibration.model_checks must compare with matching official baseline/survey before any model is called matched"}
    write_json(output / "run_metadata.json", metadata)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--fixture-broad", action="store_true")
    parser.add_argument("--allow-exploratory", action="store_true")
    parser.add_argument("--models", default="mixed:deterministic")
    parser.add_argument("--composition-model", default="smoothed_joint")
    parser.add_argument("--cases-per-protocol", default="60,20")
    parser.add_argument("--seed", type=int, default=2026091131)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    calibration = json.loads(args.calibration.read_text(encoding="utf-8-sig")) if args.calibration else None
    plan = prepare(calibration, models=parse_models(args.models), counts=tuple(map(int, args.cases_per_protocol.split(","))),
        master_seed=args.seed, composition_model=args.composition_model, fixture_broad=args.fixture_broad,
        allow_exploratory=args.allow_exploratory)
    if args.prepare_only:
        write_json(args.output / "plan.json", plan)
        result = {k: v for k, v in plan.items() if k != "cases"}
    else:
        result = execute(plan, args.output, workers=args.workers, resume=args.resume)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
