"""A4-readable figures from actual released evidence; no strategy execution.

Nine study figures are always built. Fresh official figures are opt-in and
require all frozen allocations to have terminal artifacts; no empty placeholders.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts"))
from summarize_round_pipeline import collect_official
from plot_official_validation import components_from_cases, difference_text, numeric

GROUPS = [("calibrated", "smoothed_joint"), ("calibrated", "broad_joint"),
          ("broad", "broad_joint"), ("stress", "stress_design")]
NAMES = {GROUPS[0]: "Calibrated / smoothed", GROUPS[1]: "Calibrated / broad",
         GROUPS[2]: "Broad legal", GROUPS[3]: "Stress"}
COLORS = {"development": "#306c96", "confirmation": "#be6c35"}
COMPONENTS = [("movement_s", "Move", "#476b8d"), ("switch_s", "Switch", "#e5a34b"),
              ("measurement_s", "Measure", "#63a59d"), ("optical_s", "Optical", "#bc6a70"),
              ("laser_s", "Clear extra", "#80608a")]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def relative(path):
    path = Path(path).resolve()
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


def save(fig, name, output, rows, sources, caption, *, minimum_font_pt=10.):
    output.mkdir(parents=True, exist_ok=True)
    width, height = fig.get_size_inches()
    print_scale = min(497.27559/(width*72), 405/(height*72))
    files = []
    for ext in ("png", "pdf"):
        path = output/f"{name}.{ext}"
        fig.savefig(path, dpi=220)
        files.append({"path": relative(path), "sha256": sha(path)})
    plt.close(fig)
    payload = {"figure": name, "caption": caption, "actual_rows": rows,
               "input_files": [{"path": relative(p), "sha256": sha(p)} for p in dict.fromkeys(sources)],
               "outputs": files, "figure_inches": [float(width), float(height)],
               "minimum_source_font_pt": minimum_font_pt,
               "estimated_minimum_A4_font_pt": minimum_font_pt*print_scale,
               "scope": "data-only plot; no fitted parameters, action execution or unreleased confirmation results",
               "script_sha256": sha(__file__)}
    save_json(output/f"{name}_source.json", payload)
    return payload


def paired_rows(path, problem, variant, partition):
    rows = [r for r in read(path) if r["problem"] == problem and r["partition"] == partition
            and r["variant"] == variant and r["noise_model"] == "all"
            and r.get("mechanism_id") == "__design_mixture__"]
    index = {(r["pool"], r["composition_model"]): r for r in rows}
    if len(rows) != 4 or set(index) != set(GROUPS):
        raise ValueError(f"Expected the four actual design groups: {path}, P{problem}, {variant}")
    return [index[key] for key in GROUPS]


def paired_plot(stage_root, problem, variant, name, title, output):
    paths = [stage_root/p/"paired_summary.json" for p in ("development", "confirmation")]
    parts = {p: paired_rows(path, problem, variant, p)
             for p, path in zip(("development", "confirmation"), paths)}
    fig, ax = plt.subplots(figsize=(6.6, 4.5), layout="constrained")
    labels, saved = [], []
    for i, key in enumerate(GROUPS):
        dev, conf = (parts[p][i] for p in parts)
        labels.append(f"{NAMES[key]}\nnD={dev['n_pairs']}; nC={conf['n_pairs']}")
        for phase, delta, marker in (("development", -.13, "o"), ("confirmation", .13, "s")):
            row = parts[phase][i]
            lo, hi, value = (row[k] for k in ("penalized_delta_ci_low_s", "penalized_delta_ci_high_s", "penalized_time_delta_mean_s"))
            if lo is not None and hi is not None:
                ax.hlines(i+delta, lo, hi, color=COLORS[phase], lw=1.8)
            ax.plot(value, i+delta, marker, color=COLORS[phase], ms=5,
                    label=phase.capitalize() if i == 0 else None)
            saved.append(row)
    ax.set_yticks(range(4), labels, fontsize=10)
    ax.set_ylim(3.65, -.75)
    ax.axvline(0, color="#555555", lw=.8)
    ax.grid(axis="x", alpha=.2)
    ax.set_xlabel("Candidate - baseline, failure-penalized seconds\nPointwise paired bootstrap 95% CI; lower is better", fontsize=10)
    ax.set_title(title, fontsize=12, pad=12)
    ax.legend(loc="upper left", fontsize=10, frameon=False)
    caption = (f"P{problem}, {variant}: four declared design groups, development and frozen confirmation kept separate. "
               "Intervals concern 360000-second failure-penalized differences, not official population effects. "
               f"All {sum(r['n_pairs'] for r in saved)} plotted pairs have zero failures in both arms; exact source rows retained.")
    if any(r["baseline_failures"] or r["candidate_failures"] for r in saved):
        caption = caption.replace(f"All {sum(r['n_pairs'] for r in saved)} plotted pairs have zero failures in both arms; ", "Failures are retained; ")
    return save(fig, name, output, saved, paths, caption)


def scheduling_plot(stage_root, problem, output):
    path = stage_root/"development/paired_summary.json"
    variants = ("immediate", "scan_then_clear")
    groups = {v: paired_rows(path, problem, v, "development") for v in variants}
    rows = [groups[v][i] for i in range(4) for v in variants]
    fig, ax = plt.subplots(figsize=(6.6, 5.5), layout="constrained")
    labels = []
    for i, row in enumerate(rows):
        color = "#8b5c83" if row["variant"] == "immediate" else "#4e8273"
        label = "Immediate" if row["variant"] == "immediate" else "Scan then clear"
        key = row["pool"], row["composition_model"]
        labels.append(f"{NAMES[key]} / {label}\nn={row['n_pairs']}")
        lo, hi, value = (row[k] for k in ("penalized_delta_ci_low_s", "penalized_delta_ci_high_s", "penalized_time_delta_mean_s"))
        ax.hlines(i, lo, hi, color=color, lw=1.8)
        ax.plot(value, i, "o", color=color, ms=5)
    ax.set_yticks(range(8), labels, fontsize=10)
    ax.set_ylim(7.65, -.65)
    ax.axvline(0, color="#555555", lw=.8)
    ax.grid(axis="x", alpha=.2)
    ax.set_title(f"Stage 3 / P{problem}: scheduling, development", fontsize=12)
    ax.set_xlabel("Candidate - joint, failure-penalized seconds\nPointwise paired 95% CI; joint was retained", fontsize=10)
    return save(fig, f"stage3_p{problem}_scheduling", output, rows, [path],
        "All eight preregistered scheduling contrasts are shown, separately by design group. "
        "Joint retained for both problems. Confirmation only reran retained joint, so no confirmation improvement comparison is fabricated.")


def collection_figures(round_dir, output):
    rows, sources = collect_official(round_dir)
    if len(rows) != 160 or not all(r["evaluation_complete"] for r in rows):
        raise ValueError("The historical collection figures require the audited completed 160-case collection")
    groups = [(3, "baseline"), (3, "survey"), (4, "baseline"), (4, "survey")]
    labels = [f"P{p}\n{protocol}" for p, protocol in groups]
    values = [[r["total_virtual_time_s"] for r in rows if (r["problem"], r["protocol"]) == key] for key in groups]
    fig, ax = plt.subplots(figsize=(6.6, 4.), layout="constrained")
    ax.boxplot(values, tick_labels=[f"{label}\nn={len(value)}" for label, value in zip(labels, values)], showmeans=True, showfliers=True)
    ax.set_ylabel("Total virtual seconds, complete cases")
    ax.set_title("Official round 1: protocols kept separate\n160/160 full clearance; formal tests not executed", fontsize=12)
    outputs = [save(fig, "round1_completion_time", output, rows, sources,
        "Actual official practice collection; baseline and standard survey remain separate. Boxplots are descriptive, all 160 completed.")]
    fig, ax = plt.subplots(figsize=(6.6, 4.), layout="constrained")
    bottom = np.zeros(4)
    measures = [("Move", "walk_distance_m", .2, "#476b8d"), ("Switch", "switches", 1., "#e5a34b"),
                ("Measure", "measures", 5., "#63a59d"), ("Optical", "clear_attempts", 3., "#bc6a70"),
                ("Clear extra", "clear_successes", 2., "#80608a")]
    for label, field, scale, color in measures:
        mean = [statistics.fmean(r[field]*scale for r in rows if (r["problem"], r["protocol"]) == key) for key in groups]
        ax.bar(range(4), mean, bottom=bottom, label=label, color=color)
        bottom += mean
    ax.set_xticks(range(4), labels)
    ax.set_ylabel("Mean virtual seconds, complete cases")
    ax.set_title("Official round 1: actual action costs", fontsize=12)
    ax.legend(ncol=3, fontsize=10, frameon=False, loc="upper left")
    ax.set_ylim(0, max(bottom)*1.23)
    outputs.append(save(fig, "round1_action_costs", output, rows, sources,
        "Component means use the actual 60/20/60/20 completed cases. Survey travel is acquisition cost, not a normal-policy performance sample."))
    for problem in (3, 4):
        subset = [r for r in rows if r["problem"] == problem]
        counts = Counter((r["N"], r["Ndir"]) for r in subset)
        fig, ax = plt.subplots(figsize=(6.6, 3.8), layout="constrained")
        if problem == 3:
            ax.bar(range(10, 17), [counts[n, 0] for n in range(10, 17)], color="#476b8d")
            ax.set(xticks=range(10, 17), xlabel="Total source count N", ylabel="Audited cases")
        else:
            matrix = np.full((7, 17), np.nan)
            for n in range(10, 17):
                for nd in range(n+1):
                    matrix[n-10, nd] = counts[n, nd]
            chart = ax.imshow(matrix, origin="lower", aspect="auto", cmap="Blues", vmin=0)
            for (n, nd), count in counts.items():
                ax.text(nd, n-10, str(count), ha="center", va="center", fontsize=10,
                        color="white" if count >= max(counts.values())*.6 else "black")
            ax.set(xticks=range(17), yticks=range(7), yticklabels=range(10, 17),
                   xlabel="Directional source count Ndir", ylabel="Total source count N")
            fig.colorbar(chart, ax=ax, label="Audited cases", shrink=.83)
        ax.set_title(f"Official P{problem}: joint composition, n={len(subset)}\nAll first-round cases; descriptive, not fit-only", fontsize=12)
        outputs.append(save(fig, f"round1_p{problem}_composition", output, subset, sources,
            "Observed joint source counts from public post-exit audits. This display uses all 80 first-round cases for description; fitting remains restricted to the original fit split. Invalid P4 support cells are masked."))
    return outputs


def validation_figures(round_dir, output):
    path = round_dir/"validation_summary.json"
    plan = read(round_dir/"plan.json")
    data = read(path)
    planned = {r["case_id"] for r in plan["cases"]}
    if set(r["case_id"] for r in data["cases"]) != planned or not all(r["attempted"] for r in data["cases"]):
        raise ValueError("Fresh validation is unfinished; do not draw partial or empty final figures")
    if any(not (round_dir/"cases"/case/"post_exit_audit.json").exists() for case in planned):
        raise ValueError("Fresh validation lacks terminal audits; final figures deferred")
    outputs = []
    for problem in (3, 4):
        rows = [r for r in data["cases"] if r["problem"] == problem]
        block = data["problems"][str(problem)]
        fig, axes = plt.subplots(2, 1, figsize=(6.6, 5.5), layout="constrained")
        labels, values = [], []
        for arm in ("baseline", "candidate"):
            stats = block["arms"][arm]
            labels.append(f"{arm}\n{stats['verified_full_clear']}/{stats['attempted']} full clear")
            values.append([r["total_virtual_time_s"] for r in rows if r["arm"] == arm and r["verified_full_clear"] and numeric(r["total_virtual_time_s"])])
        for i, value in enumerate(values):
            if value:
                axes[0].boxplot([value], positions=[i], widths=.45, showmeans=True)
        axes[0].set_xticks([0, 1], labels)
        axes[0].set_xlim(-.5, 1.5)
        axes[0].set_ylabel("Complete-case seconds")
        detail = difference_text(block["candidate_minus_baseline"]["penalized_loss_s"], "Penalized C-B")
        axes[0].set_title(f"Fresh official P{problem}: independent arms\n{detail}", fontsize=10)
        components = [components_from_cases(rows, problem, arm) for arm in ("baseline", "candidate")]
        bottom = [0., 0.]
        for key, label, color in COMPONENTS:
            for i, (part, n) in enumerate(components):
                if part is not None:
                    axes[1].bar(i, part[key], bottom=bottom[i], color=color, label=label if i == 0 else None)
                    bottom[i] += part[key]
        axes[1].set_xticks([0, 1], [f"{arm}; cost n={components[i][1]}" for i, arm in enumerate(("baseline", "candidate"))])
        axes[1].set_xlim(-.5, 1.5)
        axes[1].set_ylim(0, max(bottom+[1.])*1.3)
        axes[1].set_ylabel("Recorded mean cost (s)")
        axes[1].legend(ncol=3, fontsize=10, frameon=False, loc="upper left")
        for i, (_, n) in enumerate(components):
            if not n:
                axes[1].text(i, .05, "Unknown, not zero", transform=axes[1].get_xaxis_transform(), ha="center")
        caption = "New official practice only; independent-arm pointwise intervals, never paired. Complete-case times conditional on success; all failures kept in the penalized comparison. Full arm failure-rate intervals and source rows are retained in the source JSON."
        outputs.append(save(fig, f"round2_p{problem}_validation", output, {"cases": rows, "problem_summary": block},
                            [path, round_dir/"plan.json"], caption))
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round1", type=Path, default=ROOT/"results/round1")
    parser.add_argument("--output", type=Path, default=ROOT/"results/pipeline_summary/report_figures")
    parser.add_argument("--validation", type=Path, help="Only after all actual frozen cases have terminal audits")
    args = parser.parse_args()
    plt.rcParams.update({"font.size": 10, "axes.labelsize": 10, "xtick.labelsize": 10, "ytick.labelsize": 10,
                         "pdf.fonttype": 42, "axes.spines.top": False, "axes.spines.right": False})
    selection = read(args.round1/"local/selection.json")
    if selection["selected_variants"] != {"3": "joint_triangular_l1", "4": "joint_triangular_l1"}:
        raise ValueError("This report's Stage1 contrasts must match its frozen L1 selections")
    if read(args.round1/"stage2/selection.json")["selected_variants"] != {"3": "normal", "4": "nosignal"}:
        raise ValueError("This report's Stage2 comparison must match the frozen selections")
    if read(args.round1/"stage3/selection.json")["selected_variants"] != {"3": "joint", "4": "joint"}:
        raise ValueError("This report's scheduling interpretation must match the retained joint schedules")
    outputs = collection_figures(args.round1, args.output)
    for problem in (3, 4):
        outputs.append(paired_plot(args.round1/"local", problem, "joint_triangular_l1", f"stage1_p{problem}_l1_l5",
                                   f"Stage 1 / P{problem}: frozen L1 - L5", args.output))
    outputs.append(paired_plot(args.round1/"stage2", 4, "nosignal", "stage2_p4_nosignal",
                               "Stage 2 / P4: NoSignal - original selector", args.output))
    for problem in (3, 4):
        outputs.append(scheduling_plot(args.round1/"stage3", problem, args.output))
    if args.validation:
        outputs.extend(validation_figures(args.validation, args.output))
    save_json(args.output/"manifest.json", {"figures": [{k: p[k] for k in ("figure", "caption", "outputs", "estimated_minimum_A4_font_pt")} for p in outputs],
        "stage1_confirmation_scope": "Only the published frozen L1/L5 paired_summary.json; no unselected L2/L3 confirmation runs opened",
        "stage2_p3": "Retained original selector; no self-comparison graphic fabricated",
        "fresh_official_figures": bool(args.validation), "script_sha256": sha(__file__),
        "frozen_selection_files": [{"path": relative(args.round1/stage/"selection.json"), "sha256": sha(args.round1/stage/"selection.json")} for stage in ("local", "stage2", "stage3")]})
    print(json.dumps({"figures": len(outputs), "output": str(args.output), "min_A4_font_pt": min(p["estimated_minimum_A4_font_pt"] for p in outputs)}))


if __name__ == "__main__":
    main()
