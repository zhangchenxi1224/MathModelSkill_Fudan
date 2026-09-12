"""Reproducible actual-observation figures with a preregistered case-selection rule.

Reads public journals, survey design logs and set-valued archives only. No hidden
case.json or encrypted simulator log is opened. Clearance points are not targets.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
from bsolver.calibration import unique_accepted_actions  # noqa: E402


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def selected_cases(plan):
    """First survey-fit case in frozen plan order, separately for each problem."""
    return [next(case for case in plan["cases"] if case["problem"] == problem
                 and case["protocol"] == "survey" and case["split"] == "fit")
            for problem in (3, 4)]


def verify_archives(round_dir):
    round_dir = Path(round_dir)
    plan = read_json(round_dir/"plan.json")
    index = read_json(round_dir/"posterior_archives/index.json")
    if len(index["cases"]) != len(plan["cases"]):
        raise ValueError("archive count does not equal the frozen round allocation")
    if any(case["status"] != "archived" for case in index["cases"]):
        raise ValueError("archive errors or audit issues remain")
    entries = {entry["case_id"]: entry for entry in index["cases"]}
    total_sources, total_directional, empty_count = 0, 0, 0
    for case in plan["cases"]:
        folder = round_dir/"cases"/case["case_id"]
        audit = read_json(folder/"post_exit_audit.json")
        archive = read_json(round_dir/"posterior_archives"/case["case_id"]/"posterior_archive.json")
        expected = {}
        for key, alias in (("N", "source_total_post_exit"), ("Ndir", "directional_total_post_exit")):
            value = audit.get(key)
            if value is None:
                value = audit.get(alias)
            if value is not None:
                expected[key] = value
        if archive["post_exit_counts"] != expected:
            raise ValueError(f"public aggregate alias mismatch: {case['case_id']}")
        if len(archive["global_count_constraints"]) != len(expected):
            raise ValueError("public aggregate constraints missing")
        if not archive["exit_confirmed"]:
            raise ValueError("a selected round case lacks a confirmed exit")
        if archive["requests_sha256"] != sha(folder/"requests.jsonl"):
            raise ValueError("archive was not produced from the current frozen request journal")
        if case["case_id"] not in entries:
            raise ValueError("archive index missing planned case")
        empty_count += sum(state["position_outer_empty"] for state in archive["channels"].values())
        total_sources += expected.get("N", 0)
        total_directional += expected.get("Ndir", 0)
    if empty_count:
        raise ValueError("empty outer hulls require inspection before plotting")
    return {"archived_cases": len(plan["cases"]), "archive_errors": 0, "audit_issues": 0,
            "aggregate_count_alias_mismatches": 0, "empty_outer_hulls": empty_count,
            "public_N_sum": total_sources, "public_Ndir_sum": total_directional}


def build_source(round_dir, case):
    round_dir = Path(round_dir)
    folder = round_dir/"cases"/case["case_id"]
    archive_path = round_dir/"posterior_archives"/case["case_id"]/"posterior_archive.json"
    archive = read_json(archive_path)
    decisions = read_rows(folder/"decisions.jsonl")
    start = next(event for event in decisions if event["event"] == "start")
    design = next(event for event in decisions if event["event"] == "survey_plan")
    selected = design["sampled_channels"]
    if selected != [target["channel"] for target in design["plan"]]:
        raise ValueError("recorded sample order and frozen survey plan disagree")
    by_request = {event["request_id"]: event for event in decisions
                  if event["event"] == "measure" and event.get("request_id")}
    fallback = {(event.get("target_channel", event["channel"]), tuple(event["position"]),
                 event["response"]["virtual_time_s"]): event for event in decisions if event["event"] == "measure"}
    coverage_rows = [{"station_id": f"coverage:{i:03d}", "label": f"C{i:02d}", "position": point,
                      "values": [None]*20, "measured_mask": [False]*20}
                     for i, point in enumerate(start["coverage_points"])]
    survey_rows = []
    for target in design["plan"]:
        for probe in target["probes"]:
            suffix = "*" if probe["kind"] == "same_point_repeat" else "+" if probe["kind"] == "neighbor" else ""
            survey_rows.append({"station_id": probe["station_id"], "label": f"F{target['channel']:02d}-{probe['plan_index']:02d}{suffix}",
                                "position": probe["position"], "planned_channel": target["channel"],
                                "kind": probe["kind"], "values": [None]*20, "measured_mask": [False]*20})
    maps = {"coverage": {row["station_id"]: row for row in coverage_rows},
            "survey": {row["station_id"]: row for row in survey_rows}}
    after_survey = {}
    accepted = list(unique_accepted_actions(read_rows(folder/"requests.jsonl")))
    for _, row in accepted:
        if row.get("path") != "/measure":
            continue
        request, response = row["request"], row["response"]
        point = request["position"]["x"], request["position"]["y"]
        event = by_request.get(request["request_id"])
        if event is None:
            event = fallback.get((request["channel"], point, response["virtual_time_s"]))
        if event is None:
            raise ValueError("confirmed measurement lacks a matched decision event")
        if event["response"] != response:
            raise ValueError("decision and confirmed protocol response disagree")
        phase = event.get("phase")
        if phase not in maps:
            continue
        matrix_row = maps[phase][event["station_id"]]
        channel_index = request["channel"]-1
        if matrix_row["measured_mask"][channel_index]:
            raise ValueError("multiple distinct measurements occupy one planned matrix cell")
        if tuple(matrix_row["position"]) != point:
            raise ValueError("matrix station and actual request position disagree")
        matrix_row["values"][channel_index] = {"no_signal": 0, "direction": 1, "near": 2}[response["measure_result"]]
        matrix_row["measured_mask"][channel_index] = True
        if phase == "survey":
            after_survey[request["channel"]] = copy.deepcopy(event["knowledge"]["hull"])
    if not all(all(row["measured_mask"]) for row in coverage_rows):
        raise ValueError("standard coverage matrix is unexpectedly incomplete")
    if not all(sum(row["measured_mask"]) == 1 for row in survey_rows):
        raise ValueError("fixed extra survey did not measure exactly its planned target")
    all_survey_sequences = [event["sequence"] for event in decisions if event["event"] == "measure"
                            and event.get("phase") in ("coverage", "survey")]
    if any(event["sequence"] < max(all_survey_sequences) for event in decisions if event["event"] == "clear"):
        raise ValueError("survey measurements occurred after target removal")
    channels = []
    for target in design["plan"]:
        channel = target["channel"]
        state = archive["channels"][str(channel)]
        clears = [entry["position"] for entry in state["active_clear_constraints"] if entry["result"] == "success"]
        if len(clears) != 1:
            raise ValueError("selected channel must have exactly one confirmed successful clear")
        channels.append({"channel": channel, "frozen_reference": target["reference"],
                         "survey_probes": target["probes"], "after_survey_outer_hull": after_survey[channel],
                         "final_outer_hull": state["position_outer_polygon"],
                         "clear_point": clears[0], "clear_disk_radius_m": 20.,
                         "clear_point_is_ground_truth": False,
                         "radius_constraints": state["radius_constraints"],
                         "orientation_constraints": state["orientation_constraints"],
                         "joint_feasibility": state["joint_feasibility"]})
    return {"case_id": case["case_id"], "problem": case["problem"], "assignment": case,
            "selection_rule": "first survey fit case in frozen plan order; channels are the already logged preregistered random sample",
            "sampled_channels": selected, "survey_seed": case["survey_seed"],
            "post_exit_public_counts": archive["post_exit_counts"],
            "channels": channels, "coverage_rows": coverage_rows, "survey_rows": survey_rows,
            "outcome_encoding": {"no_signal": 0, "direction": 1, "near": 2, "unmeasured": None},
            "no_source_truth_read": True,
            "input_sha256": {"plan": sha(round_dir/"plan.json"), "requests": sha(folder/"requests.jsonl"),
                             "decisions": sha(folder/"decisions.jsonl"), "archive": sha(archive_path),
                             "assignment": sha(folder/"assignment.json")},
            "plot_script_sha256": sha(__file__)}


def plotting_setup():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.titlesize": 10,
                         "axes.labelsize": 9, "pdf.fonttype": 42, "ps.fonttype": 42,
                         "savefig.dpi": 180, "axes.spines.top": False, "axes.spines.right": False})
    return plt


def draw_region(source, output):
    plt = plotting_setup()
    from matplotlib.patches import Circle, Patch, Polygon
    from matplotlib.lines import Line2D
    count = len(source["channels"])
    fig, axes = plt.subplots(count, 2, figsize=(10.5, 3.9*count+1.), squeeze=False)
    colors = {"scan": "#9AA1AA", "survey": "#56B4E9", "final": "#005A87", "clear": "#D55E00"}

    def polygon(ax, points, color, alpha):
        if len(points) >= 3:
            ax.add_patch(Polygon(points, closed=True, facecolor=color, edgecolor=color, alpha=alpha, lw=1.1))
        elif points:
            ax.plot(*zip(*points), color=color, lw=1.4, marker=".")

    def fit(ax, points, minimum=48.):
        xs, ys = list(zip(*points))
        cx, cy = (min(xs)+max(xs))/2, (min(ys)+max(ys))/2
        span = max(max(xs)-min(xs), max(ys)-min(ys), minimum)*1.18
        ax.set_xlim(cx-span/2, cx+span/2)
        ax.set_ylim(cy-span/2, cy+span/2)
        ax.set_aspect("equal", adjustable="box")
        ax.grid(alpha=.18, lw=.5)
        ax.set_xlabel("East coordinate x (m)")
        ax.set_ylabel("North coordinate y (m)")
        ax.ticklabel_format(useOffset=False, style="plain")

    for row, channel in enumerate(source["channels"]):
        clear = channel["clear_point"]
        around_clear = [(clear[0]-20, clear[1]-20), (clear[0]+20, clear[1]+20)]
        scan = channel["frozen_reference"]["hull"]
        for column, ax in enumerate(axes[row]):
            if column == 0:
                polygon(ax, scan, colors["scan"], .35)
                polygon(ax, channel["after_survey_outer_hull"], colors["survey"], .55)
            polygon(ax, channel["final_outer_hull"], colors["final"], .75)
            ax.add_patch(Circle(clear, 20., fill=False, edgecolor=colors["clear"], ls="--", lw=1.2))
            ax.plot(*clear, marker="x", color=colors["clear"], ms=7, mew=1.6)
            if column == 0:
                estimate = channel["frozen_reference"]["estimate"]
                ax.plot(*estimate, marker="D", color="#222", mfc="white", ms=4)
            points = (scan if column == 0 else channel["final_outer_hull"])+around_clear
            fit(ax, points)
            title = "All recorded positive constraints" if column == 0 else "Final outer bound and clearance disk"
            ax.set_title(f"Channel {channel['channel']} | {title}", loc="left")
        lower = channel["radius_constraints"]["necessary_global_radius_interval_m"][0]
        axes[row, 1].text(.02, .02, f"Necessary R >= {lower:.1f} m; R <= 1500 m\nShared (g, R, u) feasibility is not certified",
                         transform=axes[row, 1].transAxes, fontsize=7.5,
                         bbox={"facecolor": "white", "alpha": .88, "edgecolor": "none"})
    legend = [Patch(facecolor=colors["scan"], alpha=.35, label="After fixed coverage"),
              Patch(facecolor=colors["survey"], alpha=.55, label="After added survey"),
              Patch(facecolor=colors["final"], alpha=.75, label="Final conservative outer bound"),
              Line2D([], [], color=colors["clear"], ls="--", label="20 m successful-clearance disk"),
              Line2D([], [], color=colors["clear"], marker="x", ls="none", label="Clearance point (not true source)"),
              Line2D([], [], color="#222", marker="D", mfc="white", ls="none", label="Frozen observation-based estimate")]
    fig.legend(handles=legend, loc="lower center", ncol=2, frameon=False, fontsize=8, bbox_to_anchor=(.5, .012))
    fig.suptitle(f"Problem {source['problem']} | Observable location constraints\n{source['case_id']} | preregistered channels {source['sampled_channels']}", fontsize=12, y=.99)
    fig.subplots_adjust(left=.08, right=.98, bottom=.14, top=.90, hspace=.33, wspace=.23)
    for extension in ("png", "pdf"):
        fig.savefig(output.with_suffix("."+extension), bbox_inches="tight")
    plt.close(fig)


def draw_matrix(source, output):
    plt = plotting_setup()
    import numpy as np
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from matplotlib.patches import Patch
    coverage, survey = source["coverage_rows"], source["survey_rows"]
    height = max(9., (len(coverage)+len(survey))*.16+3.)
    fig, axes = plt.subplots(2, 1, figsize=(10.5, height),
                             gridspec_kw={"height_ratios": [len(coverage)+2, len(survey)+2]})
    cmap = ListedColormap(["#BFC3C8", "#0072B2", "#E69F00", "#FFFFFF"])
    norm = BoundaryNorm([-.5, .5, 1.5, 2.5, 3.5], cmap.N)
    for ax, rows, title in zip(axes, (coverage, survey),
                              ("Fixed coverage: all 20 channels at every station",
                               "Added survey: one preregistered channel at each planned point")):
        values = np.array([[3 if value is None else value for value in row["values"]] for row in rows])
        ax.imshow(values, cmap=cmap, norm=norm, aspect="auto", interpolation="nearest")
        yy, xx = np.where(values == 3)
        ax.scatter(xx, yy, marker="x", s=5, linewidths=.35, color="#A4AAB0")
        ax.set_xticks(range(20), labels=range(1, 21))
        ax.set_yticks(range(len(rows)), labels=[row["label"] for row in rows], fontsize=6.5)
        ax.set_xticks(np.arange(-.5, 20, 1), minor=True)
        ax.set_yticks(np.arange(-.5, len(rows), 1), minor=True)
        ax.grid(which="minor", color="#E7E9EC", lw=.45)
        ax.tick_params(which="minor", bottom=False, left=False)
        ax.set_title(title, loc="left", fontsize=10)
        ax.set_xlabel("Channel number (sampled channels in bold)")
        ax.set_ylabel("Station / planned probe")
        for tick, channel in zip(ax.get_xticklabels(), range(1, 21)):
            if channel in source["sampled_channels"]:
                tick.set_fontweight("bold")
                tick.set_color("#005A87")
    legend = [Patch(facecolor="#BFC3C8", label="No signal (measured)"),
              Patch(facecolor="#0072B2", label="Direction readout"),
              Patch(facecolor="#E69F00", label="Near"),
              Patch(facecolor="white", edgecolor="#A4AAB0", hatch="xx", label="Unmeasured / masked")]
    fig.legend(handles=legend, loc="upper center", ncol=4, frameon=False, fontsize=8, bbox_to_anchor=(.5, .945))
    fig.suptitle(f"Problem {source['problem']} | Joint survey observation matrix\n{source['case_id']} | all displayed measurements precede any clearance", fontsize=12, y=.991)
    fig.text(.08, .015, "Cxx: coverage station. Fxx-yy: target channel and probe index. * same-point repeat; + 1 m neighbor.\nWhite crossed cells are unmeasured, never imputed as no signal. Colors do not label source type.", fontsize=8)
    fig.subplots_adjust(left=.13, right=.985, top=.90, bottom=max(.07, 1.05/height), hspace=.32)
    for extension in ("png", "pdf"):
        fig.savefig(output.with_suffix("."+extension), bbox_inches="tight")
    plt.close(fig)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", type=Path, default=ROOT/"results/round1")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    output = args.output or args.round/"identifiability_figures"
    output.mkdir(parents=True, exist_ok=True)
    archive_audit = verify_archives(args.round)
    plan = read_json(args.round/"plan.json")
    manifest = {"selection_rule": "first survey fit case in frozen plan order, separately for P3/P4",
                "archive_audit": archive_audit, "cases": [], "plan_sha256": sha(args.round/"plan.json")}
    captions = ["# 首轮官方观测的可识别性图示", "", "选择规则在看图前固定为：按原计划顺序，每题第一个 survey-fit 案例；频道沿用该局日志中预注册种子抽中的顺序。未按结果优劣挑选案例或频道。", ""]
    for case in selected_cases(plan):
        source = build_source(args.round, case)
        prefix = f"p{case['problem']}_{case['case_id']}"
        source_path = output/(prefix+"_source.json")
        source_path.write_text(json.dumps(source, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        draw_region(source, output/(prefix+"_regions"))
        draw_matrix(source, output/(prefix+"_matrix"))
        manifest["cases"].append({"case_id": case["case_id"], "problem": case["problem"],
                                  "sampled_channels": source["sampled_channels"], "source_json": source_path.name,
                                  "coverage_cells_measured": sum(sum(row["measured_mask"]) for row in source["coverage_rows"]),
                                  "survey_cells_measured": sum(sum(row["measured_mask"]) for row in source["survey_rows"]),
                                  "survey_cells_masked": sum(20-sum(row["measured_mask"]) for row in source["survey_rows"])})
        captions.extend([f"## 问题 {case['problem']}：{case['case_id']}", "",
            f"抽样频道 {source['sampled_channels']}，原预注册种子 {source['survey_seed']}。位置图依次呈现完整扫描后、追加调查后及结合后续阳性与成功清除约束后的保守外包。成功清除点只说明目标位于其 20 m 圆内，图中的叉号不是源真坐标。", "",
            f"矩阵包含 {len(source['coverage_rows'])} 个固定站点及 {len(source['survey_rows'])} 次追加调查动作。颜色区分已测无信号、示向读数与 near；白底交叉项明确为未测。所有这些观测均在首次清除前采集，追加阶段没有用未测频道填充无信号。", ""])
    captions.extend(["## 适用边界", "", "外包非空不证明共享位置 g、接收半径 R 与方向 u 的联合约束存在解。位置图保留外接多边形及数值外扩；no_signal 与失败清除的非凸约束仍在源 JSON/逐频道档案中，未把孔洞错误地画成已排除的凸区域。", "",
                     "冻结估计点不是源真坐标。浅蓝与深蓝区域可能重合：调查后已满足安全清除条件时，成功确认的 20 m 圆约束可以不再缩小原外包，它不额外返回真坐标。图中 R 下界是对所有候选位置有效的必要放宽界，不是精确半径估计；定向类型和朝向没有按矩阵颜色指定。官方事后 N/Ndir 只作为全局计数约束保留。两例图示不估计总体性能，也不证明调查设计统计最优。", "",
                     "脚本只读取公开 requests/decisions/assignment 与派生档案，没有读取隐藏 case.json 或解密原始模拟器日志。PNG 与 PDF 由同一 Matplotlib 数据源生成，完整坐标、掩码、生成规则及输入 SHA256 保存在对应 source.json。"])
    (output/"captions.md").write_text("\n".join(captions)+"\n", encoding="utf-8")
    (output/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
