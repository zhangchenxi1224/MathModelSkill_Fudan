"""Build evidence tables from actual saved runs; never fills missing stages."""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import statistics


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    i = (len(values)-1)*fraction
    lo = int(i)
    return values[lo]+(values[min(lo+1, len(values)-1)]-values[lo])*(i-lo)


def summarize(rows, *, local=False):
    planned_count = len(rows)
    rows = [r for r in rows if r.get("attempted", True)]
    numeric = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    complete = [r for r in rows if r["evaluation_complete"]]
    times = [r["total_virtual_time_s"] for r in complete if numeric(r.get("total_virtual_time_s"))]
    def mean(field, sample=None):
        values = [r[field] for r in (rows if sample is None else sample) if numeric(r.get(field))]
        return statistics.fmean(values) if values else None
    losses = [r["total_virtual_time_s"] if r["evaluation_complete"] and numeric(r.get("total_virtual_time_s")) else 360000. for r in rows]
    failed_clears = [r["clear_attempts"]-r["clear_successes"] for r in rows
                     if numeric(r.get("clear_attempts")) and numeric(r.get("clear_successes"))]
    return {
        "planned_or_recorded_cases": planned_count, "not_started": planned_count-len(rows),
        "attempts": len(rows), "complete": len(complete), "failures": len(rows)-len(complete),
        "complete_time_records": len(times),
        "completion_time_mean_s": statistics.fmean(times) if times else None,
        "completion_time_median_s": statistics.median(times) if times else None,
        "completion_time_p90_s": percentile(times, .9),
        "completion_time_max_s": max(times) if times else None,
        "penalized_loss_mean_s": statistics.fmean(losses) if losses else None,
        "average_clear_time_s_mean": mean("average_clear_time_s", complete),
        "walk_distance_m_mean": mean("walk_distance_m"), "measures_mean": mean("measures"),
        "switches_mean": mean("switches"), "clear_attempts_mean": mean("clear_attempts"),
        "clear_successes_mean": mean("clear_successes"),
        "failed_clear_attempts_mean": statistics.fmean(failed_clears) if failed_clears else None,
        "failed_clear_attempts_records": len(failed_clears),
        "program_real_time_s_mean": mean("program_real_time_s"),
        "outer_hull_violations": sum(len(r.get("hull_invariant_violations", [])) for r in rows) if local else None,
        "completion_statistics_condition": "successful cases only; all failures and all-attempt penalized loss shown alongside",
        "action_statistics_condition": "observed records among all attempts, including incomplete effort; missing measurements are not zero",
        "tail_warning": "p90 and max are descriptive sample statistics, not worst-case or reliable worst-5-percent claims",
    }


def csv_write(path, rows):
    keys = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        if not rows:
            return  # Truncate stale data from an earlier invocation.
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def collect_official(round_dir):
    """Preserve launch failures and unknown audits, not just completed audits."""
    round_dir = Path(round_dir)
    inputs, rows = [], []
    plan_path = round_dir/"plan.json"
    planned = read(plan_path).get("cases", []) if plan_path.exists() else []
    if plan_path.exists():
        inputs.append(plan_path)
    index = {case["case_id"]: dict(case) for case in planned}
    if len(index) != len(planned):
        raise ValueError("Duplicate planned official case")
    for folder in sorted((round_dir/"cases").glob("*")):
        if folder.is_dir():
            index.setdefault(folder.name, {"case_id": folder.name})
    for case_id, case in sorted(index.items()):
        folder = round_dir/"cases"/case_id
        docs = {}
        for name in ("assignment.json", "result.json", "post_exit_audit.json"):
            path = folder/name
            docs[name] = read(path) if path.exists() else {}
            if path.exists():
                inputs.append(path)
        assignment, result, audit = (docs[name] for name in ("assignment.json", "result.json", "post_exit_audit.json"))
        for key in ("case_id", "problem", "protocol", "split"):
            if key in assignment and key in case and assignment[key] != case[key]:
                raise ValueError(f"Official assignment differs from plan: {case_id} {key}")
        metadata = {**case, **assignment}
        for doc in (assignment, result, audit):
            for key in ("case_id", "problem", "protocol"):
                if key in doc and key in metadata and doc[key] != metadata[key]:
                    raise ValueError(f"Conflicting official metadata: {case_id} {key}")
        problem = metadata.get("problem", audit.get("problem", result.get("problem")))
        protocol = metadata.get("protocol", audit.get("protocol", result.get("protocol")))
        artifacts = [folder/name for name in ("launch_intent.json", "session.json", "requests.jsonl", "start_failure.json") if (folder/name).exists()]
        inputs.extend(artifacts)
        attempted = bool(result or audit or artifacts)
        if problem not in (3, 4) or protocol is None:
            if not attempted:
                continue
            raise ValueError(f"Attempted official case has no grouping metadata: {case_id}")
        n = audit.get("source_total_post_exit")
        n = audit.get("N") if n is None else n
        ndir = audit.get("directional_total_post_exit")
        ndir = audit.get("Ndir") if ndir is None else ndir
        status = audit.get("status", result.get("status", "attempt_incomplete" if attempted else "not_started"))
        verified = (bool(audit) and status == "complete" and isinstance(n, int) and not isinstance(n, bool)
                    and 10 <= n <= 16 and audit.get("cleared", result.get("clear_successes")) == n)
        rows.append({**result, "case_id": case_id, "problem": problem, "protocol": protocol,
                     "split": metadata.get("split", audit.get("split")), "attempted": attempted,
                     "evaluation_complete": verified, "N": n, "Ndir": ndir,
                     "case_code": audit.get("case_code"), "status": status,
                     "audit_missing": not bool(audit),
                     "total_virtual_time_s": audit.get("total_virtual_time_s", result.get("total_virtual_time_s"))})
    return rows, inputs


