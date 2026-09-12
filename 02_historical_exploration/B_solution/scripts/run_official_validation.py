"""Consume a frozen, practice-only two-arm validation plan; never prepare cases.

Preflight and summary are offline. Only the explicit run subcommand starts the
existing visible-UI practice collector. Formal tests are never dispatched.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict, fields
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
sys.path.insert(0, str(ROOT/"scripts"))
import collect_official_round as collector  # noqa: E402
from bsolver.strategy import Solver, SolverConfig  # noqa: E402

CORE_SHA256 = "1b673484453104a5d87d4605d30b20826a3b7ec64769fd6ce8d3c8f7a1ad24f6"
CORE_NAMES = ["__init__.py", "cli.py", "coverage.py", "experiments.py", "geometry.py",
              "knowledge.py", "protocol.py", "sensing.py", "simulator.py", "strategy.py"]
FAILURE_PENALTY_S = 360000.


def read_object(path):
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected an object")
    return value


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def original_baseline(problem):
    return SolverConfig(problem=problem, sensing="active", scheduling="joint", coverage="triangular",
        spacing=None, epsilon_deg=1.0051, clear_radius=19.999, local_measure_limit=5,
        joint_detour_m=800., bin_width_deg=4., radius_weight=2., reserve_real_s=5.,
        max_virtual_s=360000., nearest_safe=True, opportunistic_known_measurements=False)


def normalize_spec(spec, problem, *, baseline=False):
    """Resolve only declared policy parameters; no truth or executable callbacks."""
    if not isinstance(spec, dict):
        raise ValueError("policy_spec must be an object")
    allowed = {field.name for field in fields(SolverConfig)}
    if "solver_config" in spec or "solver_class" in spec or "nosignal_config" in spec:
        if set(spec)-{"solver_class", "solver_config", "nosignal_config"}:
            raise ValueError("unknown normalized policy fields")
        solver_class = spec.get("solver_class", "normal")
        config = spec.get("solver_config", {})
        nosignal = spec.get("nosignal_config", {})
    else:
        solver_class, config, nosignal = "normal", spec, {}
    if solver_class not in ("normal", "nosignal") or not isinstance(config, dict):
        raise ValueError("solver_class must be normal/nosignal with a config object")
    if set(config)-allowed:
        raise ValueError("unknown solver config fields; source/truth fields are forbidden")
    resolved = asdict(original_baseline(problem))
    resolved.update(config)
    if resolved["problem"] != problem:
        raise ValueError("policy problem does not match its assignment")
    if (resolved["sensing"] not in ("active", "fixed", "estimated")
            or resolved["scheduling"] not in ("joint", "immediate", "scan_then_clear")
            or resolved["coverage"] not in ("triangular", "square")
            or isinstance(resolved["local_measure_limit"], bool)
            or not isinstance(resolved["local_measure_limit"], int)
            or not 0 <= resolved["local_measure_limit"] <= 5):
        raise ValueError("unsupported policy mode or uncertified local measurement limit")
    if not 1.0051 <= resolved["epsilon_deg"] <= 1.06 or not 0 < resolved["clear_radius"] <= 19.999:
        raise ValueError("policy violates the retained geometric safety envelope")
    spacing = resolved["spacing"]
    if spacing is not None and spacing not in ((950., 990.) if resolved["coverage"] == "triangular" else (650., 700.)):
        raise ValueError("coverage spacing is outside the retained finite-time proof")
    if resolved["max_virtual_s"] > 360000. or resolved["max_virtual_s"] <= 0:
        raise ValueError("max_virtual_s must be positive and at most the official cap")
    if solver_class == "nosignal":
        from bsolver.nosignal_sensing import NoSignalConfig
        if not isinstance(nosignal, dict) or set(nosignal)-{f.name for f in fields(NoSignalConfig)}:
            raise ValueError("unknown nosignal config fields")
        nosignal = asdict(NoSignalConfig(**nosignal))
        nosignal["radius_samples"] = list(nosignal["radius_samples"])
    elif nosignal:
        raise ValueError("normal solver cannot silently ignore a nosignal config")
    normalized = {"solver_class": solver_class, "solver_config": resolved, "nosignal_config": nosignal}
    if baseline and (solver_class != "normal" or resolved != asdict(original_baseline(problem))):
        raise ValueError("baseline must remain the original joint triangular active L5 policy")
    return normalized


def _project_file(relative, project_root):
    path = (project_root/relative).resolve()
    if not path.is_relative_to(project_root.resolve()) or not path.is_file():
        raise ValueError(f"dependency must be an existing project file: {relative}")
    return path


def verify_baseline(freeze_path, *, project_root=ROOT):
    freeze = read_object(freeze_path)
    required = {f"src/bsolver/{name}" for name in CORE_NAMES}
    if freeze.get("source_sha256") != CORE_SHA256 or not required.issubset(freeze.get("files", {})):
        raise ValueError("original baseline source freeze is missing or not the known core")
    digest = hashlib.sha256()
    for name in CORE_NAMES:
        path = _project_file(f"src/bsolver/{name}", project_root)
        if sha256(path) != freeze["files"][f"src/bsolver/{name}"]:
            raise ValueError(f"frozen core dependency changed: {name}")
        digest.update(name.encode())
        digest.update(path.read_bytes())
    if digest.hexdigest() != CORE_SHA256:
        raise ValueError("aggregate baseline core hash mismatch")
    return freeze


def preflight(round_dir, baseline_freeze, *, candidate_file=None, project_root=ROOT, freeze=True):
    """Validate an existing allocation without changing any plan or assignment."""
    out = Path(round_dir).resolve()
    plan_path = out/"plan.json"
    plan = read_object(plan_path)
    if plan.get("formal_authorized") is not False or plan.get("practice_authorized_by_round_plan") is not True:
        raise ValueError("only the explicitly authorized practice plan can be consumed")
    candidate_path = Path(candidate_file or plan.get("candidate_file", "")).resolve()
    if not candidate_path.is_file() or sha256(candidate_path) != plan.get("candidate_sha256"):
        raise ValueError("frozen candidate file is missing or its SHA-256 changed")
    candidates = read_object(candidate_path)
    if set(candidates.get("problems", {})) != {"3", "4"}:
        raise ValueError("candidate file must contain both problem-specific policy specs")
    candidate_specs = {p: normalize_spec(candidates["problems"][str(p)], p) for p in (3, 4)}
    baseline = verify_baseline(baseline_freeze, project_root=project_root)
    dependencies = candidates.get("dependency_hashes", {})
    if not isinstance(dependencies, dict):
        raise ValueError("candidate dependency_hashes must map project paths to SHA-256")
    if any(spec["solver_class"] == "nosignal" for spec in candidate_specs.values()):
        if "src/bsolver/nosignal_sensing.py" not in dependencies:
            raise ValueError("nosignal candidate requires its frozen module dependency hash")
    for name, digest in dependencies.items():
        if sha256(_project_file(name, project_root)) != digest:
            raise ValueError(f"candidate dependency changed: {name}")
    cases = plan.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("a nonempty existing case allocation is required")
    seen, groups, blocks = set(), {(p, a): 0 for p in (3, 4) for a in ("baseline", "candidate")}, {}
    for sequence, case in enumerate(cases):
        problem, arm, identity = case.get("problem"), case.get("arm"), case.get("case_id")
        if (problem not in (3, 4) or arm not in ("baseline", "candidate")
                or case.get("protocol") != arm or case.get("sequence") != sequence
                or case.get("environment") != "official_practice" or case.get("round") != 2
                or case.get("split") != "validation" or case.get("pilot") is not False):
            raise ValueError("assignment is not a valid frozen round-2 practice allocation")
        if (not isinstance(identity, str) or Path(identity).name != identity
                or any(c in identity for c in "/\\:") or identity in seen):
            raise ValueError("duplicate or unsafe case identifier")
        seen.add(identity)
        assignment = read_object(out/"cases"/identity/"assignment.json")
        if assignment != case:
            raise ValueError(f"per-case assignment differs from frozen plan: {identity}")
        spec = normalize_spec(case.get("policy_spec", {}), problem, baseline=arm == "baseline")
        expected = normalize_spec({}, problem, baseline=True) if arm == "baseline" else candidate_specs[problem]
        if spec != expected:
            raise ValueError(f"case policy differs from frozen arm specification: {identity}")
        groups[(problem, arm)] += 1
        key = (problem, case.get("block"))
        block = blocks.setdefault(key, {"baseline": 0, "candidate": 0})
        block[arm] += 1
    for problem in (3, 4):
        n0, n1 = groups[(problem, "baseline")], groups[(problem, "candidate")]
        if n0 != n1 or not 40 <= n0+n1 <= 60:
            raise ValueError("each problem must have 40..60 balanced, preallocated independent cases")
        size = plan.get("sample_sizes", {}).get(str(problem), {})
        if size.get("planned_total") != n0+n1 or size.get("planned_per_arm") != n0:
            raise ValueError("frozen sample-size calculation differs from allocations")
    if any(block != {"baseline": 2, "candidate": 2} for block in blocks.values()):
        raise ValueError("frozen blocks must contain two assignments per arm")
    runtime_files = ("scripts/run_official_validation.py", "scripts/collect_official_round.py", "scripts/round_ui.ps1")
    runtime_hashes = {name: sha256(_project_file(name, project_root)) for name in runtime_files}
    manifest = {"plan_sha256": sha256(plan_path), "candidate_sha256": sha256(candidate_path),
                "baseline_freeze_sha256": sha256(baseline_freeze), "baseline_source_sha256": CORE_SHA256,
                "candidate_dependency_hashes": dependencies, "runtime_hashes": runtime_hashes,
                "failure_penalty_s": FAILURE_PENALTY_S, "comparison": "independent arms, stratified by problem",
                "case_count": len(cases), "formal_authorized": False}
    manifest_path = out/"validation_freeze.json"
    if manifest_path.exists() and read_object(manifest_path) != manifest:
        raise ValueError("validation freeze changed; do not silently replace it after collection begins")
    target_freeze = out/"baseline_freeze.json"
    if target_freeze.exists() and read_object(target_freeze) != baseline:
        raise ValueError("round baseline freeze conflicts with the original baseline")
    if freeze:
        if not target_freeze.exists():
            collector.write_json(target_freeze, baseline)
        if plan.get("dispatch_ready") is True and not manifest_path.exists():
            collector.write_json(manifest_path, manifest)
    return {"plan": plan, "candidate_file": str(candidate_path), "manifest": manifest,
            "candidate_specs": candidate_specs, "baseline_freeze": baseline}


def run_policy(case, folder, robot_id, base_url, *, client_factory=None):
    """Dependency-injectable policy boundary; only public RobotClient enters policy."""
    from bsolver.protocol import HTTPTransport, RobotClient
    spec = normalize_spec(case["policy_spec"], case["problem"], baseline=case["arm"] == "baseline")
    config = SolverConfig(**spec["solver_config"])
    client = (client_factory() if client_factory is not None else
              RobotClient(robot_id=robot_id, transport=HTTPTransport(base_url), log_path=folder/"requests.jsonl"))
    try:
        if spec["solver_class"] == "nosignal":
            from bsolver.nosignal_sensing import NoSignalConfig, NoSignalSolver
            solver = NoSignalSolver(client, config, decision_log=folder/"decisions.jsonl",
                                     nosignal_config=NoSignalConfig(**spec["nosignal_config"]))
        else:
            solver = Solver(client, config, decision_log=folder/"decisions.jsonl")
        result = solver.run()
    finally:
        client.close_log()
    result.update(environment="official_practice", case_id=case["case_id"], protocol=case["arm"],
                  arm=case["arm"], policy_spec=spec, source_total=None, directional_total=None,
                  case_code=None, baseline_sha256=CORE_SHA256)
    collector.write_json(folder/"result.json", result)
    return result


def run_frozen_plan(round_dir, baseline_freeze, robot_id, *, base_url="http://127.0.0.1:2026",
                    maximum=120, candidate_file=None, collect_module=collector, policy_runner=run_policy):
    if not robot_id or isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
        raise ValueError("robot_id and a positive maximum are required")
    out = Path(round_dir).resolve()
    checked = preflight(out, baseline_freeze, candidate_file=candidate_file)
    if checked["plan"].get("dispatch_ready") is not True:
        raise ValueError("frozen plan is still design-only (dispatch_ready is not true)")
    locks = []
    count = 0
    try:
        # The original collector lock is round-scoped. Hold both this round and
        # the source collection's lock so a resumed round1 cannot dispatch too.
        for directory in sorted({out, Path(baseline_freeze).resolve().parent}, key=str):
            locks.append(collect_module.collection_lock(directory))
        collect_module.status(out, "running", round=2, validation=True)
        collect_module.start_bridge()
        for case in checked["plan"]["cases"]:
            folder = out/"cases"/case["case_id"]
            if (folder/"post_exit_audit.json").exists():
                continue
            if count >= maximum or (out/"STOP_AFTER_CASE").exists():
                break
            # Recheck dependencies before a new UI launch, not only after enter.
            preflight(out, baseline_freeze, candidate_file=candidate_file)
            recovering = (folder/"result.json").exists()
            collect_module.collect({"cases": [case]}, out, robot_id, base_url, 1,
                                   adopt=False, policy_runner=policy_runner)
            if not (folder/"post_exit_audit.json").exists():
                if (out/"STOP_AFTER_CASE").exists():
                    break
                raise RuntimeError("collector returned without an audit; preserve the attempted case")
            if not recovering:
                count += 1
            audit = read_object(folder/"post_exit_audit.json")
            total = audit.get("source_total_post_exit")
            if (audit.get("status") != "complete" or not isinstance(total, int)
                    or not 10 <= total <= 16 or audit.get("cleared") != total):
                raise RuntimeError("validation case incomplete; safety/interface dispatch pause, failure retained")
        collect_module.status(out, "stopped_normally", round=2, new_cases_this_call=count)
    except Exception as exc:
        collect_module.status(out, "needs_attention", round=2, error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        collect_module.close_bridge()
        for lock in reversed(locks):
            lock.close()
    return {"new_cases": count, "planned_cases": len(checked["plan"]["cases"])}


def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    location = fraction*(len(values)-1)
    lo, hi = math.floor(location), math.ceil(location)
    return values[lo]*(hi-location)+values[hi]*(location-lo) if lo != hi else values[lo]


def independent_bootstrap(baseline, candidate, *, seed=92117, repetitions=5000):
    """Candidate-minus-baseline CI: independently resample each arm, never pairs."""
    if not baseline or not candidate:
        return {"difference": None, "ci95": None, "baseline_n": len(baseline), "candidate_n": len(candidate)}
    rng = random.Random(seed)
    values = []
    for _ in range(repetitions):
        a = statistics.fmean(rng.choices(baseline, k=len(baseline)))
        b = statistics.fmean(rng.choices(candidate, k=len(candidate)))
        values.append(b-a)
    return {"difference": statistics.fmean(candidate)-statistics.fmean(baseline),
            "ci95": [percentile(values, .025), percentile(values, .975)],
            "baseline_n": len(baseline), "candidate_n": len(candidate),
            "resampling": "independent within each arm; no same-case official pairing"}


def wilson(failures, total):
    if not total:
        return None
    z = 1.959963984540054
    p, d = failures/total, 1+z*z/total
    c = (p+z*z/(2*total))/d
    r = z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/d
    return [max(0., c-r), min(1., c+r)]


def collect_result_rows(round_dir):
    out = Path(round_dir)
    plan = read_object(out/"plan.json")
    rows = []
    for case in plan["cases"]:
        folder = out/"cases"/case["case_id"]
        result = read_object(folder/"result.json") if (folder/"result.json").exists() else {}
        audit = read_object(folder/"post_exit_audit.json") if (folder/"post_exit_audit.json").exists() else {}
        for document in (result, audit):
            for key, expected in (("case_id", case["case_id"]), ("problem", case["problem"]), ("protocol", case["arm"])):
                if key in document and document[key] != expected:
                    raise ValueError(f"case result/audit metadata mismatch: {case['case_id']} {key}")
        attempted = bool(result or audit or any((folder/name).exists()
                          for name in ("launch_intent.json", "session.json", "requests.jsonl", "start_failure.json")))
        total = audit.get("source_total_post_exit", audit.get("N"))
        directional = audit.get("directional_total_post_exit", audit.get("Ndir"))
        cleared = audit.get("cleared", result.get("clear_successes"))
        status = audit.get("status", result.get("status", "attempt_incomplete" if attempted else "not_started"))
        verified_success = (status == "complete" and isinstance(total, int)
                            and not isinstance(total, bool) and 10 <= total <= 16 and cleared == total)
        components = {}
        required = ("walk_distance_m", "switches", "measures", "clear_attempts", "clear_successes")
        if all(isinstance(result.get(key), (int, float)) for key in required):
            components = {"movement_s": result["walk_distance_m"]/5,
                          "switch_s": result["switches"], "measurement_s": result["measures"]*5,
                          "optical_s": result["clear_attempts"]*3,
                          "laser_s": result["clear_successes"]*2}
        virtual = audit.get("total_virtual_time_s", result.get("total_virtual_time_s"))
        rows.append({"case_id": case["case_id"], "problem": case["problem"], "arm": case["arm"],
                     "attempted": attempted, "status": status, "verified_full_clear": verified_success,
                     "unverified_count": attempted and total is None, "source_total_post_exit": total,
                     "directional_total_post_exit": directional,
                     "cleared": cleared, "total_virtual_time_s": virtual,
                     "time_per_cleared_s": virtual/cleared if virtual is not None and cleared else None,
                     "penalized_loss_s": (virtual if verified_success and virtual is not None else FAILURE_PENALTY_S)
                                           if attempted else None,
                     "components": components, "program_real_time_s": result.get("program_real_time_s"),
                     "component_residual_s": virtual-sum(components.values()) if components and virtual is not None else None})
    return rows


def summarize(round_dir, *, seed=92117, repetitions=5000):
    rows = collect_result_rows(round_dir)
    report = {"comparison": "independent baseline/candidate arms; problems analyzed separately",
              "failure_penalty_s": FAILURE_PENALTY_S, "bootstrap_repetitions": repetitions,
              "bootstrap_seed": seed, "problems": {}, "cases": rows,
              "failure_definition": "attempted without complete status and matching public total/cleared count; unknown audit counts are conservatively unverified failures",
              "missing_not_started_are_failures": False}
    for problem in (3, 4):
        arms = {}
        for arm in ("baseline", "candidate"):
            planned = [r for r in rows if r["problem"] == problem and r["arm"] == arm]
            attempted = [r for r in planned if r["attempted"]]
            complete = [r for r in attempted if r["verified_full_clear"]]
            failed = len(attempted)-len(complete)
            times = [r["total_virtual_time_s"] for r in complete if r["total_virtual_time_s"] is not None]
            counts = [r["source_total_post_exit"] for r in attempted if r["source_total_post_exit"] is not None]
            directed = [r["directional_total_post_exit"] for r in attempted if r["directional_total_post_exit"] is not None]
            fractions = [r["directional_total_post_exit"]/r["source_total_post_exit"] for r in attempted
                         if r["directional_total_post_exit"] is not None and r["source_total_post_exit"]]
            arms[arm] = {"planned": len(planned), "attempted": len(attempted), "not_started": len(planned)-len(attempted),
                         "verified_full_clear": len(complete), "failures_or_unverified": failed,
                         "unknown_post_exit_count": sum(r["unverified_count"] for r in attempted),
                         "failure_rate": failed/len(attempted) if attempted else None,
                         "failure_rate_wilson95": wilson(failed, len(attempted)),
                         "completion_time_mean_s": statistics.fmean(times) if times else None,
                         "completion_time_median_s": statistics.median(times) if times else None,
                         "penalized_loss_mean_s": statistics.fmean(r["penalized_loss_s"] for r in attempted) if attempted else None,
                         "component_means_s": {key: statistics.fmean(r["components"][key] for r in attempted if key in r["components"])
                             for key in ("movement_s", "switch_s", "measurement_s", "optical_s", "laser_s")
                             if any(key in r["components"] for r in attempted)},
                         "component_complete_records": sum(bool(r["components"]) for r in attempted)}
            arms[arm]["composition"] = {"known_N_cases": len(counts), "known_Ndir_cases": len(directed),
                "mean_N": statistics.fmean(counts) if counts else None,
                "mean_Ndir": statistics.fmean(directed) if directed else None,
                "mean_Ndir_fraction": statistics.fmean(fractions) if fractions else None}
        data_by_arm = {arm: [r for r in rows if r["problem"] == problem and r["arm"] == arm and r["attempted"]]
                  for arm in ("baseline", "candidate")}
        differences = {}
        def compare(label, values):
            differences[label] = independent_bootstrap(values["baseline"], values["candidate"],
                                                       seed=seed+problem*100+len(differences), repetitions=repetitions)
        compare("verified_completion_time_s", {arm: [r["total_virtual_time_s"] for r in data
                if r["verified_full_clear"] and r["total_virtual_time_s"] is not None] for arm, data in data_by_arm.items()})
        compare("penalized_loss_s", {arm: [r["penalized_loss_s"] for r in data] for arm, data in data_by_arm.items()})
        compare("failure_rate", {arm: [float(not r["verified_full_clear"]) for r in data] for arm, data in data_by_arm.items()})
        for key in ("movement_s", "switch_s", "measurement_s", "optical_s", "laser_s"):
            compare(key, {arm: [r["components"][key] for r in data if key in r["components"]] for arm, data in data_by_arm.items()})
        p0, p1 = arms["baseline"]["failure_rate"], arms["candidate"]["failure_rate"]
        rate_score_interval = None
        if p0 is not None and p1 is not None:
            l0, u0 = arms["baseline"]["failure_rate_wilson95"]
            l1, u1 = arms["candidate"]["failure_rate_wilson95"]
            rate_score_interval = [max(-1., p1-p0-math.sqrt((p1-l1)**2+(u0-p0)**2)),
                                   min(1., p1-p0+math.sqrt((u1-p1)**2+(p0-l0)**2))]
        report["problems"][str(problem)] = {"arms": arms, "candidate_minus_baseline": differences,
             "failure_rate_difference_score95": rate_score_interval,
             "failure_rate_uncertainty": "Wilson arm intervals and an independent Wilson-score difference interval accompany bootstrap; zero observed failures can make bootstrap degenerate and do not prove zero population risk.",
             "complete_case_time_is_conditional": True,
             "caution": "Failure rates and penalized loss accompany conditional successful-case times; no paired CI, early superiority stopping, or worst-5% reliability claim."}
    out = Path(round_dir)
    collector.write_json(out/"validation_summary.json", report)
    lines = ["# 新官方演练独立两臂汇总", "", "逐题比较，差值均为候选减基线。只对已启动案例统计失败；未启动案例单列。", "",
             "| 问题 | 臂 | 已启动/计划 | 全清确认 | 失败或未验证 | 完成者平均虚拟时间/s |", "|---|---|---:|---:|---:|---:|"]
    for p in (3, 4):
        block = report["problems"][str(p)]
        for arm in ("baseline", "candidate"):
            a = block["arms"][arm]
            mean = "未知" if a["completion_time_mean_s"] is None else f"{a['completion_time_mean_s']:.2f}"
            lines.append(f"| {p} | {arm} | {a['attempted']}/{a['planned']} | {a['verified_full_clear']} | {a['failures_or_unverified']} | {mean} |")
        for label in ("verified_completion_time_s", "penalized_loss_s", "failure_rate"):
            value = block["candidate_minus_baseline"][label]
            lines.extend(["", f"问题 {p}，{label}：差值 {value['difference']}，独立分臂 bootstrap 95% CI {value['ci95']}。"])
    lines.extend(["", "每个失败或未验证尝试的敏感性损失固定为 360000 s。成功者时间属于条件分析；同时报告失败率及全尝试惩罚损失。分量分解与全部案例状态见 JSON。没有同案例配对，不使用配对置信区间。"])
    (out/"validation_summary.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "run", "summarize"):
        p = sub.add_parser(name)
        p.add_argument("--round", type=Path, required=True)
        if name in ("preflight", "run"):
            p.add_argument("--baseline-freeze", type=Path, default=ROOT/"results/round1/baseline_freeze.json")
            p.add_argument("--candidate", type=Path)
        if name == "run":
            p.add_argument("--robot-id", required=True)
            p.add_argument("--base-url", default="http://127.0.0.1:2026")
            p.add_argument("--max-new", type=int, default=120)
        elif name == "summarize":
            p.add_argument("--bootstrap", type=int, default=5000)
            p.add_argument("--seed", type=int, default=92117)
    args = parser.parse_args(argv)
    if args.command == "preflight":
        checked = preflight(args.round, args.baseline_freeze, candidate_file=args.candidate)
        print(json.dumps({"preflight": "passed", "cases": checked["manifest"]["case_count"],
                          "dispatch_ready": checked["plan"].get("dispatch_ready")}, ensure_ascii=False))
    elif args.command == "run":
        print(json.dumps(run_frozen_plan(args.round, args.baseline_freeze, args.robot_id,
                         base_url=args.base_url, maximum=args.max_new, candidate_file=args.candidate), ensure_ascii=False))
    else:
        if args.bootstrap < 100:
            parser.error("use at least 100 independent bootstrap replicates")
        report = summarize(args.round, seed=args.seed, repetitions=args.bootstrap)
        print(json.dumps({"summary": str(args.round/"validation_summary.json"),
                          "cases": len(report["cases"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
