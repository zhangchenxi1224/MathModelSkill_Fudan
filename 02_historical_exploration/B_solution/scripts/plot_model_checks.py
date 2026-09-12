"""Data-only figures for same-protocol official/local mechanism diagnostics.

The declare subcommand fixes metric families without opening any result file.
Plot consumes already-computed whole-case intervals; it fits no model, selects
no successful subset, and never reads request journals or hidden scenarios.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
DESIGN_VERSION = "same-protocol-data-only-figures-v1"
CORE = [
    ("completion_fraction", "Completion fraction", "fraction"),
    ("complete_only_total_virtual_time_s", "Virtual time: completed cases", "seconds"),
    ("complete_only_failed_clears", "Failed clears: completed cases", "attempts"),
    ("@origin", "Visibility at the origin, before clearance", "fraction of all 20 channels"),
    ("same_point_repeat_absolute_report_delta_deg", "Same-point absolute report change", "degrees; observable proxy"),
    ("nearest_observed_point_absolute_report_delta_deg", "Nearest observed point (<=50 m) report change", "degrees; includes geometry"),
    ("coverage_mean_hull_area_ratio", "Coverage: mean hull area ratio", "ratio; algorithmic proxy"),
    ("survey_mean_hull_area_ratio", "Added survey: mean hull area ratio", "ratio; algorithmic proxy"),
    ("survey_no_signal_fraction", "Added survey: no-signal fraction", "fraction"),
    ("@joint_mean", "Mean visible-station fraction", "fraction among confirmed sources"),
]
PATTERNS = {
    "origin": r"^coverage_station_.+@-?0\.0000,-?0\.0000_visibility$",
    "joint_mean": r"^joint_([^_]+)_mean_visible_station_fraction$",
    "joint_pair": r"^joint_([^_]+)_pair_(\d+)_(\d+)_(both_visible|discordant)_probability$",
    "joint_count": r"^joint_([^_]+)_visible_count_probability_(\d+)$",
}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def design():
    return {"version": DESIGN_VERSION, "fixed_core": [list(row) for row in CORE],
            "metric_name_patterns": PATTERNS,
            "joint_pair_selection": "all pairs already selected geometrically by the schema; no feedback-based choice",
            "joint_count_selection": "all available visible-count bins, separately for each physical design hash",
            "case_selection": "every candidate_check; separate problem/protocol; alphabetical models; no status filter",
            "interval": "copy difference_ci95, including schema boundary widening; local minus official; no recomputation",
            "missing": "retain core slots; no imputation; absent complete joint designs get explicit unavailable pages",
            "limits": "pointwise exploratory whole-case intervals; shared official/local cases correlate comparisons; not family-wise inference"}


def declare(path):
    path = Path(path)
    if path.exists():
        old = load(path)
        if old["design"] != design():
            raise ValueError("Existing metric declaration differs; use an explicitly versioned new declaration")
        return old
    result = {"declared_utc": datetime.now(timezone.utc).isoformat(), "design": design(),
              "read_result_files": False, "selection_basis": "calibration.py schema only, before final combined results"}
    save(path, result)
    return result


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def grouped_checks(document):
    groups = defaultdict(list)
    seen = set()
    for row in document["candidate_checks"]:
        key = row["problem"], row["protocol"], row["model_candidate"]
        if key in seen:
            raise ValueError(f"Duplicate candidate-check group: {key}")
        if row.get("official_reference", "development_only") != "development_only":
            raise ValueError("Only official development references belong to this declaration")
        names = [m["metric"] for m in row["metrics"]]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate scalar metric")
        seen.add(key)
        groups[key[:2]].append(row)
    return {key: sorted(rows, key=lambda r: r["model_candidate"])
            for key, rows in sorted(groups.items())}


def metric_map(row):
    return {metric["metric"]: metric for metric in row["metrics"]}


def metric_catalog(rows):
    """Resolve declared name families without consulting any numeric outcome."""
    names = sorted({m["metric"] for r in rows for m in r["metrics"]})
    core = []
    for name, title, unit in CORE:
        if name.startswith("@"):
            family = "origin" if name == "@origin" else "joint_mean"
            matches = [n for n in names if re.fullmatch(PATTERNS[family], n)]
            for match in matches or [name]:
                suffix = "" if len(matches) <= 1 else "\n" + match
                core.append({"metric": match, "title": title + suffix, "unit": unit})
        else:
            core.append({"metric": name, "title": title, "unit": unit})
    pairs, counts = defaultdict(list), defaultdict(list)
    for name in names:
        match = re.fullmatch(PATTERNS["joint_pair"], name)
        if match:
            h, i, j, kind = match.groups()
            pairs[h].append({"metric": name, "title": f"Stations {int(i)} / {int(j)}: {kind.replace('_', ' ')}",
                             "unit": "probability among confirmed sources"})
        match = re.fullmatch(PATTERNS["joint_count"], name)
        if match:
            h, k = match.groups()
            counts[h].append({"metric": name, "visible_stations": int(k)})
    return {"core": core, "pairs": dict(pairs), "counts": dict(counts)}


def scalar_payload(row, name):
    metric = metric_map(row).get(name)
    if metric is None:
        return {"metric": name, "missing": True, "n_official_development": 0, "n_local": 0,
                "status": "missing", "difference_local_minus_official": None, "difference_ci95": [None, None]}
    result = dict(metric)
    result["missing"] = False
    result["small_n"] = min(metric["n_official_development"], metric["n_local"]) < 5
    interval = metric.get("difference_ci95", [None, None])
    if len(interval) != 2 or any(v is not None and not finite(v) for v in interval):
        raise ValueError("Malformed stored difference interval")
    if all(finite(v) for v in interval) and interval[0] > interval[1]:
        raise ValueError("Reversed stored difference interval")
    return result


def short_model(name):
    return name.replace("__radius_", " / R=").replace("__noise_", " / noise=")


def model_key(rows):
    return [{"id": f"M{i+1:02d}", "model": row["model_candidate"], "label": short_model(row["model_candidate"]),
             "candidate_status": row["status"], "n_official_development": row["n_official_development"],
             "n_local": row["n_local"]} for i, row in enumerate(rows)]


def figure_footer(fig, keys):
    model_lines = [f"{row['id']}: {row['label']}" for row in keys]
    fig.text(.02, .012, "\n".join(model_lines), fontsize=7.6, va="bottom", family="DejaVu Sans")


def scalar_panel(ax, rows, item):
    colors = {"matched": "#236e9b", "screen_flag": "#a65c0b", "insufficient_evidence": "#777b80", "missing": "#aaaaaa"}
    labels, payload = [], []
    for i, row in enumerate(rows):
        value = scalar_payload(row, item["metric"])
        payload.append(value)
        n, m = value["n_official_development"], value["n_local"]
        status = value["status"]
        flag = {"matched": "M", "screen_flag": "F", "insufficient_evidence": "I", "missing": "NA"}.get(status, "?")
        labels.append(f"M{i+1:02d} O{n}/L{m} {flag}" + ("*" if value.get("small_n") else ""))
        point = value.get("difference_local_minus_official")
        lo, hi = value.get("difference_ci95", [None, None])
        color = colors.get(status, colors["insufficient_evidence"])
        margin = value.get("practical_margin")
        if finite(margin) and margin >= 0:
            ax.hlines(i, -margin, margin, color="#e6e8ea", linewidth=11, zorder=0)
        if finite(lo) and finite(hi):
            ax.hlines(i, lo, hi, color=color, linewidth=1.5)
            ax.plot([lo, hi], [i, i], "|", color=color, markersize=5)
        if finite(point):
            ax.plot(point, i, "o" if finite(lo) and finite(hi) else "x", color=color, markersize=4)
        else:
            ax.text(.98, i, "no comparison", transform=ax.get_yaxis_transform(), ha="right", va="center", fontsize=6.9, color=color)
    ax.set_yticks(range(len(rows)), labels, fontsize=7.3)
    ax.set_ylim(len(rows)-.4, -.6)
    ax.axvline(0, color="#333333", linewidth=.7)
    ax.grid(axis="x", color="#eeeeee", linewidth=.6)
    ax.set_title(item["title"], fontsize=9.5, loc="left")
    ax.set_xlabel("Local minus official, " + item["unit"], fontsize=8)
    ax.tick_params(axis="x", labelsize=7.5)
    return payload


def forest_figure(rows, items, title):
    import matplotlib.pyplot as plt
    cols = 2 if len(items) <= 10 else 3
    lines = math.ceil(len(items) / cols)
    panel_height = max(1.9, .26*len(rows)+.8)
    key_height = .14*len(rows)+.55
    header_extra = .24*title.count("\n")
    height = lines*panel_height + key_height + 1.05 + header_extra
    fig, axes = plt.subplots(lines, cols, figsize=(16, height), squeeze=False)
    bottom = key_height / height
    fig.subplots_adjust(left=.095, right=.985, top=1-(.85+header_extra)/height, bottom=bottom, hspace=.68, wspace=.40)
    payloads = []
    for ax, item in zip(axes.flat, items):
        payloads.append({**item, "values": scalar_panel(ax, rows, item)})
    for ax in list(axes.flat)[len(items):]:
        ax.set_visible(False)
    fig.suptitle(title + "\nPointwise exploratory 95% whole-case intervals; O/L = official development/local case clusters", y=1-.08/height, fontsize=12)
    fig.text(.5, 1-(.53+header_extra)/height, "M: inside practical margin   F: screening flag   I: insufficient evidence   *: min n < 5   x: point only   gray band: stated margin", ha="center", fontsize=8)
    figure_footer(fig, model_key(rows))
    return fig, payloads


def unavailable_figure(rows, title, family):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 4.8))
    fig.subplots_adjust(bottom=.32)
    ax.axis("off")
    ax.set_title(title, fontsize=13)
    ax.text(.5, .57, f"{family}: unavailable in this protocol group\nNo complete common pre-clear coverage design supplies these comparisons.\nMissing observations are not zero and do not establish model compatibility.",
            transform=ax.transAxes, ha="center", va="center", fontsize=11)
    figure_footer(fig, model_key(rows))
    return fig


def count_figure(rows, items, title):
    import matplotlib.pyplot as plt
    cols, lines = 2, math.ceil(len(rows)/2)
    key_height = .14*len(rows)+.45
    height = lines*2.25+key_height+1.25
    fig, axes = plt.subplots(lines, cols, figsize=(14, height), squeeze=False)
    fig.subplots_adjust(left=.08, right=.985, top=1-1.25/height, bottom=key_height/height, wspace=.26, hspace=.57)
    items = sorted(items, key=lambda r: r["visible_stations"])
    payload = []
    for i, (ax, row) in enumerate(zip(axes.flat, rows)):
        values = [scalar_payload(row, item["metric"]) for item in items]
        payload.append({"model": row["model_candidate"], "values": values})
        available = []
        for item, value in zip(items, values):
            x = item["visible_stations"]
            point = value.get("difference_local_minus_official")
            lo, hi = value.get("difference_ci95", [None, None])
            if finite(point):
                available.append((x, point))
                ax.plot(x, point, "o" if finite(lo) and finite(hi) else "x", color="#236e9b", markersize=3)
            if finite(lo) and finite(hi):
                ax.vlines(x, lo, hi, color="#236e9b", linewidth=.8)
        if not available:
            ax.text(.5, .5, "No comparison; missing bins not imputed", transform=ax.transAxes, ha="center", color="#777777")
        ns = sorted({(v["n_official_development"], v["n_local"]) for v in values})
        text_n = ", ".join(f"O{a}/L{b}" for a, b in ns)
        status_counts = {s: sum(v["status"] == s for v in values) for s in ("matched", "screen_flag", "insufficient_evidence", "missing")}
        ax.set_title(f"M{i+1:02d} {text_n}; M/F/I/NA={status_counts['matched']}/{status_counts['screen_flag']}/{status_counts['insufficient_evidence']}/{status_counts['missing']}", fontsize=9, loc="left")
        ax.axhline(0, color="#333333", linewidth=.7)
        ax.set_xlabel("Number of visible stations per confirmed source", fontsize=8)
        ax.set_ylabel("Local minus official probability", fontsize=8)
        ax.grid(alpha=.15)
        ax.tick_params(labelsize=8)
        if len(items) <= 10:
            ax.set_xticks([r["visible_stations"] for r in items])
    for ax in list(axes.flat)[len(rows):]:
        ax.set_visible(False)
    fig.suptitle(title + "\nAll bins retained; pointwise whole-case intervals, including conservative boundary widening", y=1-.06/height, fontsize=11)
    fig.text(.5, 1-.78/height, "M: inside practical margin   F: screening flag   I: insufficient evidence   NA: missing   x: mean only, no CI; O5 is a small official sample", ha="center", fontsize=7.8)
    figure_footer(fig, model_key(rows))
    return fig, payload


def plot(input_path, declaration_path, output):
    declaration = load(declaration_path)
    if declaration["design"] != design() or not declaration.get("read_result_files") is False:
        raise ValueError("Metric declaration differs from the fixed plotting design")
    document = load(input_path)
    groups = grouped_checks(document)
    if not groups:
        raise ValueError("No local protocol comparisons available; no figures fabricated")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    matplotlib.rcParams.update({"font.family": "DejaVu Sans", "pdf.fonttype": 42})
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"input": str(Path(input_path).resolve()), "input_sha256": sha(input_path),
                "declaration": str(Path(declaration_path).resolve()), "declaration_sha256": sha(declaration_path),
                "script_sha256": sha(__file__), "analysis_version": document.get("analysis_version"),
                "candidate_check_count": len(document["candidate_checks"]), "groups": [], "pages": [],
                "scope": "data-only copy of stored model-minus-official diagnostics; no additional inference"}
    full_source = {"manifest": manifest, "declaration": declaration, "all_candidate_checks": document["candidate_checks"], "plotted": []}
    with PdfPages(output/"model_check_diagnostics.pdf", metadata={"Title": "Same-protocol model checks: pointwise exploratory diagnostics"}) as pdf:
        for (problem, protocol), rows in groups.items():
            catalog = metric_catalog(rows)
            prefix = f"p{problem}_{protocol}"
            title = f"P{problem} | {protocol} | Local mechanism candidates versus official development"
            manifest["groups"].append({"problem": problem, "protocol": protocol, "model_keys": model_key(rows), "catalog": catalog})

            def emit(fig, stem, data):
                fig.savefig(output/(stem+".png"), dpi=145)
                pdf.savefig(fig)
                plt.close(fig)
                manifest["pages"].append(stem)
                full_source["plotted"].append({"page": stem, "data": data})

            fig, data = forest_figure(rows, catalog["core"], title)
            emit(fig, prefix+"_core", data)
            if not catalog["pairs"]:
                emit(unavailable_figure(rows, title, "Joint station pairs"), prefix+"_joint_pairs_unavailable", [])
            for h, items in sorted(catalog["pairs"].items()):
                fig, data = forest_figure(rows, items, title + f"\nAll geometric station pairs, physical design {h}")
                emit(fig, prefix+"_joint_pairs_"+h, data)
            if not catalog["counts"]:
                emit(unavailable_figure(rows, title, "Joint visible-count distribution"), prefix+"_joint_counts_unavailable", [])
            for h, items in sorted(catalog["counts"].items()):
                fig, data = count_figure(rows, items, title + f"\nVisible-count distribution, physical design {h}")
                emit(fig, prefix+"_joint_counts_"+h, data)
    save(output/"source.json", full_source)
    save(output/"manifest.json", manifest)
    notes = ["# 同协议机制检查图", "", "指标规则先从 schema 声明，再读取最终检查文件。所有候选按名称排序；未按 matched、screen_flag 或效果大小筛选。", "",
             "横向森林图区间和纵向分布区间直接复制 model_checks.json 的 difference_ci95，包含全零/全一局级率的边界加宽；没有用较窄的原始 bootstrap 区间替代。差值为本地减官方，官方仅 development。每题、每协议、每实际站点设计分开。", "",
             "O/L 是该指标实际贡献的整局簇数，可能小于组总局数。I 表示 insufficient_evidence；星号表示至少一侧不足5局。M 只是该单项进入预设实质容限，F 只是探索性筛查信号；均不等同于整模型机制已识别或正式拒绝。所有区间逐项解释，多候选共享官方案例和部分本地场景，图不提供族级显著性控制或独立复制。", "",
             "完整局耗时与清除失败是条件指标，须结合完成率，不能将失败局短投入解释成效率高。相邻报告角差含真实几何变化，同点角差也不是相对真方位的误差；面积比是算法外包代理。原点率分母为20频道；联合指标分母为事后确认存在的频道，两者不混同。", "",
             "baseline 穿插清除，可能缺完整清除前联合设计；缺失页明确保留，不补零。survey 官方 development 的小样本限制不会因增加本地局数而消失。全部可见站点数箱和既有六个几何最近站点对均展示，没有挑选差异明显的站点。", "",
             "可见性与成本还受 N/Ndir 组成混合影响，比较差异不能单独解释成接收半径或噪声的因果作用。源数据保留的 survey_nominal_distance_1250 等字段是相对冻结估计点的分箱下界：[1250,1500) m，不是距真源1250 m；本次预声明主图没有从这些箱中挑选显著结果。", "",
             "源 JSON 保留所有 candidate_checks，包括图中固定指标之外的检查；模型 ID 的全称、输入/声明/脚本 SHA256 与每页原数值可复核。", ""]
    for group in manifest["groups"]:
        notes.append(f"## P{group['problem']} / {group['protocol']}")
        notes.append("")
        for key in group["model_keys"]:
            notes.append(f"- {key['id']}: {key['model']}；组状态 {key['candidate_status']}；组记录 O{key['n_official_development']}/L{key['n_local']}。")
        notes.append("")
    (output/"captions.md").write_text("\n".join(notes), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("declare", help="Fix schema-only metric design without reading results")
    d.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("plot", help="Render existing checks only; no simulator or calibration execution")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--declaration", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = declare(args.output) if args.command == "declare" else plot(args.input, args.declaration, args.output)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
