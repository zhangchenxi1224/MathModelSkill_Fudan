"""Plot independently confirmed local paired effects from the saved summary."""
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/p4_refinement_diagnostics"


def main():
    data = json.loads((OUT / "iteration_summary.json").read_text(encoding="utf-8"))
    names = {"calibrated": "Calibrated design", "broad": "Broad legal", "stress": "Boundary stress"}
    fig, ax = plt.subplots(figsize=(10, 4.6), constrained_layout=True)
    labels = []
    for i, row in enumerate(data["contrasts"]):
        value, low, high = row["mean_delta_s"], *row["ci95_s"]
        color = "#07836D" if row["comparator"] == "current" else "#567795"
        label = "vs current" if row["comparator"] == "current" else "vs coverage only"
        labels.append(f"{names[row['pool']]}: {label} (n={row['n']})")
        ax.errorbar(value, i, xerr=[[value-low], [high-value]], fmt="o", color=color, capsize=4, lw=2)
    ax.set_yticks(range(len(labels)), labels)
    ax.invert_yaxis()
    ax.axvline(0, color="#6B7280", linestyle="--", lw=1)
    ax.set(title="Independent local confirmation: combined refinement\nPaired mean difference and pointwise 95% bootstrap interval",
           xlabel="Total virtual seconds: combined minus reference (negative = faster)")
    ax.grid(axis="x", alpha=.15)
    fig.savefig(OUT / "confirmation_paired_effects.png", dpi=170)
    fig.savefig(OUT / "confirmation_paired_effects.pdf")
    plt.close(fig)
    fig, axes = plt.subplots(1, 3, figsize=(12, 4.5), constrained_layout=True)
    variants = ("current", "coverage", "local_cover", "combined_cover")
    for ax, pool in zip(axes, names):
        rows = {r["variant"]:r for r in data["groups"] if r["partition"] == "development" and r["pool"] == pool}
        values = [rows[v]["total_virtual_time_s"]/60 for v in variants]
        ax.bar(range(4), values, color=("#718096", "#567795", "#77B1A3", "#07836D"))
        ax.set_xticks(range(4), ("Current", "Coverage", "Optical", "Both"), rotation=30, ha="right")
        ax.set_title(f"{names[pool]}\nn={rows['current']['n']} worlds per arm")
        ax.set_ylim(0, max(values)*1.14)
        ax.grid(axis="y", alpha=.15)
        for i, v in enumerate(values):
            ax.text(i, v+1.2, f"{v:.1f}", ha="center", fontsize=9)
    axes[0].set_ylabel("Mean complete-case virtual minutes")
    fig.suptitle("Development ablation: four variants on identical fixed environments")
    fig.savefig(OUT / "development_ablation.png", dpi=170)
    plt.close(fig)
    print("Saved confirmation_paired_effects.png/.pdf and development_ablation.png")


if __name__ == "__main__":
    main()
