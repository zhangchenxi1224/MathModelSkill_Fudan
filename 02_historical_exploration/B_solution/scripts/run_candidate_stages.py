"""Local-only single-factor stage 2/3 comparisons with locked confirmation.

No global monkeypatching. Each arm directly instantiates Solver or NoSignalSolver.
New seeds/worlds are checked against every supplied earlier-stage manifest.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
import gzip
import hashlib
import json
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bsolver.geometry import contains
from bsolver.protocol import RobotClient
from bsolver.round_experiments import (FROZEN_CORE_SHA256, assert_frozen_core, canonical, compare_paired,
    core_digest, digest, frozen_baseline, generate_manifest, seed_for, validate_manifest, write_csv, write_json)
from bsolver.simulator import FixedErrorField, LocalSimulator, Source
from bsolver.strategy import Solver, SolverConfig


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def json_value(value):
    return json.loads(canonical(value))


def nosignal_config(values=None):
    from bsolver.nosignal_sensing import NoSignalConfig
    values = dict(values or {})
    if "radius_samples" in values:
        values["radius_samples"] = tuple(values["radius_samples"])
    return NoSignalConfig(**values)


def dependency_hashes(needs_nosignal):
    paths = [Path(__file__), ROOT / "src/bsolver/round_experiments.py"]
    if needs_nosignal:
        paths.append(ROOT / "src/bsolver/nosignal_sensing.py")
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def arm_definitions(stage, limits, *, modes=("normal", "normal"), nosignal=None):
    if stage not in (2, 3) or len(limits) != 2 or any(isinstance(n, bool) or n not in (1, 2, 3, 5) for n in limits):
        raise ValueError("Stage 2/3 requires explicitly selected P3,P4 limits from 1,2,3,5")
    if len(modes) != 2 or any(m not in ("normal", "nosignal") for m in modes):
        raise ValueError("Select one normal/nosignal mode per problem")
    needs_nosignal = stage == 2 or "nosignal" in modes
    configuration = json_value(asdict(nosignal_config(nosignal))) if needs_nosignal else None
    definitions = {}
    for problem, limit, selected_mode in zip((3, 4), limits, modes):
        arms = [("normal", "normal", "joint"), ("nosignal", "nosignal", "joint")] if stage == 2 else [
            (scheduling, selected_mode, scheduling) for scheduling in ("joint", "immediate", "scan_then_clear")]
        definitions[str(problem)] = [{"variant": variant, "solver_class": mode,
            "solver_config": json_value(asdict(replace(frozen_baseline(problem), local_measure_limit=limit, scheduling=scheduling))),
            "nosignal_config": configuration if mode == "nosignal" else None} for variant, mode, scheduling in arms]
    return definitions


def world_signature(case):
    return digest({key: case[key] for key in ("problem", "sources", "error_field")})


def prepare_manifest(calibration, *, stage, limits, previous_manifests, sizes=(160, 160, 80),
                     master_seed=None, modes=("normal", "normal"), nosignal=None, mechanism_candidates=None):
    if not previous_manifests:
        raise ValueError("Earlier manifests are required to audit fresh scenario allocation")
    for old in previous_manifests:
        validate_manifest(old)
    required_stages = {1} if stage == 2 else {1, 2}
    if not required_stages <= {old.get("stage") for old in previous_manifests}:
        raise ValueError("Supply stage1 for stage2, and stage1+stage2 for stage3")
    seed = master_seed if master_seed is not None else {2: 2026091202, 3: 2026091303}.get(stage)
    if seed is None or seed in {old["master_seed"] for old in previous_manifests}:
        raise ValueError("Later stages require a new predeclared master seed")
    definitions = arm_definitions(stage, limits, modes=modes, nosignal=nosignal)
    latest_previous = max(previous_manifests, key=lambda m: m["stage"])
    mechanisms = mechanism_candidates if mechanism_candidates is not None else latest_previous.get("mechanism_candidates")
    inherited_models = tuple(latest_previous.get("calibrated_models", ()))
    if sizes[0] and not inherited_models:
        raise ValueError("A later calibrated pool requires composition models frozen in the latest earlier manifest")
    manifest = generate_manifest(calibration, sizes=sizes, master_seed=seed, mechanism_candidates=mechanisms,
                                 calibrated_models=inherited_models or ("smoothed_joint",))
    old_seeds = {case["seed"] for old in previous_manifests for case in old["scenarios"]}
    old_worlds = {world_signature(case) for old in previous_manifests for case in old["scenarios"]}
    for case in manifest["scenarios"]:
        if case["seed"] in old_seeds or world_signature(case) in old_worlds:
            raise ValueError("Later-stage scene overlaps a previously generated scene")
        case["case_id"] = f"stage{stage}-" + case["case_id"]
        case["stage"] = stage
        case.pop("scenario_sha256")
        case["scenario_sha256"] = digest(case)
    manifest.update({"stage": stage, "arm_definitions": definitions,
        "baseline": "normal" if stage == 2 else "joint",
        "selected_local_limits": dict(zip(("3", "4"), limits)),
        "selected_sensing_modes": dict(zip(("3", "4"), modes)) if stage == 3 else None,
        "single_factor": "normal_vs_nosignal_measurement_selection" if stage == 2 else "scheduling_only",
        "planned_strategy_runs": sum(len(definitions[str(c["problem"])]) for c in manifest["scenarios"]),
        "previous_manifests": [{"stage": m["stage"], "manifest_sha256": m["manifest_sha256"], "master_seed": m["master_seed"]} for m in previous_manifests],
        "fresh_world_audit": "no shared seed or source+error-field world with any supplied earlier manifest",
        "dependency_sha256": dependency_hashes(stage == 2 or "nosignal" in modes),
        "confirmation_policy": "run development first; only baseline and preregistered selected candidate may enter confirmation"})
    manifest.pop("manifest_sha256")
    manifest["manifest_sha256"] = digest(manifest)
    return manifest


def validate_dependencies(manifest):
    assert_frozen_core()
    validate_manifest(manifest)
    for filename, expected in manifest["dependency_sha256"].items():
        if hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Frozen experiment dependency changed: {filename}")


def create_selection(manifest, development_results, choices):
    validate_manifest(manifest)
    if len(choices) != 2:
        raise ValueError("Exactly one selected variant per problem is required")
    expected = {(c["case_id"], a["variant"]) for c in manifest["scenarios"] if c["partition"] == "development"
                for a in manifest["arm_definitions"][str(c["problem"]) ]}
    actual = {(r["case_id"], r["variant"]) for r in development_results}
    if actual != expected or len(actual) != len(development_results) or any(r["partition"] != "development" for r in development_results):
        raise ValueError("Selection requires exactly the complete development allocation, excluding confirmation")
    if any(r.get("manifest_sha256") != manifest["manifest_sha256"] for r in development_results):
        raise ValueError("Development results belong to another frozen manifest")
    selected = dict(zip(("3", "4"), choices))
    for problem, variant in selected.items():
        if variant not in {a["variant"] for a in manifest["arm_definitions"][problem]}:
            raise ValueError("Selected arm was not preregistered")
    selection = {"stage": manifest["stage"], "manifest_sha256": manifest["manifest_sha256"],
        "selected_variants": selected, "selection_basis": "development_only", "frozen_before_confirmation": True,
        "development_results_sha256": digest(development_results), "formal_authorized": False,
        "practice_validation_in_authorized_plan": True, "official_dispatch_ready": False}
    selection["selection_sha256"] = digest(selection)
    return selection


def validate_selection(manifest, selection):
    if not selection:
        raise ValueError("Confirmation requires a previously frozen selection JSON")
    copy = dict(selection)
    claimed = copy.pop("selection_sha256", None)
    if digest(copy) != claimed or selection.get("manifest_sha256") != manifest["manifest_sha256"]:
        raise ValueError("Selection hash/manifest mismatch")
    if selection.get("selection_basis") != "development_only" or not selection.get("frozen_before_confirmation"):
        raise ValueError("Selection must precede confirmation and use development only")
    selected = selection.get("selected_variants", {})
    if selection.get("stage") != manifest["stage"] or set(selected) != {"3", "4"}:
        raise ValueError("Selection must identify this stage and both problems")
    for problem, variant in selected.items():
        if variant not in {a["variant"] for a in manifest["arm_definitions"][problem]}:
            raise ValueError("Selected arm was not preregistered")


def run_arm(case, arm, manifest, directory, logs=True):
    validate_dependencies(manifest)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    specification = case["error_field"]
    environment = LocalSimulator([Source(**s) for s in case["sources"]], enforce_case_size=True,
        error_field=FixedErrorField(specification["seed"], specification["mode"], specification["correlation_length_m"]))
    client = RobotClient(robot_id="local-team", transport=environment,
                         log_path=directory / "requests.jsonl" if logs else None)
    config = SolverConfig(**arm["solver_config"])
    if arm["solver_class"] == "normal":
        policy = Solver(client, config)
    elif arm["solver_class"] == "nosignal":
        from bsolver.nosignal_sensing import NoSignalSolver
        policy = NoSignalSolver(client, config, nosignal_config=nosignal_config(arm["nosignal_config"]))
    else:
        raise ValueError("Unknown preregistered solver class")
    try:
        result = policy.run()
    finally:
        client.close_log()
    truth = environment.summary()
    locations = {s["channel"]: s["position"] for s in case["sources"]}
    violations = []
    for event in policy.decisions:
        k = event.get("knowledge")
        if k and k["channel"] in locations and not contains(k["hull"], locations[k["channel"]], tol=1e-4):
            violations.append({"sequence": event["sequence"], "channel": k["channel"]})
    complete = result["status"] == "complete" and truth["all_cleared"] and not result["error"] and not violations
    result.update({k: case[k] for k in ("case_id", "problem", "pool", "composition_model", "mechanism_id", "mechanism_status", "partition", "layout", "radius_mode", "error_mode", "n", "n_directed", "scenario_sha256")})
    result.update({"stage": manifest["stage"], "manifest_sha256": manifest["manifest_sha256"], "variant": arm["variant"],
        "arm_sha256": digest(arm), "solver_class": arm["solver_class"], "nosignal_config": arm["nosignal_config"],
        "source_total": case["n"], "clear_fraction": truth["clearance_ratio"], "evaluation_complete": complete,
        "environment": "self_built_fixed_scenario", "environment_summary": truth,
        "hull_invariant_violations": violations, "error_field_sha256": digest(specification),
        "policy_core_sha256": core_digest(), "dependency_sha256": manifest["dependency_sha256"],
        "failure_penalized_time_s": result["total_virtual_time_s"] if complete else 360000.})
    if logs:
        with gzip.open(directory / "decisions.jsonl.gz", "wt", encoding="utf-8") as stream:
            for event in policy.decisions:
                stream.write(canonical(event) + "\n")
        path = directory / "requests.jsonl"
        if path.exists():
            with gzip.open(directory / "requests.jsonl.gz", "wb") as stream:
                stream.write(path.read_bytes())
            path.unlink()
    write_json(directory / "result.json", result)
    return result


def run_case(case, manifest, output, resume, logs, selection):
    arms = list(manifest["arm_definitions"][str(case["problem"])])
    if case["partition"] == "confirmation":
        allowed = {manifest["baseline"], selection["selected_variants"][str(case["problem"]) ]}
        arms = [a for a in arms if a["variant"] in allowed]
    random.Random(seed_for(case["seed"], "stage_arm_order")).shuffle(arms)
    results = []
    for arm in arms:
        directory = Path(output) / "runs" / case["pool"] / case["case_id"] / arm["variant"]
        path = directory / "result.json"
        if path.exists():
            if not resume:
                raise ValueError("Existing stage run requires --resume")
            result = read(path)
            if any(result.get(k) != v for k, v in {"manifest_sha256": manifest["manifest_sha256"],
                    "arm_sha256": digest(arm), "scenario_sha256": case["scenario_sha256"]}.items()):
                raise ValueError("Stored result does not match the frozen scene/arm")
        else:
            result = run_arm(case, arm, manifest, directory, logs)
        results.append(result)
    return results


def summarize(results, manifest, output):
    pairs, groups = compare_paired(results, baseline=manifest["baseline"])
    write_json(output / "paired_cases.json", pairs)
    write_csv(output / "paired_cases.csv", pairs)
    write_json(output / "paired_summary.json", groups)
    write_csv(output / "paired_summary.csv", groups)
    write_json(output / "failures.json", [r for r in results if not r["evaluation_complete"]])
    lines = [f"# 阶段{manifest['stage']}单因素同场景配对", "",
        f"因素：`{manifest['single_factor']}`；基线：`{manifest['baseline']}`。各题的L与其余配置都已冻结。",
        "失败惩罚360000秒；失败未当成短完成。按池/题/组成/噪声分别报告，区间是点态探索性配对bootstrap。",
        "新的独立场景种子不复用之前确认集。development与confirmation分开运行、分开存放；确认只比较冻结选择与基线。", "",
        "design_mixture是设计组合，不是官方总体，所有机制层单独保留。", "",
        "|池|题|模型|机制|分区|候选|配对n|失败 基/候|惩罚时间差均值s|95% CI|双成功退步率|",
        "|---|---|---|---|---|---|---:|---:|---:|---|---:|"]
    fmt = lambda x: "NA" if x is None else f"{x:.2f}"
    for r in groups:
        if r["noise_model"] != "all" or r["partition"] == "all":
            continue
        lines.append(f"|{r['pool']}|{r['problem']}|{r['composition_model']}|{r['mechanism_id']}|{r['partition']}|{r['variant']}|{r['n_pairs']}|{r['baseline_failures']}/{r['candidate_failures']}|{fmt(r['penalized_time_delta_mean_s'])}|[{fmt(r['penalized_delta_ci_low_s'])},{fmt(r['penalized_delta_ci_high_s'])}]|{fmt(r['completed_regression_rate'])}|")
    lines += ["", "选择基线自身时确认区可只有一臂，此时没有候选配对比较，不补造改进。正式测试未授权；本脚本不含官方接口。", ""]
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def execute(manifest, output, *, partition="development", workers=4, resume=False, logs=True, selection=None):
    validate_dependencies(manifest)
    if partition not in ("development", "confirmation") or workers < 1:
        raise ValueError("Run one partition at a time with positive workers")
    if partition == "confirmation":
        validate_selection(manifest, selection)
    elif selection is not None:
        raise ValueError("A selection is used only for the locked confirmation partition")
    output = Path(output) / partition
    output.mkdir(parents=True, exist_ok=True)
    allocation = {"manifest_sha256": manifest["manifest_sha256"], "partition": partition,
                  "selection_sha256": selection.get("selection_sha256") if selection else None}
    if (output / "allocation.json").exists() and read(output / "allocation.json") != allocation:
        raise ValueError("Do not change a stage/selection in an existing output partition")
    write_json(output / "allocation.json", allocation)
    if selection:
        write_json(output / "selection_before_confirmation.json", selection)
    cases = [c for c in manifest["scenarios"] if c["partition"] == partition]
    started, results = time.monotonic(), []
    def accept(rows):
        results.extend(rows)
        print(canonical({"stage": manifest["stage"], "partition": partition, "completed_strategy_runs": len(results),
              "case_id": rows[0]["case_id"], "complete_arms": sum(r["evaluation_complete"] for r in rows)}), flush=True)
    if workers == 1:
        for case in cases:
            accept(run_case(case, manifest, output, resume, logs, selection))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run_case, case, manifest, str(output), resume, logs, selection) for case in cases]
            for future in as_completed(futures):
                accept(future.result())
    results.sort(key=lambda r: (r["pool"], r["case_id"], r["variant"]))
    write_json(output / "results.json", results)
    write_csv(output / "results.csv", [{k: v for k, v in r.items() if k not in ("stop_evidence", "environment_summary")} for r in results])
    metadata = {"stage": manifest["stage"], "partition": partition, "distinct_scenarios": len(cases),
        "strategy_runs": len(results), "wall_time_s": time.monotonic() - started,
        "runtime_sum_s": sum(r["program_real_time_s"] for r in results), "workers": workers,
        "manifest_sha256": manifest["manifest_sha256"], "allocation": allocation}
    write_json(output / "run_metadata.json", metadata)
    summarize(results, manifest, output)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--stage", type=int, choices=(2, 3), required=True)
    prepare.add_argument("--local-limits", required=True, help="Selected P3,P4 limits, for example 3,2")
    prepare.add_argument("--modes", default="normal,normal", help="Stage3 selected P3,P4 sensing modes")
    prepare.add_argument("--nosignal-config", type=Path)
    prepare.add_argument("--calibration", type=Path)
    prepare.add_argument("--mechanisms", type=Path, help="Omit to inherit the last earlier-stage manifest's mechanism list")
    prepare.add_argument("--sizes", default="160,160,80")
    prepare.add_argument("--seed", type=int)
    prepare.add_argument("--previous-manifest", type=Path, action="append", required=True)
    prepare.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--partition", choices=("development", "confirmation"), default="development")
    run.add_argument("--selection", type=Path)
    run.add_argument("--workers", type=int, default=4)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--no-logs", action="store_true")
    selection = sub.add_parser("freeze-selection")
    selection.add_argument("--manifest", type=Path, required=True)
    selection.add_argument("--development-results", type=Path, required=True)
    selection.add_argument("--choices", required=True, help="One preregistered P3,P4 variant, e.g. normal,nosignal")
    selection.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        manifest = prepare_manifest(read(args.calibration) if args.calibration else None,
            stage=args.stage, limits=tuple(map(int, args.local_limits.split(","))),
            previous_manifests=[read(p) for p in args.previous_manifest], sizes=tuple(map(int, args.sizes.split(","))),
            master_seed=args.seed, modes=tuple(args.modes.split(",")), nosignal=read(args.nosignal_config) if args.nosignal_config else None,
            mechanism_candidates=read(args.mechanisms) if args.mechanisms else None)
        write_json(args.output / "manifest.json", manifest)
        result = {k: v for k, v in manifest.items() if k != "scenarios"}
    elif args.command == "run":
        result = execute(read(args.manifest), args.output, partition=args.partition, workers=args.workers,
            resume=args.resume, logs=not args.no_logs, selection=read(args.selection) if args.selection else None)
    else:
        result = create_selection(read(args.manifest), read(args.development_results), tuple(args.choices.split(",")))
        write_json(args.output, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
