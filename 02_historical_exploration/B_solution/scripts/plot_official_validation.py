"""Figures for actual independent-arm official practice validation results."""
import argparse
import json
import hashlib
import math
from pathlib import Path
import random
import statistics
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


COMPONENT_KEYS = ("movement_s", "switch_s", "measurement_s", "optical_s", "laser_s")


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def difference_text(difference, label):
    value, interval = difference.get("difference"), difference.get("ci95", [None, None])
    if not numeric(value):
        return f"{label}: insufficient actual arms"
    if not (len(interval) == 2 and all(numeric(v) for v in interval)):
        return f"{label}: {value:+.1f} s; CI unavailable (small n)"
    return f"{label}: {value:+.1f} s; independent 95% CI [{interval[0]:.1f}, {interval[1]:.1f}]"


def components_from_cases(cases, problem, arm):
    chosen = [r["components"] for r in cases if r["problem"] == problem and r["arm"] == arm and r["attempted"]
              and all(numeric(r.get("components", {}).get(k)) for k in COMPONENT_KEYS)]
    return ({key: statistics.fmean(r[key] for r in chosen) for key in COMPONENT_KEYS} if chosen else None), len(chosen)


def plot_round(round_dir, output=None):
    source = Path(round_dir)/"validation_summary.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    output = Path(output) if output else Path(round_dir)/"figures"
    output.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 2, figsize=(14, 10.8), constrained_layout=True)
    rng = random.Random(20260911)
    for i, problem in enumerate((3, 4)):
        block = data["problems"][str(problem)]
        ax, cost = axes[i]
        arms = ["baseline", "candidate"]
        times = [[r["total_virtual_time_s"] for r in data["cases"] if r["problem"] == problem and r["arm"] == arm
                  and r["verified_full_clear"] and numeric(r.get("total_virtual_time_s"))] for arm in arms]
        for index, values in enumerate(times):
            if values:
                ax.boxplot([values], positions=[index+1], widths=.45, showfliers=False)
                ax.scatter([index+1+rng.uniform(-.10, .10) for _ in values], values, s=17, alpha=.65)
        labels = []
        for arm in arms:
            stats = block["arms"][arm]
            ci = stats.get("failure_rate_wilson95", [None, None])
            interval = f"failure 95% [{100*ci[0]:.1f}%, {100*ci[1]:.1f}%]" if ci and all(numeric(v) for v in ci) else "failure interval unavailable"
            labels.append(f"{arm}\n{stats['verified_full_clear']}/{stats['attempted']} full clear; {stats['not_started']} not started\n{interval}")
        ax.set_xticks([1, 2], labels, fontsize=8)
        ax.set_xlim(.5, 2.5)
        ax.set_ylabel("Virtual completion time (s), completed cases only")
        differences = block["candidate_minus_baseline"]
        detail = difference_text(differences["verified_completion_time_s"], "C - B completed")
        penalty = difference_text(differences.get("penalized_loss_s", {}), "C - B penalized")
        ax.set_title(f"P{problem}: fresh randomized independent cases\n{detail}\n{penalty}", fontsize=9)
        ax.grid(axis="y", alpha=.2)
        bottom = [0., 0.]
        complete_components = [components_from_cases(data["cases"], problem, arm) for arm in arms]
        for key, label, color in [("movement_s", "movement", "#416a91"), ("switch_s", "switch", "#eba653"), ("measurement_s", "measure", "#61a9a2"), ("optical_s", "optical attempts", "#bd6570"), ("laser_s", "successful clear extra", "#80608a")]:
            first_available = next((k for k, (v, _) in enumerate(complete_components) if v is not None), -1)
            for j, (components, count) in enumerate(complete_components):
                if components is not None:
                    cost.bar([j+1], [components[key]], bottom=[bottom[j]], color=color,
                             label=label if j == first_available else None, width=.55)
                    bottom[j] += components[key]
        cost_labels = []
        for j, arm in enumerate(arms):
            known = complete_components[j][1]
            attempted = block["arms"][arm]["attempted"]
            cost_labels.append(f"{arm}\ncomponent records {known}/{attempted} attempts")
            if not known:
                cost.text(j+1, .04, "unknown; not zero", transform=cost.get_xaxis_transform(), ha="center", fontsize=8)
        cost.set_xticks([1, 2], cost_labels, fontsize=8)
        cost.set_xlim(.5, 2.5)
        cost.set_ylabel("Mean observed effort (s), complete component records")
        cost.set_title(f"P{problem}: cost decomposition with actual denominator\nObserved failures included; missing component records not imputed", fontsize=9)
        if i == 0 and any(v is not None for v, _ in complete_components):
            cost.legend(fontsize=8, loc="upper left")
    fig.suptitle("Official PRACTICE validation; formal tests not executed\nNo same-world pairing; confidence intervals are pointwise and do not certify rare-failure safety", fontsize=11)
    for suffix in ("png", "pdf"):
        fig.savefig(output/f"official_validation.{suffix}", dpi=160)
    plt.close(fig)
    (output/"official_validation_source.json").write_text(json.dumps({"input": str(source.resolve()),
        "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "source_kind": "official_practice",
        "problems": data["problems"], "cases": data["cases"],
        "cost_denominator": "attempted cases with all five recorded components, separately per arm/problem"}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"actual_attempts": sum(r["attempted"] for r in data["cases"]), "output": str(output)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--round", type=Path, default=Path("results/round2"))
    args = p.parse_args()
    print(json.dumps(plot_round(args.round)))


if __name__ == "__main__":
    main()
