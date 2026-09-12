"""Read-only extraction of the frozen candidate's real official practice data."""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import statistics
import zipfile

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent.parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def write_csv(path, rows):
    columns = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def quantile(values, p):
    values = sorted(values)
    i = (len(values)-1)*p
    lo = math.floor(i)
    return values[lo]+(values[math.ceil(i)]-values[lo])*(i-lo)


def export():
    OUT.mkdir(parents=True, exist_ok=True)
    summary_path = ROOT/"results/round2/validation_summary.json"
    candidate_path = ROOT/"results/round2/candidate.json"
    validation = read(summary_path)
    plan = read(ROOT/"results/round2/plan.json")
    assignments = [x for x in plan["cases"] if x["arm"] == "candidate"]
    assert len(assignments) == 60
    assert all(sum(x["problem"] == p for x in assignments) == 30 for p in (3, 4))
    cases, actions, originals = [], [], []
    for assignment in assignments:
        folder = ROOT/"results/round2/cases"/assignment["case_id"]
        result, audit = read(folder/"result.json"), read(folder/"post_exit_audit.json")
        assert read(folder/"assignment.json") == assignment
        assert result["protocol"] == audit["protocol"] == "candidate"
        assert audit["status"] == result["status"] == "complete"
        assert audit["cleared"] == result["clear_successes"] == audit["source_total_post_exit"]
        jlog = folder/"original_logs"/audit["original_log_filename"]
        assert sha(jlog) == audit["original_log_sha256"]
        row = {
            "case_id": assignment["case_id"], "case_code": audit["case_code"],
            "problem": assignment["problem"], "arm": "candidate", "environment": "official_practice",
            "sequence": assignment["sequence"], "block": assignment["block"],
            "N": audit["source_total_post_exit"], "Ndir": audit["directional_total_post_exit"],
            "Nomni": audit["omnidirectional_total_post_exit"], "status": result["status"],
            "cleared": result["clear_successes"], "clear_fraction": audit["clear_fraction"],
            "total_virtual_time_s": result["total_virtual_time_s"],
            "average_clear_time_s": result["average_clear_time_s"],
            "walk_distance_m": result["walk_distance_m"], "measures": result["measures"],
            "switches": result["switches"], "clear_attempts": result["clear_attempts"],
            "failed_clear_attempts": result["clear_attempts"]-result["clear_successes"],
            "fallback_targets": result["fallback_targets"], "certified_clears": result["certified_clears"],
            "program_real_time_s": result["program_real_time_s"],
            "stop_evidence_type": result["stop_evidence"]["type"],
            "timing_residual_s": result["timing_residual_s"],
            "movement_time_s": result["walk_distance_m"]/5,
            "measurement_time_s": result["measures"]*5,
            "switch_time_s": result["switches"],
            "optical_attempt_time_s": result["clear_attempts"]*3,
            "clear_success_extra_time_s": result["clear_successes"]*2,
            "error": result["error"], "case_folder": folder.relative_to(ROOT).as_posix(),
            "original_log_filename": audit["original_log_filename"],
            "original_log_sha256": audit["original_log_sha256"],
            "original_log_bytes": audit["original_log_bytes"], "audit_utc": audit["audited_utc"],
        }
        seen, feedback = set(), {"direction": 0, "near": 0, "no_signal": 0}
        for number, line in enumerate((folder/"requests.jsonl").read_text(encoding="utf-8-sig").splitlines(), 1):
            raw = json.loads(line)
            request, response = raw.get("request", {}), raw.get("response") or {}
            before, after = raw.get("state_before") or {}, raw.get("state_after") or {}
            key = request.get("request_id")
            counted = response.get("accepted") is True and key not in seen
            if counted:
                seen.add(key)
                if raw.get("path") == "/measure":
                    feedback[response["measure_result"]] += 1
            position = request.get("position") or {}
            entry = {
                "case_id": assignment["case_id"], "case_code": audit["case_code"],
                "problem": assignment["problem"], "log_line": number,
                "path": raw.get("path"), "request_id": key, "attempt": raw.get("attempt"),
                "accepted": response.get("accepted"), "counted_unique_action": counted,
                "request_channel": request.get("channel"), "request_x_m": position.get("x"), "request_y_m": position.get("y"),
                "measure_result": response.get("measure_result"),
                "svd_deg": response.get("svd_deg") if response.get("measure_result") == "direction" else None,
                "clear_result": response.get("clear_result"),
                "virtual_time_before_s": before.get("virtual_time_s"),
                "virtual_time_after_s": after.get("virtual_time_s"),
                "response_virtual_time_s": response.get("virtual_time_s"),
                "channel_before": before.get("current_channel"), "channel_after": after.get("current_channel"),
                "cleared_before": before.get("cleared_count"), "cleared_after": after.get("cleared_count"),
                "wall_timestamp_s": raw.get("wall_timestamp_s"), "monotonic_s": raw.get("monotonic_s"),
                "response_real_timestamp_ms": response.get("real_timestamp_ms"),
                "outcome": raw.get("outcome"),
                "request_json": json.dumps(request, ensure_ascii=False, separators=(",", ":")),
                "response_json": json.dumps(raw.get("response"), ensure_ascii=False, separators=(",", ":")),
                "state_before_json": json.dumps(before, ensure_ascii=False, separators=(",", ":")),
                "state_after_json": json.dumps(after, ensure_ascii=False, separators=(",", ":")),
            }
            actions.append(entry)
        assert sum(feedback.values()) == result["measures"]
        row.update({f"measure_{name}_count": value for name, value in feedback.items()})
        row["unique_accepted_actions"] = len(seen)
        assert len(seen) == result["measures"] + result["clear_attempts"] + 2
        cases.append(row)
        for file in sorted(folder.rglob("*")):
            if file.is_file():
                originals.append({"path": file.relative_to(ROOT).as_posix(), "bytes": file.stat().st_size, "sha256": sha(file)})
    cases.sort(key=lambda r: (r["problem"], r["sequence"]))
    aggregate = {}
    for problem in (3, 4):
        subset = [r for r in cases if r["problem"] == problem]
        times = [r["total_virtual_time_s"] for r in subset]
        sums = {k: sum(r[k] for r in subset) for k in ("N", "Ndir", "Nomni", "cleared", "walk_distance_m", "measures", "switches", "clear_attempts", "failed_clear_attempts", "unique_accepted_actions", "measure_direction_count", "measure_near_count", "measure_no_signal_count")}
        averages = {k: statistics.fmean(r[k] for r in subset) for k in ("total_virtual_time_s", "average_clear_time_s", "walk_distance_m", "measures", "switches", "clear_attempts", "failed_clear_attempts", "program_real_time_s")}
        assert math.isclose(averages["total_virtual_time_s"], validation["problems"][str(problem)]["arms"]["candidate"]["completion_time_mean_s"], abs_tol=1e-9)
        aggregate[str(problem)] = {"cases": len(subset), "complete": sum(r["status"] == "complete" for r in subset),
            "sums": sums, "means": averages, "min_time_s": min(times), "median_time_s": statistics.median(times),
            "p90_time_s": quantile(times, .9), "max_time_s": max(times),
            "official_comparison": validation["problems"][str(problem)]}
    payload = {"created_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "environment": "official_practice",
        "scope": "All 60 frozen candidate cases; 60 independent contemporary baseline cases are comparison context, not candidate runs. No formal tests.",
        "case_count": len(cases), "request_transmission_rows": len(actions),
        "candidate_spec": read(candidate_path), "candidate_sha256": sha(candidate_path),
        "validation_summary_sha256": sha(summary_path), "problems": aggregate, "cases": cases,
        "not_observed": ["Exact target coordinates", "Per-source true reception radius", "Per-source true orientation", "True bearing error", "RSS"],
        "sources": originals}
    write_json(OUT/"candidate_data.json", payload)
    write_csv(OUT/"candidate_cases.csv", cases)
    write_csv(OUT/"candidate_actions.csv", actions)
    lines = ["# 最终候选的全部60局官方演练记录", "", "本文件从冻结计划选出全部候选局，没有筛掉慢局。正式测试未执行。问题3/4分别30局，全部全清。", "",
        "N是目标总数，Nd是定向源数；它们来自结束后公开界面，不曾提供给当局策略。时间为虚拟秒；程序时间为现实秒。失败列是失败清除尝试次数，非失败案例数。下表仅为展示舍入，CSV/JSON及原始日志保留原精度。", ""]
    for problem in (3, 4):
        lines += [f"## 问题{problem}：全部30局", "", "|案例序号|官方案例编码|N/Nd|清除|总虚拟秒|平均秒/源|移动米|测量|换频|清除尝试|失败尝试|程序秒|", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for r in (r for r in cases if r["problem"] == problem):
            lines.append(f"|{r['case_id'].rsplit('-',1)[1]}|{r['case_code']}|{r['N']}/{r['Ndir']}|{r['cleared']}/{r['N']}|{r['total_virtual_time_s']:.2f}|{r['average_clear_time_s']:.2f}|{r['walk_distance_m']:.2f}|{r['measures']}|{r['switches']}|{r['clear_attempts']}|{r['failed_clear_attempts']}|{r['program_real_time_s']:.3f}|")
        lines.append("")
    lines += ["## 完整数据", "", "candidate_cases.csv保留每局指标、观测状态计数、停止证据类型和原始日志来源。candidate_actions.csv逐行展开全部请求，含方向/无信号/near、位置、频道、前后状态、完整请求与响应JSON。candidate_data.json包含完整候选配置、分题汇总及原文件SHA256。", "", "候选原始资料包保持每局原始requests、decisions、界面记录、结束审计、result和未改名jlog字节不变。未知的真实位置、半径、朝向和测角误差不补造。", "", "展示序号保留原分配序号，因此不连续；缺少的序号是同题基线局。基线和候选来自不同官方案例，不能把相近序号作配对。"]
    (OUT/"candidate_all_cases.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    write_json(OUT/"support/export_check.json", {"status": "passed", "case_count": len(cases), "all_complete": all(r["status"] == "complete" for r in cases),
        "request_rows": len(actions), "raw_files_checked": len(originals), "raw_bytes": sum(x["bytes"] for x in originals),
        "candidate_sha256": sha(candidate_path), "validation_sha256": sha(summary_path)})
    print(json.dumps({"cases": len(cases), "action_rows": len(actions), "raw_files": len(originals), "output": str(OUT)}, ensure_ascii=False))