def execution_inventory(stage_rows):
    """Count published analysis rows separately from physical runs.

    Only aggregate metadata is opened here; withheld result/performance files
    are never loaded to reconstruct execution counts.
    """
    grouped, sources = {}, []
    for stage, rows in stage_rows:
        stage = Path(stage).resolve()
        origin = stage if any((stage/name).exists() for name in ("run_metadata.json", "aggregate_reliability.json")) else stage.parent
        item = grouped.setdefault(origin, {"stage_root": str(origin), "analysis_inputs": [],
                                         "published_analysis_rows": 0, "published_partitions": {}, "identities": set()})
        item["analysis_inputs"].append(str(stage/"results.json"))
        for row in rows:
            identity = row["case_id"], row["variant"]
            if identity in item["identities"]:
                raise ValueError("Overlapping local analysis inputs would double-count case/variant")
            item["identities"].add(identity)
            item["published_analysis_rows"] += 1
            partition = row.get("partition", "unknown")
            item["published_partitions"][partition] = item["published_partitions"].get(partition, 0)+1
    for origin, item in grouped.items():
        metadata = {}
        for name in ("aggregate_reliability.json", "run_metadata.json"):
            path = origin/name
            if path.exists():
                metadata.update(read(path))
                sources.append(path)
        physical = metadata.get("strategy_runs", metadata.get("runs"))
        if physical is not None and (not isinstance(physical, int) or isinstance(physical, bool) or physical < item["published_analysis_rows"]):
            raise ValueError("Physical execution metadata cannot be smaller than published analysis rows")
        item.pop("identities")
        item.update(physical_strategy_runs=physical,
                    physical_distinct_scenarios=metadata.get("distinct_scenarios_run"),
                    executed_rows_not_in_this_analysis=None if physical is None else physical-item["published_analysis_rows"],
                    interpretation="Analysis rows may omit unreleased confirmation arms; physical execution count comes only from aggregate metadata, never withheld performance")
    return list(grouped.values()), sources


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--round", type=Path, default=Path("results/round1"))
    p.add_argument("--local-stage", type=Path, action="append", default=[])
    p.add_argument("--validation", type=Path, default=Path("results/round2"))
    p.add_argument("--output", type=Path, default=Path("results/pipeline_summary"))
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    official_rows, inputs = collect_official(args.round)
    groups, cases = {}, []
    for row in official_rows:
        groups.setdefault((row["problem"], row["protocol"]), []).append(row)
        cases.append({k: row.get(k) for k in ("case_id", "case_code", "problem", "protocol", "split", "N", "Ndir", "attempted", "audit_missing", "status", "evaluation_complete", "total_virtual_time_s", "walk_distance_m", "measures", "switches", "clear_attempts", "clear_successes", "program_real_time_s")})
    official = [{"source": "official_practice_round1", "problem": key[0], "protocol": key[1], **summarize(rows)} for key, rows in sorted(groups.items())]
    local, stage_rows = [], []
    for stage in dict.fromkeys(path.resolve() for path in args.local_stage):
        result_path = stage/"results.json"
        if not result_path.exists():
            continue
        inputs.append(result_path)
        group = {}
        identities = set()
        rows_from_file = read(result_path)
        stage_rows.append((stage, rows_from_file))
        for row in rows_from_file:
            identity = row["case_id"], row["variant"]
            if identity in identities:
                raise ValueError("Duplicate local case/variant would distort the evidence table")
            identities.add(identity)
            key = (row["pool"], row["problem"], row["composition_model"], row["partition"], row["variant"])
            group.setdefault(key, []).append(row)
        for key, rows in sorted(group.items()):
            local.append({"source": "local_simulator", "stage_path": stage.as_posix(),
                          **dict(zip(("pool", "problem", "composition_model", "partition", "variant"), key)),
                          "interpretation": "predeclared mechanism design mixture; not an official population average", **summarize(rows, local=True)})
    execution_counts, execution_sources = execution_inventory(stage_rows)
    inputs.extend(execution_sources)
    validation_path = args.validation/"validation_summary.json"
    validation = read(validation_path) if validation_path.exists() else None
    validation_metrics, validation_cases = [], []
    if validation:
        inputs.append(validation_path)
        validation_rows, validation_inputs = collect_official(args.validation)
        inputs.extend(validation_inputs)
        validation_groups = {}
        for row in validation_rows:
            validation_groups.setdefault((row["problem"], row["protocol"]), []).append(row)
            validation_cases.append({k: row.get(k) for k in ("case_id", "case_code", "problem", "protocol", "N", "Ndir", "attempted", "audit_missing", "status", "evaluation_complete", "total_virtual_time_s", "average_clear_time_s", "walk_distance_m", "measures", "switches", "clear_attempts", "clear_successes", "program_real_time_s")})
        validation_metrics = [{"source": "official_practice_round2", "problem": key[0], "protocol": key[1], **summarize(rows)} for key, rows in sorted(validation_groups.items())]
    payload = {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
               "official_collection": official, "local_stages": local,
               "local_execution_inventory": execution_counts,
               "fresh_official_validation": validation,
               "fresh_official_validation_metrics": validation_metrics,
               "source_files": [{"path": str(x.resolve()), "sha256": hashlib.sha256(x.read_bytes()).hexdigest()} for x in inputs],
               "formal_test_status": "not executed; separate explicit authorization required",
               "missing_stages": "Omitted, never replaced by simulated or planned results"}
    (args.output/"evidence_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_write(args.output/"official_collection.csv", official)
    csv_write(args.output/"official_cases.csv", cases)
    csv_write(args.output/"local_stages.csv", local)
    csv_write(args.output/"official_validation_metrics.csv", validation_metrics)
    csv_write(args.output/"official_validation_cases.csv", validation_cases)
    lines = ["# 实际执行证据表", "", "此文件由原始结果生成；调查与正常求解分开，三个本地场景池分开。失败耗时不作为完成时间。", "", "|来源|题|协议|实际局数|全清|失败|完成均值s|移动均值m|测量均值|清除失败均值|", "|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in official:
        f = lambda k: "未知" if r[k] is None else f"{r[k]:.2f}"
        lines.append(f"|官方演练首轮|{r['problem']}|{r['protocol']}|{r['attempts']}|{r['complete']}|{r['failures']}|{f('completion_time_mean_s')}|{f('walk_distance_m_mean')}|{f('measures_mean')}|{f('failed_clear_attempts_mean')}|")
    lines.extend(["", "完整本地分池表见local_stages.csv；逐局与输入SHA256见JSON。缺失阶段不填写任何推算成绩。正式测试未执行。", ""])
    for item in execution_counts:
        physical = item["physical_strategy_runs"]
        lines.append(f"本地阶段 `{Path(item['stage_root']).name}`：本表发布分析 {item['published_analysis_rows']} 行；元数据记录物理执行 {physical if physical is not None else '未知'} 局。差额可能是未发布确认臂，不据此读取或评价其性能。")
    if validation_metrics:
        lines += ["", "新官方独立两组的完整次数、题目平均清除时间、移动/调用次数、程序墙钟时间和样本p90/max另存official_validation_metrics.csv；逐局含案例编码的记录在official_validation_cases.csv。均值差与独立组置信区间以round2/validation_summary为准，未与首轮或本地数据合并。"]
    (args.output/"evidence_summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"official_cases": len(cases), "official_groups": len(official), "local_groups": len(local), "fresh_validation_present": validation is not None}))


if __name__ == "__main__":
    main()
