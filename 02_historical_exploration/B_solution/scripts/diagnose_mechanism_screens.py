"""Read-only, post-screen explanation from already extracted public features.

This does not refit composition, alter formal model_checks statuses, or claim
true source distances/errors. Exact nominal probe radii are separated from
coarse bins and from repeated/neighbor anchor measurements.
"""
from collections import Counter, defaultdict
import argparse
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bsolver.calibration import _compare_scalar, bootstrap_mean, canonical_hash


def rows(path):
    with path.open(encoding="utf-8-sig") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def diagnose(feature_root, output, *, repeats=500):
    feature_root, output = Path(feature_root), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    design = {"version": "post-screen-probe-explanation-v1", "declared_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "scope": "post-screen explanation; primary formal statuses unchanged; fit/development feedback summaries separate; no alpha retuning",
              "metrics": ["radial_600_visibility", "radial_1100_visibility", "radial_1450_visibility", "bin_1250_visibility",
                          "survey_visibility", "neighbor_1m_absolute_report_delta_deg", "complete_only_failed_clears"],
              "probe_radius_rule": "kind=radial and nominal distance within 1e-6m of the stated radius; distances refer to frozen estimate, not true g",
              "neighbor_rule": "measurement_design.kind=neighbor and nearest-observed-point proxy separation in (0,1+1e-7]m",
              "bootstrap_unit": "whole_case", "bootstrap_repeats": repeats,
              "cost_limit": "failed-clear costs are supplemental effects; not used alone to infer or reject a sensor mechanism",
              "feature_sha256": {name: hashlib.sha256((feature_root / name).read_bytes()).hexdigest()
                                   for name in ("cases.jsonl", "measurements.jsonl", "observable_pairs.csv")}}
    write(output / "diagnostic_design.json", design)
    cases = {r["case_group_id"]: r for r in rows(feature_root / "cases.jsonl")}
    values = defaultdict(lambda: defaultdict(list))
    metadata = defaultdict(lambda: {"kinds": Counter(), "nominal_distances_m": Counter(), "source_distance_lower": [],
                                  "source_distance_upper": [], "outer_radius_m": [], "measurements": 0})
    neighbor_actions = set()
    for row in rows(feature_root / "measurements.jsonl"):
        if row["phase"] != "survey" or row["any_cleared_before"]:
            continue
        identity, d = row["case_group_id"], row.get("measurement_design") or {}
        if identity not in cases:
            continue
        case = cases[identity]
        values[identity]["survey_visibility"].append(float(row["visible"]))
        if d.get("kind") == "neighbor":
            neighbor_actions.add((identity, row["sequence"]))
        nominal = d.get("nominal_distance_m")
        if not isinstance(nominal, (float, int)):
            continue
        if d.get("kind") == "radial":
            for radius in (600, 1100, 1450):
                if abs(nominal - radius) <= 1e-6:
                    values[identity][f"radial_{radius}_visibility"].append(float(row["visible"]))
        if 1250 <= nominal < 1500:
            values[identity]["bin_1250_visibility"].append(float(row["visible"]))
            key = (case["source_kind"], case["problem"], case["split"], case.get("model_candidate", "official"))
            summary = metadata[key]
            summary["measurements"] += 1
            summary["kinds"][d.get("kind", "unknown")] += 1
            summary["nominal_distances_m"][f"{nominal:.6f}"] += 1
            interval = d.get("source_distance_interval_m")
            if isinstance(interval, (tuple, list)) and len(interval) == 2:
                summary["source_distance_lower"].append(interval[0])
                summary["source_distance_upper"].append(interval[1])
            if isinstance(d.get("reference_outer_radius_m"), (int, float)):
                summary["outer_radius_m"].append(d["reference_outer_radius_m"])
    with (feature_root / "observable_pairs.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["phase"] != "survey" or row["kind"] != "nearest_observed_point" or not row["reported_angle_delta_deg"]:
                continue
            identity, sequence = row["case_group_id"], int(row["sequence"])
            if (identity, sequence) in neighbor_actions and 0 < float(row["separation_m"]) <= 1 + 1e-7:
                values[identity]["neighbor_1m_absolute_report_delta_deg"].append(abs(float(row["reported_angle_delta_deg"])))
    per_case = {}
    for identity, case in cases.items():
        per_case[identity] = {metric: statistics.mean(sample) for metric, sample in values[identity].items() if sample}
        if case["completed"] and case.get("clear_attempts") is not None and case.get("clear_successes") is not None:
            per_case[identity]["complete_only_failed_clears"] = case["clear_attempts"] - case["clear_successes"]
    groups = defaultdict(list)
    for identity, case in cases.items():
        groups[(case["source_kind"], case["problem"], case["protocol"], case["split"], case["model_candidate"])].append(identity)
    summaries = []
    for key, identities in sorted(groups.items(), key=lambda item: str(item[0])):
        for metric in design["metrics"]:
            sample = [per_case[i][metric] for i in identities if metric in per_case[i]]
            if sample:
                summaries.append({**dict(zip(("source_kind", "problem", "protocol", "split", "model_candidate"), key)),
                                  "metric": metric, **bootstrap_mean(sample, repeats=repeats, seed=0),
                                  "minimum_case_value": min(sample), "maximum_case_value": max(sample)})
    comparisons = []
    for key, local_ids in sorted(groups.items(), key=lambda item: str(item[0])):
        if key[0] != "local":
            continue
        _, problem, protocol, _, candidate = key
        for split in ("fit", "development"):
            official_ids = [i for i, c in cases.items() if c["source_kind"] == "official" and c["problem"] == problem
                            and c["protocol"] == protocol and c["split"] == split]
            for metric in design["metrics"]:
                a = [per_case[i][metric] for i in official_ids if metric in per_case[i]]
                b = [per_case[i][metric] for i in local_ids if metric in per_case[i]]
                if not a and not b:
                    continue
                margin = .1 if "visibility" in metric else .5 if "deg" in metric else max(1., .25 * statistics.mean(a)) if a else 1.
                check = _compare_scalar(a, b, metric=metric, repeats=repeats,
                                        seed=int(canonical_hash([problem, protocol, split, metric])[:8], 16),
                                        absolute_tolerance=margin)
                # The common name is retained for compatibility with existing
                # plot readers, but this diagnostic explicitly includes fit.
                check["n_official_reference"] = check.pop("n_official_development")
                comparisons.append({"model_candidate": candidate, "problem": problem, "protocol": protocol,
                                    "official_reference_split": split, **check,
                                    "scope": "post-screen explanatory interval; not a new confirmatory test and not an automatic exclusion"})
    bin_metadata = []
    for key, value in sorted(metadata.items(), key=lambda item: str(item[0])):
        row = {**dict(zip(("source_kind", "problem", "split", "model_candidate"), key)), **value}
        for field in ("source_distance_lower", "source_distance_upper", "outer_radius_m"):
            sample = row.pop(field)
            row[field + "_range"] = [min(sample), max(sample)] if sample else [None, None]
        bin_metadata.append(row)
    write(output / "case_summaries.json", summaries)
    write(output / "diagnostic_contrasts.json", comparisons)
    write(output / "bin_1250_measurement_design.json", bin_metadata)
    text = ["# 机制筛查后的解释性诊断", "", "输入为已提取公开特征。官方 fit 与 development 分开；"
            "原正式统计状态不改变。这里的半径是相对冻结估计点的名义探针距离，不是真实源距。", "",
            "1250 箱指 1250≤名义距离<1500，可能混入锚点复测/邻点。下面只用 kind=radial 且名义1450m的精确探针作对照；"
            "bin_1250_measurement_design.json 保留箱内类型、名义值及源距离保守区间范围。", "",
            "|模型|题|官方划分|指标|官方局数/均值|本地局数/均值|本地减官方区间|", "|---|---:|---|---|---|---|---|"]
    for row in comparisons:
        if row["protocol"] == "survey" and row["metric"] in ("radial_1100_visibility", "radial_1450_visibility", "neighbor_1m_absolute_report_delta_deg"):
            text.append(f"|{row['model_candidate']}|{row['problem']}|{row['official_reference_split']}|{row['metric']}|"
                        f"{row['n_official_reference']}/{row['official_mean']}|{row['n_local']}/{row['local_mean']}|{row['difference_ci95']}|")
    text.extend(["", "所有区间都是整局层面的解释性诊断；本次家族未作同时显著性校正。重复巨大差异可为模型筛查提供解释，"
                 "但不声称恢复真实R分布或误差公式。确定性排除以独立几何见证为准。失败清除数只作补充，不用于反调模型使耗时吻合。"])
    (output / "report.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    return {"summaries": len(summaries), "comparisons": len(comparisons), "bin_metadata_groups": len(bin_metadata)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap", type=int, default=500)
    args = parser.parse_args()
    print(json.dumps(diagnose(args.features, args.output, repeats=args.bootstrap)))


if __name__ == "__main__":
    main()