def package():
    data = read(OUT/"candidate_data.json")
    destination = OUT/"候选策略官方60局完整原始数据.zip"
    report_path = destination.with_suffix(".manifest.json")
    if destination.exists() or report_path.exists():
        raise FileExistsError("Preserve existing archive; choose a new review directory")
    entries = []
    for item in data["sources"]:
        path = ROOT/item["path"]
        if sha(path) != item["sha256"]:
            raise RuntimeError("Original source changed since extraction: "+item["path"])
        entries.append((path, "B_solution/"+item["path"], item["sha256"]))
    for relative in ("candidate.json", "plan.json", "validation_freeze.json", "baseline_freeze.json", "validation_summary.json", "validation_summary_reviewed.md", "integrity_final/overall.json", "integrity_final/verified_summary.json"):
        path = ROOT/"results/round2"/relative
        entries.append((path, "B_solution/"+path.relative_to(ROOT).as_posix(), sha(path)))
    for name in ("candidate_cases.csv", "candidate_actions.csv", "candidate_data.json", "candidate_all_cases.md", "候选与基线官方完整数据.xlsx", "support/candidate_source_audit.json", "support/export_check.json", "support/export_candidate_data.py"):
        path = OUT/name
        if not path.is_file():
            raise FileNotFoundError(path)
        entries.append((path, "review/"+name, sha(path)))
    manifest = [{"path": name, "bytes": path.stat().st_size, "sha256": digest} for path, name, digest in entries]
    with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path, name, digest in entries:
            archive.write(path, name)
        archive.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("README.txt", "最终候选的全部60局官方演练原始数据，问题3/4各30局，全部完整清除；正式测试0局。\nB_solution/results/round2/cases 下仅包含这60局的完整原始资料，文件名和字节不变。\n冻结的120局计划及统计汇总另包含60局同期基线作为对照；工作簿保留全120行用于筛选比较。\nreview 下有中文逐局表、完整字段CSV、逐请求CSV、JSON、工作簿和只读提取/核验记录。\nCSV/JSON保持原精度，表格显示小数舍入不改写原始请求与决策。\n所有项目相对路径均以B_solution为根。原始jlog保持官方文件名，不解密。\n目标数量和定向数量来自结束后的公开界面；未获得逐源精确位置、真实半径、真实朝向、真实测角误差或RSS，这些不补造。\n全部数据仅在本机整理，没有上传。\n")
    with zipfile.ZipFile(destination) as archive:
        for item in manifest:
            if hashlib.sha256(archive.read(item["path"])).hexdigest() != item["sha256"]:
                raise RuntimeError("Archive hash mismatch: "+item["path"])
    result = {"status": "verified_all_files", "cases": 60, "formal_cases": 0, "files": len(entries),
        "bytes": destination.stat().st_size, "sha256": sha(destination), "source_files_unchanged": True, "upload_performed": False}
    write_json(report_path, result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("export", "package"))
    args = parser.parse_args()
    export() if args.mode == "export" else package()
