"""Local-only round pipeline: generate, benchmark, run, summarize, validation-design."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import random
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bsolver.round_experiments import (BASELINE, FROZEN_CORE_SHA256, generate_manifest,
    run_manifest, summarize_run, write_json)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig")) if path else None


def validation_design(output, seed=2026091102, cases_per_problem=60, standardized_effect=.5,
                      confirmation_results=None):
    """Design only. Independent official cases cannot be treated as paired replays."""
    if cases_per_problem not in range(40, 61, 2):
        raise ValueError("Use an even total of 40..60 official cases per problem")
    if not math.isfinite(standardized_effect) or standardized_effect <= 0:
        raise ValueError("standardized effect must be finite and positive")
    z_alpha, z_power = statistics.NormalDist().inv_cdf(.975), statistics.NormalDist().inv_cdf(.8)
    per_arm_needed = math.ceil(2 * (z_alpha + z_power) ** 2 / standardized_effect ** 2)
    detectable_d = math.sqrt(2 * (z_alpha + z_power) ** 2 / (cases_per_problem / 2))
    assignments = []
    for problem in (3, 4):
        rng = random.Random(seed + problem)
        for block in range(cases_per_problem // 4):
            arms = ["baseline", "baseline", "candidate", "candidate"]
            rng.shuffle(arms)
            assignments.extend({"problem": problem, "sequence": len([a for a in assignments if a['problem'] == problem]) + 1,
                                "block": block, "arm": arm, "case_reuse": False} for arm in arms)
        if cases_per_problem % 4:
            arms = ["baseline", "candidate"]
            rng.shuffle(arms)
            for arm in arms:
                assignments.append({"problem": problem, "sequence": sum(a["problem"] == problem for a in assignments) + 1,
                                    "block": cases_per_problem // 4, "arm": arm, "case_reuse": False})
    estimates = []
    if confirmation_results:
        rows = load(confirmation_results)
        for problem in (3, 4):
            values = [r["failure_penalized_time_s"] for r in rows if r["problem"] == problem
                      and r["variant"] == BASELINE and r["partition"] == "confirmation"]
            if len(values) >= 2:
                mean, sd = statistics.fmean(values), statistics.stdev(values)
                estimates.append({"problem": problem, "n_synthetic": len(values), "sd_s": sd,
                    "mean_s": mean, "detectable_delta_s_at_planned_n": detectable_d * sd,
                    "detectable_fraction_of_mean": detectable_d * sd / mean,
                    "status": "synthetic planning proxy, not official variance estimate"})
    result = {"scope": "official practice validation design only; no requests sent",
        "formal_authorized": False, "practice_validation_in_authorized_plan": True, "dispatch_ready": False,
        "candidate_must_be_frozen_before_first_assignment": True, "max_candidates": 1,
        "seed": seed, "cases_per_problem_total": cases_per_problem, "cases_per_arm_per_problem": cases_per_problem // 2,
        "allocation": "independent fresh official cases, equal arms, randomized blocks of four",
        "primary": "completion rate first; prespecified 360000-second failure-penalized virtual time second",
        "inference": "independent-arm intervals, never local paired intervals; problem-specific estimates",
        "power_assumption": "normal approximation, equal arm variance, two-sided alpha=.05, power=.80; not powered for rare failures",
        "requested_standardized_mean_effect": standardized_effect, "required_per_arm": per_arm_needed,
        "minimum_detectable_standardized_effect_at_planned_n": detectable_d,
        "zero_failure_one_sided_95pct_rate_upper_bound_per_arm": 1 - .05 ** (1 / (cases_per_problem // 2)),
        "synthetic_planning_proxies": estimates, "assignments": assignments}
    output = Path(output)
    write_json(output / "official_validation_design.json", result)
    text = f"""# 下一轮独立官方验证设计（只生成方案）

