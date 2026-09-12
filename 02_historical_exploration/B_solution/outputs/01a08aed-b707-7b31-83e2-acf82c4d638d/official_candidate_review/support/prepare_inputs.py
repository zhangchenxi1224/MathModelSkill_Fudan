"""Read-only official evidence extraction for the standalone workbook."""
import csv
import hashlib
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8-sig"))

csv_rel = "results/pipeline_summary/official_validation_cases.csv"
summary_rel = "results/round2/validation_summary.json"
candidate_rel = "results/round2/candidate.json"
plan_rel = "results/round2/plan.json"
freeze_rel = "results/round2/validation_freeze.json"
baseline_rel = "results/round2/baseline_freeze.json"
summary, candidate, plan, freeze, baseline = [read(p) for p in [summary_rel, candidate_rel, plan_rel, freeze_rel, baseline_rel]]
assert digest(ROOT / candidate_rel) == freeze["candidate_sha256"]
assert digest(ROOT / plan_rel) == freeze["plan_sha256"]
assert digest(ROOT / baseline_rel) == freeze["baseline_freeze_sha256"]
assert candidate["baseline_core_sha256"] == baseline["source_sha256"]
for rel, expected in {**baseline["files"], **candidate["dependency_hashes"]}.items():
    assert digest(ROOT / rel) == expected, rel
plans = {p["case_id"]: p for p in plan["cases"]}
with (ROOT / csv_rel).open(encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)
    fields, csv_rows = reader.fieldnames, list(reader)
assert len(fields) == 18 and len(csv_rows) == 120
integer_fields = {"problem", "N", "Ndir", "measures", "switches", "clear_attempts", "clear_successes"}
boolean_fields = {"attempted", "audit_missing", "evaluation_complete"}
float_fields = {"total_virtual_time_s", "average_clear_time_s", "walk_distance_m", "program_real_time_s"}
def convert(key, val):
    if key in boolean_fields:
        assert val in {"True", "False"}
        return val == "True"
    if key in integer_fields:
        return int(val) if val else None
    if key in float_fields:
        return float(val) if val else None
    return val

rows, provenance = [], []
for raw in csv_rows:
    record = {k: convert(k, v) for k, v in raw.items()}
    case = record["case_id"]
    assignment = plans[case]
    folder = Path("results/round2/cases") / case
    audit_rel = (folder / "post_exit_audit.json").as_posix()
    audit = read(audit_rel)
    assert assignment["problem"] == record["problem"] == audit["problem"]
    assert assignment["protocol"] == record["protocol"] == audit["protocol"]
    assert audit["source_total_post_exit"] == record["N"]
    assert audit["directional_total_post_exit"] == record["Ndir"]
    assert audit["cleared"] == record["clear_successes"]
    assert audit["case_code"] == record["case_code"]
    assert audit["total_virtual_time_s"] == record["total_virtual_time_s"]
    original_rel = (folder / "original_logs" / audit["original_log_filename"]).as_posix()
    assert digest(ROOT / original_rel) == audit["original_log_sha256"]
    assert (ROOT / original_rel).stat().st_size == audit["original_log_bytes"]
    rows.append([record[k] for k in fields] + [audit["original_log_filename"], audit["original_log_sha256"], folder.as_posix(), digest(ROOT / audit_rel), audit["original_log_bytes"], assignment["sequence"] + 1])
    provenance.append({"case_id": case, "audit_path": audit_rel, "audit_sha256": digest(ROOT / audit_rel), "original_log_path": original_rel, "original_log_sha256": digest(ROOT / original_rel), "requests_sha256": digest(ROOT / folder / "requests.jsonl"), "decisions_sha256": digest(ROOT / folder / "decisions.jsonl")})
assert len({r[0] for r in rows}) == 120 and set(plans) == {r[0] for r in rows}
controls = []
for problem in (3, 4):
    for arm in ("baseline", "candidate"):
        items = [r for r in rows if r[2] == problem and r[3] == arm]
        expected = summary["problems"][str(problem)]["arms"][arm]
        assert len(items) == expected["planned"] == 30
        assert all(r[6] and not r[7] and r[8] == "complete" and r[9] and r[16] == r[4] for r in items)
        mean = lambda i: sum(r[i] for r in items) / len(items)
        metrics = {"completion_time_mean_s": mean(10), "walk_distance_mean_m": mean(12), "measurements_mean": mean(13), "switches_mean": mean(14), "clear_attempts_mean": mean(15), "clear_successes_mean": mean(16), "failed_clears_mean": mean(15) - mean(16), "program_real_time_mean_s": mean(17), "average_clear_time_mean_s": mean(11), "mean_N": mean(4), "mean_Ndir": mean(5)}
        assert math.isclose(metrics["completion_time_mean_s"], expected["completion_time_mean_s"], rel_tol=0, abs_tol=1e-9)
        components = {"movement_s": mean(12) / 5, "switch_s": mean(14), "measurement_s": 5 * mean(13), "optical_s": 3 * mean(15), "laser_s": 2 * mean(16)}
        for key, value in components.items():
            assert math.isclose(value, expected["component_means_s"][key], rel_tol=0, abs_tol=1e-9), (problem, arm, key)
        controls.append({"problem": problem, "arm": arm, "count": len(items), "full_clear": len(items), **metrics, **components})
sources = [{"path": rel, "sha256": digest(ROOT / rel)} for rel in [csv_rel, summary_rel, candidate_rel, plan_rel, freeze_rel, baseline_rel]]
payload = {"fields": fields, "rows": rows, "summary": summary, "candidate": candidate, "baseline": baseline, "sources": sources, "case_provenance": provenance, "controls": controls, "scope": "Only 120 frozen round2 official PRACTICE cases. Candidate 60, concurrent baseline 60. No first-round160 included. No new official actions."}
(HERE / "inputs.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"records": len(rows), "groups": controls, "source_hashes": sources}, ensure_ascii=False, indent=2))
