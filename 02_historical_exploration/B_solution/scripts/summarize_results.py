"""Offline, truth-aware evaluation only; this module never invokes a policy.

Run: python scripts/summarize_results.py [--results PATH] [--no-plots]
Read completed split results.json files. Missing/in-progress splits are recorded,
not reconstructed from partial CSV files. Every failed case remains in outputs.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
VARIANTS = ["baseline", "active", "nearest_safe", "immediate", "joint_square", "joint_triangular"]
ABLATIONS = [("baseline", "active"), ("active", "nearest_safe"),
             ("nearest_safe", "immediate"), ("nearest_safe", "joint_square"),
             ("joint_square", "joint_triangular"), ("baseline", "joint_triangular")]
METRICS = ["total_virtual_time_s", "average_clear_time_s", "walk_distance_m",
           "movement_time_s", "measure_time_s", "switch_time_s", "optical_time_s",
           "laser_time_s", "measures", "switches", "clear_attempts", "clear_successes",
           "fallback_targets", "program_real_time_s", "clear_fraction", "timing_residual_s"]


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def quantile(values, q):
    """Linear interpolation, the usual type-7 empirical quantile."""
    values = sorted(values)
    if not values:
        return None
    position = (len(values) - 1) * q
    lo = math.floor(position)
    hi = math.ceil(position)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def stats(values):
    values = [float(x) for x in values if number(x)]
    return {"n": len(values), "mean": statistics.fmean(values) if values else None,
            "median": statistics.median(values) if values else None,
            "p95": quantile(values, .95), "max": max(values) if values else None,
            "min": min(values) if values else None}


def external_complete(row):
    truth = row.get("environment_summary", {})
    if "all_cleared" in truth:
        return bool(truth["all_cleared"])
    fraction = row.get("clear_fraction")
    return fraction >= 1 - 1e-12 if number(fraction) else None


def complete(row):
    return (row.get("status") == "complete" and external_complete(row) is True
            and not row.get("error") and not row.get("hull_invariant_violations"))


def normalize(row, split):
    row = dict(row)
    row["split"] = split
    row["evaluation_complete"] = complete(row)
    row["externally_all_cleared"] = external_complete(row)
    for destination, source, factor in [
        ("movement_time_s", "walk_distance_m", .2), ("measure_time_s", "measures", 5),
        ("switch_time_s", "switches", 1), ("optical_time_s", "clear_attempts", 3),
        ("laser_time_s", "clear_successes", 2)]:
        row[destination] = row[source] * factor if number(row.get(source)) else None
    return row


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def write_csv(path, rows, fields=None):
    if fields is None:
        fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                             for k, v in row.items() if k in fields})


def grouped_summary(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["split"], row["problem"], row["variant"]].append(row)
    output = []
    for (split, problem, variant), group in sorted(groups.items()):
        n = len(group)
        passed = sum(x["evaluation_complete"] for x in group)
        external = [x["externally_all_cleared"] for x in group if x["externally_all_cleared"] is not None]
        success_counts = [x.get("clear_successes") for x in group]
        successes = sum(success_counts) if all(number(x) for x in success_counts) else None
        total_times = [x.get("total_virtual_time_s") for x in group]
        item = {"split": split, "problem": problem, "variant": variant, "n_cases": n,
                "complete_count": passed, "failed_count": n - passed,
                "complete_rate": passed / n, "failure_rate": 1 - passed / n,
                "externally_all_cleared_rate": sum(external) / len(external) if external else None,
                "external_truth_n": len(external),
                "hull_invariant_violations": sum(len(x.get("hull_invariant_violations", [])) for x in group),
                "status_counts": dict(Counter(x.get("status", "missing") for x in group)),
                "code_sha256": sorted(set(x.get("code_sha256", "unknown") for x in group)),
                "pooled_time_per_cleared_source_s": (sum(total_times) / successes
                    if successes and all(number(x) for x in total_times) else None)}
        for metric in METRICS:
            for key, value in stats(x.get(metric) for x in group).items():
                item[f"{metric}_{key}"] = value
        output.append(item)
    return output


def paired_ablation(rows):
    index = {(r["split"], r["problem"], r["case_id"], r["variant"]): r for r in rows}
    pairs = []
    for row in rows:
        for base, candidate in ABLATIONS:
            if row["variant"] != base:
                continue
            other = index.get((row["split"], row["problem"], row["case_id"], candidate))
            if other is None:
                continue
            pair = {"split": row["split"], "problem": row["problem"], "case_id": row["case_id"],
                    "baseline_variant": base, "candidate_variant": candidate,
                    "baseline_complete": row["evaluation_complete"], "candidate_complete": other["evaluation_complete"],
                    "both_complete": row["evaluation_complete"] and other["evaluation_complete"],
                    "completion_delta": int(other["evaluation_complete"]) - int(row["evaluation_complete"]),
                    "baseline_code_sha256": row.get("code_sha256"),
                    "candidate_code_sha256": other.get("code_sha256"),
                    "baseline_virtual_time_s": row.get("total_virtual_time_s"),
                    "candidate_virtual_time_s": other.get("total_virtual_time_s")}
            for metric in METRICS:
                a, b = row.get(metric), other.get(metric)
                pair[f"delta_{metric}"] = b - a if number(a) and number(b) else None
            a, b = row.get("total_virtual_time_s"), other.get("total_virtual_time_s")
            pair["time_reduction_pct"] = 100 * (a - b) / a if number(a) and a > 0 and number(b) else None
            pairs.append(pair)
    groups = defaultdict(list)
    for pair in pairs:
        groups[pair["split"], pair["problem"], pair["baseline_variant"], pair["candidate_variant"]].append(pair)
    summaries = []
    for (split, problem, base, candidate), group in sorted(groups.items()):
        item = {"split": split, "problem": problem, "baseline_variant": base,
                "candidate_variant": candidate, "n_pairs": len(group),
                "n_both_complete": sum(x["both_complete"] for x in group),
                "completion_delta_mean": statistics.fmean(x["completion_delta"] for x in group)}
        for subset, selected in [("all_pairs", group), ("both_complete", [x for x in group if x["both_complete"]])]:
            for metric in ["delta_" + m for m in METRICS] + ["time_reduction_pct"]:
                for key, value in stats(x.get(metric) for x in selected).items():
                    item[f"{subset}_{metric}_{key}"] = value
        summaries.append(item)
    return pairs, summaries


def formatted(value, digits=2):
    return f"{value:.{digits}f}" if number(value) else "NA"


def markdown(summary, paired, metadata):
    lines = ["# 自建环境实验汇总", "", f"生成时间（UTC）：{metadata['generated_at_utc']}", "",
        "仅读取 tune/test/stress 的完整 results.json；缺失分组跳过并记录。官方练习不混入这些统计。",
        "完整案例要求 status=complete、外部真值全部清除、无错误且无后验真值丢失。失败率是其补集；所有时长统计保留失败案例，不能把提前失败造成的短时间解释为改进。",
        "平均清除时间采用每案例总虚拟时间/清除成功数，再对案例平均；JSON/CSV 另给汇总总时间/总成功数。未知量保留为空，每指标 n 单独报告。p95 使用线性插值经验分位数。",
        "时间分解 = 路程/5 + 检测数×5 + 切换数×1 + 光学尝试数×3 + 成功数×2。运行时间为自建环境本地程序计时，不是官方服务器运行时间或并行作业吞吐。",
        "轨迹图中的源真值只由本离线评估脚本读取，用于画图和核验；没有传入策略。", "",
        "## 全案例统计", "", "|分组|题号|变体|案例数|完整率|失败数|均值 s|中位数 s|p95 s|最大 s|均清除 s|路程 m|检测|切换|光学尝试|运行 s|",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in summary:
        values = [row["split"], str(row["problem"]), row["variant"], str(row["n_cases"]),
                  formatted(100 * row["complete_rate"]) + "%", str(row["failed_count"])]
        values += [formatted(row[key]) for key in ["total_virtual_time_s_mean", "total_virtual_time_s_median",
            "total_virtual_time_s_p95", "total_virtual_time_s_max", "average_clear_time_s_mean", "walk_distance_m_mean",
            "measures_mean", "switches_mean", "clear_attempts_mean", "program_real_time_s_mean"]]
        lines.append("|" + "|".join(values) + "|")
    lines += ["", "## 配对消融", "", "同题、同分组、同 case_id 配对。delta=候选−基线，负时长差表示更快；时间减少率=(基线−候选)/基线。JSON/CSV 保留每一对及全部指标。不同代码散列会记录，不能自动视作严格同版本消融。",
        "baseline→active 只改变测点选择；active→nearest_safe 改清除位置；nearest_safe→immediate/joint_square 改调度；joint_square→joint_triangular 改覆盖。P3 的覆盖构造相同，末项应无策略差异。",
        "", "|分组|题号|基线→候选|配对数|双完整数|完整率差|全对均时长差 s|双完整均时长差 s|双完整平均减少率|",
        "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for row in paired:
        values = [row["split"], str(row["problem"]), row["baseline_variant"] + "→" + row["candidate_variant"],
                  str(row["n_pairs"]), str(row["n_both_complete"]), formatted(row["completion_delta_mean"]),
                  formatted(row["all_pairs_delta_total_virtual_time_s_mean"]),
                  formatted(row["both_complete_delta_total_virtual_time_s_mean"]),
                  formatted(row["both_complete_time_reduction_pct_mean"]) + "%"]
        lines.append("|" + "|".join(values) + "|")
    audit = metadata.get("version_audit")
    if audit:
        lines += ["", "## 版本说明", "",
            f"原始{audit['original_experiment_rows']}条实验保留源散列 `{audit['original_code_sha256']}`。最终交付源为 `{audit['final_code_sha256']}`，仅新增请求前日志与非法测角分箱参数检查。",
            f"另用最终源重跑首个P3/P4测试场景各6变体，共{audit['regression_rows']}次：结果字段全部相同={audit['all_result_fields_equal']}，完整动作坐标/响应序列全部相同={audit['all_action_sequences_equal']}。不比较runtime。",
            "这12次是版本回归，不混入本页192条效果统计，也不替换原结果的散列。两版源ZIP、逐行差异和逐案例证据见 `docs/version_audit.md`、`results/final_regression/version_audit.json`。"]
    lines += ["", "## 数据范围和限制", "", "```json", json.dumps(metadata, ensure_ascii=False, indent=2), "```", "",
        "合成场景规模有限，经验完整率不是连续域保证；理论覆盖与有限终止证明单独见 docs/coverage_proofs.md 和 docs/termination_bound.md。",
        "6.91 h 对应三角950覆盖、首次方向观测后直接光学兜底的基线；当前最多5次局部测量的自适应实现需使用独立预算证明，通用保守上界约65.156 h（默认范围内）。",
        "样例轨迹按 test→tune→stress、joint_triangular→joint_square→其余变体、case_id 字典序确定；不按最好时间挑选。失败样例同样保留。图仅示例，不构成统计检验。", ""]
    return "\n".join(lines)


def plot_results(rows, pairs, result_root):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.patches import Circle, Polygon
    from bsolver.coverage import coverage_certificate, directional_points, omnidirectional_points, order_route, route_length
    from bsolver.geometry import minimum_enclosing_circle
    figures = result_root / "figures"
    figures.mkdir(exist_ok=True)
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                         "savefig.dpi": 160})
    produced = []

    def save(fig, name):
        fig.savefig(figures / name, bbox_inches="tight")
        plt.close(fig)
        produced.append(name)

    for split in sorted({r["split"] for r in rows}):
        selected = [r for r in rows if r["split"] == split]
        fig, axes = plt.subplots(1, 2, figsize=(13, 5), constrained_layout=True)
        for ax, problem in zip(axes, [3, 4]):
            variants = [v for v in VARIANTS if any(r["problem"] == problem and r["variant"] == v for r in selected)]
            x, bottom = np.arange(len(variants)), np.zeros(len(variants))
            for metric, label, color in [("movement_time_s", "Movement", "#2563eb"),
                ("measure_time_s", "Measure", "#f59e0b"), ("switch_time_s", "Switch", "#8b5cf6"),
                ("optical_time_s", "Optical attempts", "#ef4444"), ("laser_time_s", "Laser", "#10b981")]:
                values = [stats(r.get(metric) for r in selected if r["problem"] == problem and r["variant"] == v)["mean"] for v in variants]
                values = np.asarray([np.nan if v is None else v / 3600 for v in values])
                ax.bar(x, values, bottom=bottom, label=label, color=color)
                bottom += values
            for index, variant in enumerate(variants):
                group = [r for r in selected if r["problem"] == problem and r["variant"] == variant]
                ax.text(index, bottom[index], f"{sum(r['evaluation_complete'] for r in group)}/{len(group)}", ha="center", va="bottom", fontsize=8)
            ax.set_xticks(x, [v.replace("_", "\n") for v in variants])
            ax.set_title(f"P{problem}: complete / all cases above bars")
            ax.set_ylabel("All-case mean virtual time (h)")
            ax.margins(y=.18)
            ax.grid(axis="y", alpha=.18)
        axes[0].legend(fontsize=8)
        fig.suptitle(f"{split}: independent virtual-time accounting (failures retained)")
        save(fig, f"time_breakdown_{split}.png")

        fig, axes = plt.subplots(2, 3, figsize=(12, 8), constrained_layout=True)
        for ax, (base, candidate) in zip(axes.flat, ABLATIONS):
            group = [p for p in pairs if p["split"] == split and p["baseline_variant"] == base and p["candidate_variant"] == candidate]
            limit = 1.0
            for problem, marker, color in [(3, "o", "#2563eb"), (4, "^", "#d97706")]:
                for okay in [True, False]:
                    points = [p for p in group if p["problem"] == problem and p["both_complete"] == okay
                              and number(p["baseline_virtual_time_s"]) and number(p["candidate_virtual_time_s"])]
                    xs = [p["baseline_virtual_time_s"] / 3600 for p in points]
                    ys = [p["candidate_virtual_time_s"] / 3600 for p in points]
                    if points:
                        ax.scatter(xs, ys, marker=marker if okay else "x", color=color if okay else "red",
                                   label=f"P{problem}" + ("" if okay else " failure"), alpha=.8)
                        limit = max(limit, *xs, *ys)
            ax.plot([0, limit * 1.05], [0, limit * 1.05], "--", color=".5", linewidth=1)
            ax.set(xlabel=base + " (h)", ylabel=candidate + " (h)", xlim=(0, limit * 1.08), ylim=(0, limit * 1.08))
            ax.set_aspect("equal")
            if group:
                ax.legend(fontsize=8)
            else:
                ax.text(.5, .5, "No paired cases", transform=ax.transAxes, ha="center")
        fig.suptitle(f"{split}: paired virtual time; points below diagonal are faster")
        save(fig, f"paired_time_{split}.png")

    # Truth is read only below, after all strategy runs have already finished.
    trace_manifest = []
    for problem in [3, 4]:
        choices = sorted([r for r in rows if r["problem"] == problem], key=lambda r: (
            {"test": 0, "tune": 1, "stress": 2}.get(r["split"], 3),
            {"joint_triangular": 0, "joint_square": 1}.get(r["variant"], 2), r["case_id"], r["variant"]))
        for row in choices:
            directory = result_root / row["split"] / row["variant"] / row["case_id"]
            if (directory / "case.json").exists() and (directory / "decisions.jsonl").exists():
                break
        else:
            continue
        case = json.loads((directory / "case.json").read_text(encoding="utf-8"))
        events = [json.loads(line) for line in (directory / "decisions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        trace = []
        for event in events:
            p = event.get("position")
            if p and (not trace or p != trace[-1]):
                trace.append(p)
        fig, ax = plt.subplots(figsize=(8, 8), constrained_layout=True)
        ax.add_patch(Circle((0, 0), 1800, fill=False, color=".2", linestyle="--", label="Source domain"))
        if trace:
            ax.plot(*zip(*trace), color="#2563eb", linewidth=.8, alpha=.65, label="Robot trajectory")
            ax.scatter(*trace[0], c="black", marker="s", s=45, label="Start", zorder=5)
            ax.scatter(*trace[-1], c="#2563eb", marker="D", s=45, label="Finish", zorder=5)
        clears = [e for e in events if e["event"] == "clear" and e.get("response", {}).get("clear_result") == "success"]
        if clears:
            ax.scatter(*zip(*(e["position"] for e in clears)), c="#10b981", marker="+", s=75, label="Successful clear", zorder=6)
        for source in case["sources"]:
            x, y = source["position"]
            ax.scatter(x, y, c="#b91c1c", marker="*", s=70, zorder=5)
            ax.annotate(str(source["channel"]), (x, y), xytext=(5, 5), textcoords="offset points", fontsize=8)
            angle = source.get("direction_deg")
            if angle is not None:
                theta = math.radians(angle)
                ax.arrow(x, y, 210 * math.cos(theta), 210 * math.sin(theta), width=7, color="#b91c1c", alpha=.6)
        ax.scatter([], [], c="#b91c1c", marker="*", label="Hidden truth (evaluation only)")
        ax.set(xlabel="x (m)", ylabel="y (m)", title=f"P{problem} | {row['variant']} | {row['case_id']}\n"
               f"{row['status']} | {row['total_virtual_time_s']/3600:.3f} virtual h")
        ax.set_aspect("equal")
        ax.legend(loc="best", fontsize=8)
        ax.grid(alpha=.15)
        save(fig, f"trajectory_p{problem}.png")

        observed = [e for e in events if e.get("knowledge", {}).get("positive_count", 0) > 0 and e.get("knowledge", {}).get("hull")]
        if not observed:
            continue
        channel = observed[0]["knowledge"]["channel"]
        snapshots = [e for e in events if e.get("knowledge", {}).get("channel") == channel and e.get("knowledge", {}).get("hull")]
        snapshots = [e for i, e in enumerate(snapshots) if i == 0 or e["knowledge"]["hull"] != snapshots[i - 1]["knowledge"]["hull"]]
        truth = next((s["position"] for s in case["sources"] if s["channel"] == channel), None)
        fig, axes = plt.subplots(1, 3, figsize=(14, 4.5), constrained_layout=True)
        colors = plt.cm.viridis(np.linspace(.15, .85, len(snapshots)))
        areas, radii = [], []
        for event, color in zip(snapshots, colors):
            hull = event["knowledge"]["hull"]
            areas.append(abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(hull, hull[1:] + hull[:1]))) / 2)
            radii.append(minimum_enclosing_circle(hull)[1])
            axes[0].add_patch(Polygon(hull, facecolor=color, edgecolor=color, alpha=.2))
            axes[0].plot(*zip(*(hull + hull[:1])), color=color, linewidth=.8)
        last = snapshots[-1]["knowledge"]["hull"]
        axes[1].add_patch(Polygon(last, facecolor=colors[-1], edgecolor=colors[-1], alpha=.25))
        center, radius = minimum_enclosing_circle(last)
        axes[1].add_patch(Circle(center, radius, fill=False, color="#2563eb", linestyle="--"))
        if truth:
            for ax in axes[:2]:
                ax.scatter(*truth, c="#b91c1c", marker="*", s=85, label="Truth (offline)", zorder=5)
        for ax in axes[:2]:
            ax.autoscale_view()
            ax.margins(.1)
            ax.set_aspect("equal", adjustable="datalim")
            ax.set(xlabel="x (m)", ylabel="y (m)")
            ax.grid(alpha=.15)
        axes[0].set_title("Nested outer hulls: first to final")
        axes[1].set_title(f"Final hull and enclosing circle\nradius = {radius:.3f} m")
        indices = range(1, len(snapshots) + 1)
        initial_area, initial_radius = max(areas[0], 1e-12), max(radii[0], 1e-12)
        axes[2].semilogy(indices, [max(a, 1e-12) / initial_area for a in areas], "o-", c="#2563eb", label="Outer-hull area / initial area")
        axes[2].semilogy(indices, [max(r, 1e-12) / initial_radius for r in radii], "s--", c="#d97706", label="MEC radius / initial radius")
        axes[2].axhline(20 / initial_radius, color="#d97706", alpha=.4, linewidth=1, label="20 m / initial radius")
        axes[2].set(xlabel="Distinct posterior update", ylabel="Fraction of first posterior (log scale)")
        axes[2].set_xticks(list(indices))
        axes[2].legend(fontsize=8)
        axes[2].grid(alpha=.15)
        axes[2].set_title("Conservative region contraction")
        fig.suptitle(f"P{problem}: first positively detected channel {channel} | {row['variant']}")
        save(fig, f"region_contraction_p{problem}.png")
        trace_manifest.append({"problem": problem, "split": row["split"], "variant": row["variant"], "case_id": row["case_id"],
            "selected_channel": channel, "selection_rule": "first positive channel; fixed split/variant/case ordering",
            "distinct_updates": len(snapshots), "outer_hull_areas_m2": areas, "enclosing_radii_m": radii,
            "source_of_truth": str(directory / "case.json"), "policy_receives_truth": False})

    fig, axes = plt.subplots(1, 3, figsize=(15, 5), constrained_layout=True)
    certificates = []
    for ax, kind in zip(axes, ["omnidirectional", "square", "triangular"]):
        points = omnidirectional_points() if kind == "omnidirectional" else directional_points(kind)
        route = order_route(points)
        certificate = coverage_certificate(kind)
        certificate["open_route_length_m"] = route_length(route)
        certificates.append(certificate)
        ax.add_patch(Circle((0, 0), 1800, facecolor="#e0e7ff", edgecolor="black", alpha=.25))
        if kind == "omnidirectional":
            for p in points:
                ax.add_patch(Circle(p, 900, fill=False, color="#2563eb", alpha=.4, linewidth=.8))
        else:
            # Lattice edges illustrate geometry; the analytic certificate is authoritative.
            spacing = certificate["spacing_m"]
            for i, a in enumerate(points):
                for b in points[i + 1:]:
                    if abs(math.dist(a, b) - spacing) < 1e-6:
                        ax.plot([a[0], b[0]], [a[1], b[1]], c=".65", linewidth=.7)
        ax.plot(*zip(*([(0, 0)] + route)), color="#d97706", linewidth=1.2, alpha=.7)
        ax.scatter(*zip(*points), color="#2563eb", s=20, zorder=4)
        ax.scatter(0, 0, color="black", marker="s", s=40, zorder=5)
        ax.set_aspect("equal")
        ax.set(xlabel="x (m)", ylabel="y (m)", title=f"{'P3' if kind == 'omnidirectional' else 'P4'} {kind}\n"
            f"{len(points)} scan points | route {route_length(route)/1000:.2f} km")
        ax.grid(alpha=.12)
    fig.suptitle("Continuous-domain discovery constructions; orange = open NN + 2-opt route")
    save(fig, "coverage_q3_q4.png")
    write_json(figures / "plot_manifest.json", {"figures": produced, "trace_examples": trace_manifest,
                                               "coverage_certificates": certificates})
    return produced


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=ROOT / "results")
    parser.add_argument("--splits", default="tune,test,stress")
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args(argv)
    rows, inputs, skipped = [], [], []
    for split in args.splits.split(","):
        path = args.results / split / "results.json"
        if not path.exists():
            skipped.append({"split": split, "reason": "results.json not present; may still be running"})
            continue
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, list):
            raise ValueError(f"{path} must contain a list")
        if not data:
            skipped.append({"split": split, "reason": "empty results list"})
            continue
        inputs.append({"path": str(path), "case_variant_rows": len(data), "modified_at_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()})
        rows.extend(normalize(row, split) for row in data)
    keys = [(r["split"], r["problem"], r["case_id"], r["variant"]) for r in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate split/problem/case/variant keys; refusing implicit reweighting")
    summary = grouped_summary(rows)
    pairs, paired = paired_ablation(rows)
    metadata = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "inputs": inputs, "skipped": skipped,
                "total_case_variant_rows": len(rows), "total_failures": sum(not r["evaluation_complete"] for r in rows),
                "timing_population": "all cases, failures retained", "delta_sign": "candidate minus baseline",
                "paired_matching": "split, problem, case_id", "data_environments": sorted(set(r.get("environment", "unknown") for r in rows)),
                "code_sha256": sorted(set(r.get("code_sha256", "unknown") for r in rows)),
                "no_truth_in_policy": "This is an offline evaluator; truth is read only for completion evaluation and plots."}
    audit_path = args.results / "final_regression/version_audit.json"
    if audit_path.exists():
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        metadata["version_audit"] = {key: audit[key] for key in ["original_code_sha256", "final_code_sha256",
            "original_experiment_rows", "regression_rows", "all_result_fields_equal", "all_action_sequences_equal"]}
    output = args.results / "summary"
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "summary.json", {"metadata": metadata, "groups": summary, "paired_ablations": paired})
    write_csv(output / "summary.csv", summary, fields=None if summary else ["split", "problem", "variant", "n_cases"])
    write_json(output / "paired_cases.json", pairs)
    write_csv(output / "paired_cases.csv", pairs, fields=None if pairs else ["split", "problem", "case_id"])
    write_csv(output / "paired_ablations.csv", paired, fields=None if paired else ["split", "problem", "baseline_variant", "candidate_variant"])
    failures = [r for r in rows if not r["evaluation_complete"]]
    write_json(output / "failures.json", failures)
    write_csv(output / "failures.csv", failures, fields=["split", "problem", "variant", "case_id", "status", "error",
              "evaluation_complete", "externally_all_cleared", "source_total"] + METRICS + ["hull_invariant_violations", "code_sha256"])
    write_csv(output / "all_cases.csv", rows, fields=["split", "problem", "variant", "case_id", "status", "error",
              "evaluation_complete", "externally_all_cleared", "source_total"] + METRICS + ["hull_invariant_violations", "code_sha256"])
    (output / "summary.md").write_text(markdown(summary, paired, metadata), encoding="utf-8")
    figures = plot_results(rows, pairs, args.results) if not args.no_plots else []
    print(json.dumps({"output": str(output), "rows": len(rows), "groups": len(summary), "pairs": len(pairs),
                      "failures": len(failures), "skipped": skipped, "figures": figures}, ensure_ascii=False))


if __name__ == "__main__":
    main()
