"""Plot saved paired local differences, keeping pools and models separate."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--partition", choices=("development", "confirmation"), required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    rows = json.loads((args.input/"paired_summary.json").read_text(encoding="utf-8"))
    rows = [r for r in rows if r["partition"] == args.partition and r["noise_model"] == "all" and r.get("mechanism_id", "__design_mixture__") == "__design_mixture__"]
    if not rows:
        raise SystemExit("No actual paired rows match this partition; no plot fabricated")
    args.output.mkdir(parents=True, exist_ok=True)
    colors = {"calibrated": "#366a94", "broad": "#a67924", "stress": "#96505a"}
    fig, axes = plt.subplots(1, 2, figsize=(14, max(4, max(sum(r['problem']==p for r in rows) for p in (3, 4))*.38+1.5)), constrained_layout=True)
    plotted = []
    for problem, ax in zip((3, 4), axes):
        ax.set_title(f"P{problem}: {args.partition}")
        group = sorted([r for r in rows if r["problem"] == problem], key=lambda r:(r["pool"], r["composition_model"], r["variant"]))
        if not group:
            ax.text(.5, .5, "No candidate comparison in this problem", transform=ax.transAxes, ha="center")
            ax.set_axis_off()
            continue
        for i, r in enumerate(group):
            value, lo, hi = (r[k] for k in ("penalized_time_delta_mean_s", "penalized_delta_ci_low_s", "penalized_delta_ci_high_s"))
            if lo is not None and hi is not None:
                ax.hlines(i, lo, hi, color=colors[r["pool"]], linewidth=2)
            ax.plot(value, i, "o", color=colors[r["pool"]])
            plotted.append(r)
        labels = [f"{r['pool']} / {r['composition_model'].replace('_joint','')} / {r['variant']}\nn={r['n_pairs']}; failures B/C={r['baseline_failures']}/{r['candidate_failures']}" for r in group]
        ax.set_yticks(range(len(group)), labels, fontsize=8)
        ax.invert_yaxis()
        ax.axvline(0, color="black", linewidth=.8)
        ax.set_xlabel("Candidate minus stage baseline, virtual seconds\nnegative = lower cost; pointwise paired bootstrap 95% CI")
        ax.grid(axis="x", alpha=.2)
    fig.suptitle("Local paired experiments: separate pools and composition models\nMechanism design mixtures are not official expected performance; failure loss fixed at 360000 s", fontsize=11)
    for extension in ("png", "pdf"):
        fig.savefig(args.output/f"paired_{args.partition}.{extension}", dpi=160)
    plt.close(fig)
    (args.output/f"paired_{args.partition}_source.json").write_text(json.dumps({"input": str(args.input.resolve()), "partition": args.partition, "actual_rows": plotted}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"plotted_groups": len(plotted), "output": str(args.output)}))


if __name__ == "__main__":
    main()
