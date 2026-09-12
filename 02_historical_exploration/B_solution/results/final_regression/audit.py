"""Reproduce the final-source audit without modifying the original test results."""
from pathlib import Path
import difflib
import json
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from bsolver.experiments import code_digest, run_batch


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def actions(path):
    result = []
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in records:
        request = {k: v for k, v in row.get("request", {}).items() if k != "request_id"}
        response = {k: v for k, v in row.get("response", {}).items()
                    if k in ["accepted", "virtual_time_s", "measure_result", "svd_deg", "clear_result", "error"]}
        result.append({"path": row.get("path"), "request": request, "response": response, "outcome": row.get("outcome")})
    return result, all("state_before" in row for row in records)


def main():
    out = ROOT / "results/final_regression"
    original = read(ROOT / "results/test/results.json")
    case_ids = [min(row["case_id"] for row in original if row["problem"] == problem) for problem in [3, 4]]
    cases = [read(ROOT / "results/test/joint_triangular" / case_id / "case.json") for case_id in case_ids]
    variants = ["baseline", "active", "nearest_safe", "immediate", "joint_square", "joint_triangular"]
    starting_digest = code_digest()
    if "--existing-results" not in sys.argv:
        run_batch(cases, variants, out)
    # Compare parsed JSON on both sides, including normalized tuple/list and
    # integer dictionary keys. Do not compare old JSON with fresh Python tuples.
    current = read(out / "results.json")
    ending_digest = code_digest()
    if starting_digest != ending_digest:
        raise RuntimeError("Source changed during regression; audit is invalid")
    if any(row["code_sha256"] != starting_digest for row in current):
        raise RuntimeError("Stored regression results do not match current source")
    old_digests = sorted({row["code_sha256"] for row in original})
    if len(old_digests) != 1:
        raise RuntimeError("Original test has mixed source versions")
    old_digest = old_digests[0]
    fields = ["status", "error", "problem", "config", "walk_distance_m", "switches", "measures",
              "clear_attempts", "clear_successes", "fallback_targets", "certified_clears", "initial_hull_invariant",
              "total_virtual_time_s", "independently_accounted_time_s", "timing_residual_s", "average_clear_time_s",
              "stop_evidence", "source_total", "clear_fraction", "hull_invariant_violations", "environment_summary"]
    comparisons = []
    for row in current:
        old = next(r for r in original if r["case_id"] == row["case_id"] and r["variant"] == row["variant"])
        old_path = ROOT / "results/test" / row["variant"] / row["case_id"] / "requests.jsonl"
        new_path = out / row["variant"] / row["case_id"] / "requests.jsonl"
        before, _ = actions(old_path)
        after, state_before_complete = actions(new_path)
        differences = [{"field": key, "original": old.get(key), "final": row.get(key)}
                       for key in fields if old.get(key) != row.get(key)]
        action_equal = before == after
        first_action_difference = next((i for i, (a, b) in enumerate(zip(before, after)) if a != b), None)
        if len(before) != len(after) and first_action_difference is None:
            first_action_difference = min(len(before), len(after))
        comparisons.append({"case_id": row["case_id"], "problem": row["problem"], "variant": row["variant"],
            "result_fields_exact_equal": not differences, "field_differences": differences,
            "actions_exact_equal": action_equal, "first_action_difference_index": first_action_difference,
            "original_action_count": len(before), "final_action_count": len(after),
            "state_before_in_every_final_log_record": state_before_complete,
            "virtual_time_s": row["total_virtual_time_s"], "final_status": row["status"]})
    old_snapshot = ROOT / "results/code_snapshots" / (old_digest + ".zip")
    diffs, changed_files = [], []
    with zipfile.ZipFile(old_snapshot) as archive:
        for path in sorted((ROOT / "src/bsolver").glob("*.py")):
            name = path.relative_to(ROOT).as_posix()
            previous = archive.read(name).decode("utf-8").splitlines(keepends=True)
            latest = path.read_text(encoding="utf-8").splitlines(keepends=True)
            diff = list(difflib.unified_diff(previous, latest, fromfile="original/" + name, tofile="final/" + name))
            if diff:
                changed_files.append(name)
                diffs.extend(diff)
    (out / "source_changes.diff").write_text("".join(diffs), encoding="utf-8")
    snapshot = ROOT / "results/code_snapshots" / (ending_digest + ".zip")
    with zipfile.ZipFile(snapshot, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((ROOT / "src/bsolver").glob("*.py")):
            archive.write(path, path.relative_to(ROOT).as_posix())
    audit = {"original_code_sha256": old_digest, "final_code_sha256": ending_digest,
        "original_experiment_rows": 192, "regression_rows": len(current), "case_ids": case_ids,
        "changed_source_files": changed_files, "source_diff": "results/final_regression/source_changes.diff",
        "final_snapshot": str(snapshot.relative_to(ROOT)),
        "all_result_fields_equal": all(row["result_fields_exact_equal"] for row in comparisons),
        "all_action_sequences_equal": all(row["actions_exact_equal"] for row in comparisons),
        "runtime_compared": False,
        "comparison_policy": "Exact parsed-JSON equality for all listed fields and ordered action requests/coordinates/outcomes/virtual times; exclude request IDs, wall/monotonic/real timestamps, runtime, code hash and newly added state_before metadata.",
        "compared_result_fields": fields, "comparisons": comparisons}
    (out / "version_audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# 版本审计与最终源回归", "", f"原192条自建实验使用 `{old_digest}`；保留原始结果及散列，不改写为新版本。",
        f"最终源使用 `{ending_digest}`。两个版本均存于 `results/code_snapshots/<hash>.zip`。", "",
        "最终修补仅涉及 protocol.py 增加请求前 state_before 日志，以及 sensing.py 拒绝非正/非有限的分箱宽度、负/非有限的角容差，或扩张角达到90°的参数。默认 bin_width=4°、epsilon=1.0051°不触及新增拒绝条件；具体逐行差异见 `results/final_regression/source_changes.diff`。", "",
        "抽取既有 test 的首个P3、P4案例，各使用原六种变体重跑，共12次。未用结果调整参数；这12次是版本回归，不并入原192条的模型效果样本。", "",
        "核验采用解析JSON后的精确相等：计数、总虚拟时间、独立核算时间、路程、清除比例、停止证据、外部环境统计，以及完整动作顺序/坐标/响应/动作虚拟时间。排除请求ID、真实时间戳、本地runtime和新增日志元数据。", "",
        "|题号|变体|请求数|全部结果字段相同|动作序列相同|总虚拟时间 s|", "|---|---|---:|---|---|---:|"]
    for row in comparisons:
        lines.append(f"|P{row['problem']}|{row['variant']}|{row['final_action_count']}|{row['result_fields_exact_equal']}|{row['actions_exact_equal']}|{row['virtual_time_s']:.6f}|")
    lines += ["", f"结果：12次结果字段全部相同={audit['all_result_fields_equal']}；12条完整动作序列全部相同={audit['all_action_sequences_equal']}。全部新请求日志含state_before。",
        "该回归支持这12个默认合法场景的行为未改变，不宣称穷尽全部场景，也不把旧官方练习记录改标成最终源结果。原官方练习应按其自己的源散列和日志解释。", "",
        "机器可读证据：`results/final_regression/version_audit.json`；原始12次记录：该目录下各变体/案例；重现脚本：`results/final_regression/audit.py`。", ""]
    (ROOT / "docs/version_audit.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({k: v for k, v in audit.items() if k not in ["comparisons", "compared_result_fields"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
