"""Observable outcome diagnostics for the already frozen stage2 protocols.

Read only stage2 logs; whole-case comparisons, split/pool/composition separated.
The result does not identify true noise or prove an outcome-count mediation.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import statistics


def load(path):
    return json.loads(path.read_text(encoding="utf8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_case(directory, case, variant):
    result = load(directory / "result.json")
    actions, seen, issues = [], set(), []
    with gzip.open(directory / "requests.jsonl.gz", "rt", encoding="utf8") as stream:
        for raw in stream:
            row = json.loads(raw)
            response, request = row.get("response") or {}, row.get("request") or {}
            if response.get("accepted") is True and request.get("request_id") not in seen:
                seen.add(request.get("request_id"))
                if row.get("path") == "/measure":
                    actions.append(row)
    decisions = []
    with gzip.open(directory / "decisions.jsonl.gz", "rt", encoding="utf8") as stream:
        for raw in stream:
            row = json.loads(raw)
            if row.get("event") == "measure":
                decisions.append(row)
    if len(actions) != len(decisions):
        issues.append("accepted_measure_decision_count_mismatch")
    counts = defaultdict(Counter)
    counts["local_active_sensing"]
    for action, decision in zip(actions, decisions):
        request, response = action["request"], action["response"]
        actual = [request["position"]["x"], request["position"]["y"]]
        if (decision.get("position") != actual or decision.get("knowledge", {}).get("channel") != request["channel"]
                or any(decision.get("response", {}).get(k) != response.get(k) for k in ("measure_result", "svd_deg", "virtual_time_s"))):
            issues.append("accepted_measure_decision_alignment_mismatch")
            continue
        reason = decision.get("reason") or "unknown_reason"
        outcome = response.get("measure_result")
        if outcome not in ("direction", "near", "no_signal"):
            issues.append("invalid_measure_outcome")
            continue
        counts[reason][outcome] += 1
    common = {k: case[k] for k in ("case_id", "problem", "pool", "partition", "composition_model", "mechanism_id")}
    rows = []
    for reason, tally in counts.items():
        n = sum(tally.values())
        rows.append({**common, "variant": variant, "measurement_reason": reason, "measures": n,
                     **{k: tally[k] for k in ("direction", "near", "no_signal")},
                     "direction_rate": tally["direction"] / n if n else None,
                     "near_rate": tally["near"] / n if n else None,
                     "no_signal_rate": tally["no_signal"] / n if n else None,
                     "whole_case_clear_attempts": result.get("clear_attempts"),
                     "whole_case_fallback_targets": result.get("fallback_targets"),
                     "evaluation_complete": result.get("evaluation_complete")})
    return rows, issues, {name: sha(directory / name) for name in ("requests.jsonl.gz", "decisions.jsonl.gz", "result.json")}


def write_csv(path, rows):
    import csv
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        writer.writerows(rows)


def run(directory, output):
    directory, output = Path(directory), Path(output)
    manifest = load(directory / "manifest.json")
    if manifest.get("stage") != 2:
        raise ValueError("This diagnostic is pre-scoped to stage2 only")
    selection = load(directory / "selection.json")
    clone = dict(selection)
    expected = clone.pop("selection_sha256")
    encoded = json.dumps(clone, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    if hashlib.sha256(encoded).hexdigest() != expected or selection.get("manifest_sha256") != manifest["manifest_sha256"]:
        raise ValueError("Invalid frozen stage2 selection")
    rows, issues, provenance = [], [], []
    for case in manifest["scenarios"]:
        arms = manifest["arm_definitions"][str(case["problem"])]
        if case["partition"] == "confirmation":
            allowed = {manifest["baseline"], selection["selected_variants"][str(case["problem"])]}
            arms = [a for a in arms if a["variant"] in allowed]
        for arm in arms:
            path = directory / case["partition"] / "runs" / case["pool"] / case["case_id"] / arm["variant"]
            case_rows, errors, hashes = parse_case(path, case, arm["variant"])
            rows.extend(case_rows)
            issues.extend({"case_id": case["case_id"], "variant": arm["variant"], "issue": e} for e in errors)
            provenance.append({"case_id": case["case_id"], "variant": arm["variant"], "source_sha256": hashes})
    groups = defaultdict(list)
    for row in rows:
        for mechanism in ("__design_mixture__", row["mechanism_id"]):
            key = tuple(row[k] for k in ("partition", "problem", "pool", "composition_model", "variant", "measurement_reason")) + (mechanism,)
            groups[key].append(row)
    aggregate = []
    for key, values in sorted(groups.items()):
        out = dict(zip(("partition", "problem", "pool", "composition_model", "variant", "measurement_reason", "mechanism_id"), key))
        out.update(cases=len(values), cases_with_measurements=sum(r["measures"] > 0 for r in values))
        out.update({k: sum(r[k] for r in values) for k in ("measures", "direction", "near", "no_signal")})
        for outcome in ("direction", "near", "no_signal"):
            rates = [r[f"{outcome}_rate"] for r in values if r[f"{outcome}_rate"] is not None]
            out[f"case_mean_{outcome}_rate"] = statistics.fmean(rates) if rates else None
        aggregate.append(out)
    index = {(r["case_id"], r["variant"], r["measurement_reason"]): r for r in rows}
    pairs = []
    for row in rows:
        if row["variant"] != "nosignal":
            continue
        base = index.get((row["case_id"], "normal", row["measurement_reason"]))
        if base is None:
            issues.append({"case_id": row["case_id"], "issue": "unpaired_measurement_reason", "reason": row["measurement_reason"]})
            continue
        out = {k: row[k] for k in ("case_id", "problem", "partition", "pool", "composition_model", "mechanism_id", "measurement_reason")}
        for field in ("measures", "direction", "near", "no_signal", "whole_case_clear_attempts", "whole_case_fallback_targets"):
            out[f"delta_{field}"] = row[field] - base[field]
        out["both_phase_nonempty"] = row["measures"] > 0 and base["measures"] > 0
        for outcome in ("direction", "near", "no_signal"):
            out[f"delta_{outcome}_rate"] = row[f"{outcome}_rate"] - base[f"{outcome}_rate"] if out["both_phase_nonempty"] else None
        pairs.append(out)
    paired_groups = defaultdict(list)
    for row in pairs:
        for mechanism in ("__design_mixture__", row["mechanism_id"]):
            key = tuple(row[k] for k in ("partition", "problem", "pool", "composition_model", "measurement_reason")) + (mechanism,)
            paired_groups[key].append(row)
    contrasts = []
    for key, values in sorted(paired_groups.items()):
        out = dict(zip(("partition", "problem", "pool", "composition_model", "measurement_reason", "mechanism_id"), key))
        out.update(pairs=len(values), both_phase_nonempty_pairs=sum(r["both_phase_nonempty"] for r in values))
        for field in values[0]:
            if field.startswith("delta_"):
                valid = [r[field] for r in values if r[field] is not None]
                out[f"mean_{field}"] = statistics.fmean(valid) if valid else None
        contrasts.append(out)
    output.mkdir(parents=True, exist_ok=True)
    for name, data in (("case_outcomes", rows), ("group_totals", aggregate), ("paired_case_deltas", pairs), ("paired_group_deltas", contrasts)):
        (output / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")
        write_csv(output / f"{name}.csv", data)
    summary = {"stage": 2, "audited_run_logs": len(provenance), "measure_decisions_checked": sum(r["measures"] for r in rows),
               "issue_count": len(issues), "issues": issues, "manifest_sha256": sha(directory / "manifest.json"),
               "selection_sha256": sha(directory / "selection.json"), "case_source_hashes": provenance,
               "scope": "Observed category by exact measurement reason; paired whole cases; no independent action-level inference; no Stage1 or unselected confirmation log access."}
    (output / "provenance.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf8")
    lines = ["# 第二阶段测量结果诊断", "", "每行比较同一场景 NoSignal−normal；开发/确认、问题、池、组成分别保留。以下仅局部主动测量，完整 CSV 另按原始 measurement_reason 分开，不混为全日志比例。", "",
             "|分区|题|池|组成|配对局|测量数差|方向数差|near数差|无信号数差|全局光学尝试差|全局兜底目标差|",
             "|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in contrasts:
        if row["measurement_reason"] == "local_active_sensing" and row["mechanism_id"] == "__design_mixture__":
            lines.append("|" + "|".join(str(row[k]) for k in ("partition", "problem", "pool", "composition_model", "pairs")) + "|" +
                         "|".join(f"{row[f'mean_delta_{k}']:.5f}" for k in ("measures", "direction", "near", "no_signal", "whole_case_clear_attempts", "whole_case_fallback_targets")) + "|")
    lines += ["", "全局清除尝试/兜底目标差附在局部测量表中只是整局关联描述，未把这些成本全部归因给某一种反馈。两臂测点随策略变化，反馈差不识别真实误差，也不构成因果中介证明；未按动作数伪增统计样本量。确认区 P3 仅有冻结 normal 单臂，不能制造 NoSignal 配对。", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf8")
    return {k: v for k, v in summary.items() if k != "case_source_hashes"}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(run(a.input, a.output), ensure_ascii=False, indent=2))
