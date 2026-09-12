"""Read a completed Q2 study and export reproducible scientific figures.

Example: python plot_results.py --input results/q2_precision_v2
Only files beneath --output are written. Input JSON and experiment code remain
unchanged. Sample summaries are never relabeled as worst-case guarantees.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "vendor"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Circle
import numpy as np

from q2_geometry import lens_centers, radial_limit


ORDER = ("active", "fixed", "precision_800", "precision")
LABELS = {"active": "Active", "fixed": "Fixed", "precision_800": "Precision (800 m)",
          "precision": "Precision"}
COLORS = {"active": "#33658A", "fixed": "#AA6030", "precision_800": "#2F826F",
          "precision": "#79549A"}


def style_axis(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#E5E9EE", linewidth=.7)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", labelsize=9)


def save_figure(fig, output, stem):
    paths = []
    for suffix in ("png", "pdf"):
        path = output / f"{stem}.{suffix}"
        fig.savefig(path, dpi=190, facecolor="white", bbox_inches="tight")
        paths.append(path)
    plt.close(fig)
    return paths


def comparison(summary, output, source_name):
    records = summary["summaries"]
    counts = {records[key]["n"] for key in ORDER}
    if len(counts) != 1:
        raise ValueError("Plot requires equally sized strategy groups")
    count = counts.pop()
    fig, axes = plt.subplots(2, 2, figsize=(13.1, 8.7))
    fig.subplots_adjust(left=.075, right=.975, bottom=.135, top=.855, hspace=.43, wspace=.22)
    fig.suptitle("Q2: precision and movement after two measurements", fontsize=19, fontweight="bold", x=.075, ha="left", y=.975)
    fig.text(.075, .928,
             f"Deterministic synthetic design  |  {count} worlds per strategy  |  "
             f"{summary['public_first_contexts']} distinct public first-observation contexts",
             fontsize=10.5, color="#52616B")
    xs = np.arange(len(ORDER))
    labels = [LABELS[key] for key in ORDER]
    colors = [COLORS[key] for key in ORDER]

    ax = axes[0, 0]
    means = [records[key]["mean_posterior_mec_radius_m"] for key in ORDER]
    maxima = [records[key]["max_posterior_mec_radius_m"] for key in ORDER]
    for i, color in enumerate(colors):
        ax.plot([i, i], [means[i], maxima[i]], color=color, linewidth=4, alpha=.5)
        ax.scatter(i, means[i], s=80, color=color, edgecolor="white", zorder=3)
        ax.scatter(i, maxima[i], s=85, color=color, marker="^", edgecolor="white", zorder=3)
        ax.annotate(f"{means[i]:.1f}", (i, means[i]), xytext=(-11, -17), textcoords="offset points", fontsize=9)
        ax.annotate(f"{maxima[i]:.1f}", (i, maxima[i]), xytext=(-11, 9), textcoords="offset points", fontsize=9)
    ax.set(title="A  Posterior outer-hull uncertainty", ylabel="Minimum enclosing circle radius (m)",
           xticks=xs, xticklabels=labels, ylim=(0, max(maxima) * 1.25), xlim=(-.5, 3.5))
    ax.legend(handles=[Line2D([], [], marker="o", color="#66727D", linestyle="none", label="Mean"),
                       Line2D([], [], marker="^", color="#66727D", linestyle="none", label="Sample maximum")],
              frameon=False, loc="upper right", fontsize=8.5)

    ax = axes[0, 1]
    moves = [records[key]["mean_movement_m"] for key in ORDER]
    ax.bar(xs, np.array(moves) / 5., color=colors, width=.55)
    for i, meters in enumerate(moves):
        ax.text(i, meters / 5. + 4., f"{meters / 5.:.1f} s\n{meters:.0f} m", ha="center", fontsize=9)
    ax.set(title="B  Movement from the first to second station", ylabel="Mean movement time at 5 m/s (s)",
           xticks=xs, xticklabels=labels, ylim=(0, max(moves) / 5. * 1.27))

    ax = axes[1, 0]
    fractions = [100. * records[key]["two_measurement_certified_fraction"] for key in ORDER]
    ax.bar(xs, fractions, color=colors, width=.55)
    for i, fraction in enumerate(fractions):
        ax.text(i, fraction + 1.1, f"{fraction:.1f}%", ha="center", fontsize=10)
    ax.set(title="C  Already eligible for certified clearance", ylabel="Fraction after two measurements (%)",
           xticks=xs, xticklabels=labels, ylim=(0, max(fractions) * 1.28))
    ax.text(.03, .96, "Outer-hull radius <= 19.999 m", transform=ax.transAxes, va="top", fontsize=8.5, color="#52616B")

    ax = axes[1, 1]
    distances = sorted(summary["by_distance"], key=float)
    for key in ORDER:
        ys = [summary["by_distance"][value][key]["mean_posterior_mec_radius_m"] for value in distances]
        ax.plot([float(value) for value in distances], ys, marker="o", markersize=4,
                color=COLORS[key], linewidth=1.8, label=LABELS[key])
    ax.axhline(19.999, linestyle=":", linewidth=1., color="#737D86")
    ax.set(title="D  Sensitivity to source distance", xlabel="Source distance from first station (m)",
           ylabel="Mean posterior circle radius (m)", xticks=[float(value) for value in distances], ylim=(0, None))
    ax.legend(frameon=False, ncol=2, fontsize=8, loc="upper left")
    for ax in axes.flat:
        style_axis(ax)
        ax.title.set_fontsize(11.5)
        ax.title.set_fontweight("bold")
        ax.title.set_position((.5, 1.045))
    fig.text(.075, .065, "Sample maxima are not worst-case proofs. The design is not an estimated official source distribution.",
             fontsize=9.5, color="#35454F")
    fig.text(.075, .036, f"Movement excludes sensing and clearance actions. Source: {source_name}/synthetic_summary.json",
             fontsize=8.5, color="#61707B")
    return save_figure(fig, output, "q2_comparison")


def geometry(convergence, output, source_name):
    eps = 1.0051
    angles = np.linspace(-90. + eps, 90. - eps, 1801)
    radii = np.array([radial_limit(value, eps) for value in angles])
    boundary_x = radii * np.cos(np.radians(angles))
    boundary_y = radii * np.sin(np.radians(angles))
    fig, axes = plt.subplots(1, 2, figsize=(13.1, 6.7))
    fig.subplots_adjust(left=.065, right=.98, bottom=.16, top=.835, wspace=.20)
    fig.suptitle("Q2: certified reception lens and second-station choices", fontsize=18, fontweight="bold", x=.065, ha="left", y=.965)
    fig.text(.065, .915, "First station s = (0, 0) m  |  Reported bearing = 0 degrees  |  Angular tolerance = 1.0051 degrees",
             fontsize=10.5, color="#52616B")
    centers = lens_centers((0., 0.), 0., eps)
    for ax in axes:
        ax.fill(boundary_x, boundary_y, facecolor="#D5EADF", edgecolor="#417960", linewidth=1.4,
                label="Certified reception lens")
        ax.scatter([0.], [0.], marker="*", s=130, color="#26313D", zorder=6)
        ax.axhline(0., linewidth=.6, color="#CAD2D9")
        ax.axvline(0., linewidth=.6, color="#CAD2D9")
        ax.set_aspect("equal", adjustable="box")
        ax.set(xlabel="x (m)", ylabel="y (m)")
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(labelsize=9)
    ax = axes[0]
    for i, center in enumerate(centers):
        color = "#AA6030" if i < 2 else "#33658A"
        ax.add_patch(Circle(center, 1000., fill=False, edgecolor=color, linewidth=.9,
                            linestyle="-" if i % 2 == 0 else "--", alpha=.8))
        ax.scatter(*center, color=color, marker="+", s=45, linewidth=1.)
    ax.set(xlim=(-1080., 2080.), ylim=(-1080., 1080.), title="A  Intersection of four radius-1000 m disks")
    ax.legend(handles=[Line2D([], [], color="#AA6030", label="Centers at r = 5 m, angle +/- epsilon"),
                       Line2D([], [], color="#33658A", label="Centers at r = 1000 m, angle +/- epsilon")],
              frameon=True, facecolor="white", edgecolor="none", framealpha=.97,
              fontsize=8, loc="lower left")
    ax.text(30., -150., "First station", fontsize=8.5, color="#26313D")

    ax = axes[1]
    offsets = {"active": (10, -19), "fixed": (10, -17), "precision_800": (-105, 16), "precision": (9, 16)}
    for key in ORDER:
        point = convergence[key]["point"]
        ax.plot([0., point[0]], [0., point[1]], color=COLORS[key], linewidth=.9, linestyle=":", alpha=.8)
        ax.scatter(*point, s=70, color=COLORS[key], edgecolor="white", linewidth=.8, zorder=5)
        ax.annotate(LABELS[key], point, xytext=offsets[key], textcoords="offset points", fontsize=9,
                    color=COLORS[key], fontweight="bold")
    ax.set(xlim=(-60., 1120.), ylim=(-80., 900.), title="B  Canonical choices from the frozen result")
    for ax in axes:
        ax.title.set_fontsize(11.5)
        ax.title.set_fontweight("bold")
    fig.text(.065, .087, "The four-disk intersection certifies reception under the shared hidden-radius model; it does not certify localization.",
             fontsize=9.5, color="#35454F")
    fig.text(.065, .050, "Without target-domain clipping this lens is exact. Domain clipping can enlarge the safe set. "
             f"Points: {source_name}/bound_convergence.json", fontsize=8.5, color="#61707B")
    return save_figure(fig, output, "q2_reception_lens")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "results" / "q2_precision_v2")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "figures")
    args = parser.parse_args()
    source, output = args.input.resolve(), args.output.resolve()
    if not (source / "completion.json").is_file():
        raise SystemExit("The selected study has no completion.json; refusing to plot a partial run")
    names = ("synthetic_summary.json", "bound_convergence.json", "completion.json")
    payloads = {name: (source / name).read_bytes() for name in names}
    records = {name: json.loads(value) for name, value in payloads.items()}
    output.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "pdf.fonttype": 42, "axes.labelcolor": "#26313D", "text.color": "#26313D"})
    files = comparison(records["synthetic_summary.json"], output, source.name)
    files += geometry(records["bound_convergence.json"], output, source.name)
    manifest = {"input_directory": str(source), "output_directory": str(output),
                "input_sha256": {name: hashlib.sha256(value).hexdigest() for name, value in payloads.items()},
                "files": [str(path) for path in files],
                "interpretation": "Synthetic sample summaries; sample maxima are not worst-case proofs.",
                "first_context_count": records["synthetic_summary.json"]["public_first_contexts"],
                "worlds_per_strategy": records["synthetic_summary.json"]["distinct_worlds"]}
    (output / "figure_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