每问共{cases_per_problem}个独立新案例，基线/冻结候选各{cases_per_problem//2}例，按4例一块预先随机分派；同一官方场景不可假定能够重放，因此不用本地的配对检验。候选最多1个，随机序列在任何新案例结果出现前固定。

先看完整率，再看预注册失败惩罚360000秒的虚拟时间；成功案例时间作为补充。来源、真实接口、案例ID和未成功尝试都留存，不能只保留成功请求。按P3/P4单独报告，不能中途看效果就换候选。

双侧alpha=0.05、80%功效、独立两组同方差正态近似下，每臂 n≈2(z_0.975+z_0.8)²/d²。检测标准化差 d={standardized_effect} 约需每臂{per_arm_needed}例；当前每臂{cases_per_problem//2}例仅能可靠检测约 d={detectable_d:.3f} 的大差异。若{cases_per_problem//2}例零失败，单臂失败率的单侧95%上界仍为{100*result['zero_failure_one_sided_95pct_rate_upper_bound_per_arm']:.2f}%，不能证明低失败率或非劣性。

合成确认集方差可作为粗略预算代理，不能当成官方方差。应在已有独立官方资料可估计方差时冻结目标差值/功效计算；若40–60每问不够，应报告精度不足而不是许诺显著改进。

新的官方演练验证已在用户本轮计划范围内，尚未执行；依赖候选与样本量预先冻结，再由主执行器调度。正式测试仍未授权。程序只生成JSON随机分派表，不打开模拟器、不发送HTTP。
"""
    (output / "official_validation_design.md").write_text(text, encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate")
    generate.add_argument("--calibration", type=Path)
    generate.add_argument("--sizes", default="400,400,200")
    generate.add_argument("--seed", type=int, default=2026091101)
    generate.add_argument("--models", default="empirical_joint,smoothed_joint")
    generate.add_argument("--mechanisms", type=Path, help="Preregistered retained/unresolved mechanism candidate list JSON")
    generate.add_argument("--output", type=Path, required=True)
    bench = sub.add_parser("bench")
    bench.add_argument("--output", type=Path, required=True)
    bench.add_argument("--cases-per-problem", type=int, default=2)
    bench.add_argument("--workers", type=int, default=2)
    bench.add_argument("--seed", type=int, default=2026091199)
    run = sub.add_parser("run")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--workers", type=int, default=4)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--no-logs", action="store_true")
    run.add_argument("--max-cases", type=int)
    summary = sub.add_parser("summarize")
    summary.add_argument("--output", type=Path, required=True)
    summary.add_argument("--bootstrap-draws", type=int, default=2000)
    design = sub.add_parser("validation-design")
    design.add_argument("--output", type=Path, required=True)
    design.add_argument("--cases-per-problem", type=int, default=60)
    design.add_argument("--standardized-effect", type=float, default=.5)
    design.add_argument("--confirmation-results", type=Path)
    args = parser.parse_args()
    if args.command == "generate":
        manifest = generate_manifest(load(args.calibration), sizes=tuple(map(int, args.sizes.split(","))),
                                     master_seed=args.seed, calibrated_models=tuple(args.models.split(",")),
                                     mechanism_candidates=load(args.mechanisms))
        write_json(args.output / "manifest.json", manifest)
        result = {k: v for k, v in manifest.items() if k != "scenarios"}
    elif args.command == "bench":
        if args.cases_per_problem < 1:
            raise ValueError("Benchmark requires at least one case per problem")
        manifest = generate_manifest(sizes=(0, args.cases_per_problem * 2, 0), master_seed=args.seed)
        rows, timing = run_manifest(manifest, args.output, workers=args.workers)
        result = summarize_run(rows, args.output, bootstrap_draws=400)
        timing["rough_1000_scenario_wall_s"] = timing["wall_time_s"] * 1000 / timing["distinct_scenarios_run"]
        timing["projection_warning"] = "small broad-only benchmark; startup, logs and hard calibration/stress cases may alter scaling"
        write_json(args.output / "benchmark.json", timing)
        result["timing"] = timing
    elif args.command == "run":
        rows, timing = run_manifest(load(args.manifest), args.output, workers=args.workers,
                                   resume=args.resume, logs=not args.no_logs, max_cases=args.max_cases)
        result = summarize_run(rows, args.output)
        result["timing"] = timing
    elif args.command == "summarize":
        result = summarize_run(load(args.output / "results.json"), args.output, args.bootstrap_draws)
    else:
        result = validation_design(args.output, cases_per_problem=args.cases_per_problem,
            standardized_effect=args.standardized_effect, confirmation_results=args.confirmation_results)
        result = {k: v for k, v in result.items() if k != "assignments"}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
