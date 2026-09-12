"""Read-only public UI and stopping-certificate supplement to request replay.

The saved UI accessibility tree is public post-exit evidence, not hidden source
coordinates. No UI tool, official request, solver or simulator is imported.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def xy(value):
    return (float(value["x"]), float(value["y"])) if isinstance(value, dict) else tuple(map(float, value))


def verify_ui(snapshot, audit, problem, session):
    issues = []
    names = [r.get("name", "") for r in snapshot.get("items", [])]
    text = "\n".join(names)
    summary = re.search(r"共\s*(\d+)\s*个，\s*全向\s*(\d+)\s*个，\s*定向\s*(\d+)\s*个", text)
    dialog = re.search(r"本次案例含干扰源\s*(\d+)\s*个，其中全向\s*(\d+)\s*个、定向\s*(\d+)\s*个", text)
    if summary is None or dialog is None:
        return {"issues": ["public_ui_count_summary_or_exit_dialog_missing"], "N": None, "Ndir": None, "Nomni": None}
    counts, second = tuple(map(int, summary.groups())), tuple(map(int, dialog.groups()))
    if counts != second:
        issues.append("public_ui_summary_dialog_count_disagreement")
    total, omni, directed = counts
    if not 10 <= total <= 16 or total != omni + directed or not 0 <= directed <= total or (problem == 3 and directed != 0):
        issues.append("public_ui_composition_outside_problem_constraints")
    for field, expected in (("source_total_post_exit", total), ("omnidirectional_total_post_exit", omni), ("directional_total_post_exit", directed)):
        if audit.get(field) != expected:
            issues.append(f"public_ui_audit_field_mismatch:{field}")
    if audit.get("case_code") not in names or session.get("case_code") != audit.get("case_code"):
        issues.append("public_ui_case_identity_mismatch")
    if audit.get("original_log_filename") not in names:
        issues.append("public_ui_original_log_name_mismatch")
    if "测试已结束" not in names or "/exit" not in text:
        issues.append("public_ui_normal_exit_evidence_missing")
    if audit.get("cleared") != total or audit.get("status") != "complete" or audit.get("clear_fraction") != 1.:
        issues.append("public_post_exit_not_full_clear")
    return {"issues": issues, "N": total, "Ndir": directed, "Nomni": omni,
            "summary_and_dialog_independently_agree": counts == second}


def verify_stop(result, decisions, requests, problem):
    issues = []
    certificate = result.get("stop_evidence") or {}
    upper_bound_stop = certificate.get("type") == "known_upper_bound"
    if certificate.get("type") not in ("known_upper_bound", "per_channel_coverage"):
        issues.append("unknown_stopping_certificate_type")
    starts = [r for r in decisions if r.get("event") == "start"]
    points = certificate.get("points", [])
    required = {3: 7, 4: 31}.get(problem)
    if not upper_bound_stop and (len(starts) != 1 or points != starts[0].get("coverage_points")):
        issues.append("stopping_points_do_not_match_frozen_start")
    if not upper_bound_stop and (len(points) != required or certificate.get("required_point_count") != required):
        issues.append("stopping_fixed_station_count_mismatch")
    seen, successful, negative = set(), set(), defaultdict(list)
    outcomes = Counter()
    for row in requests:
        response, request = row.get("response") or {}, row.get("request") or {}
        identity = request.get("request_id")
        if response.get("accepted") is not True or identity in seen:
            continue
        seen.add(identity)
        outcomes[row["path"]] += 1
        if row["path"] == "/clear" and response.get("clear_result") == "success":
            successful.add(request["channel"])
        if row["path"] == "/measure" and response.get("measure_result") == "no_signal":
            negative[request["channel"]].append(xy(request["position"]))
    absent = sorted(set(range(1, 21)) - successful)
    if certificate.get("cleared_channels") != sorted(successful) or (not upper_bound_stop and certificate.get("absent_channels") != absent):
        issues.append("stopping_channel_partition_mismatch")
    if upper_bound_stop:
        # Published N <= 16 independently closes the search after sixteen
        # distinct successful clear confirmations; full coverage is unnecessary.
        if len(successful) != 16:
            issues.append("known_upper_bound_stop_without_sixteen_distinct_successes")
    else:
        for channel in absent:
            if certificate.get("checked_indices", {}).get(str(channel)) != list(range(len(points))):
                issues.append(f"stopping_absence_indices_incomplete:{channel}")
            for index, position in enumerate(points):
                if not any(math.dist(xy(position), q) <= 1e-7 for q in negative[channel]):
                    issues.append(f"stopping_negative_measurement_missing:{channel}:{index}")
    if outcomes["/enter"] != 1 or outcomes["/exit"] != 1 or set(outcomes) != {"/enter", "/measure", "/clear", "/exit"}:
        issues.append("four_interface_execution_count_mismatch")
    return {"issues": issues, "source_channels_successfully_cleared": len(successful),
            "certificate_type": certificate.get("type"),
            "absent_channels_certified": len(absent), "coverage_station_count": len(points),
            "accepted_path_counts": dict(outcomes),
            "scope": "Actual no-signal measurements and declared fixed coverage stations; coverage geometry remains justified by frozen core proof, not by the post-exit counts."}


def run(round_root, output):
    round_root, output = Path(round_root), Path(output)
    plan = load(round_root / "plan.json")
    primary = load(output / "overall.json")
    primary_cases = {r["case_id"]: r for r in load(output / "cases.json")}
    rows, issues = [], []
    for assignment in plan["cases"]:
        folder = round_root / "cases" / assignment["case_id"]
        result, audit, session = (load(folder / name) for name in ("result.json", "post_exit_audit.json", "session.json"))
        snapshot = load(folder / "post_exit_ui.json")
        decisions = [json.loads(r) for r in (folder / "decisions.jsonl").read_text("utf8").splitlines() if r.strip()]
        requests = [json.loads(r) for r in (folder / "requests.jsonl").read_text("utf8").splitlines() if r.strip()]
        ui, stop = verify_ui(snapshot, audit, assignment["problem"], session), verify_stop(result, decisions, requests, assignment["problem"])
        errors = ui["issues"] + stop["issues"]
        if ui["N"] != stop["source_channels_successfully_cleared"]:
            errors.append("public_UI_N_not_equal_unique_clear_successes")
        original = primary_cases[assignment["case_id"]]
        row = {"case_id": assignment["case_id"], "problem": assignment["problem"], "arm": assignment["arm"],
               "N": ui["N"], "Ndir": ui["Ndir"], "Nomni": ui["Nomni"], "issues": errors,
               "ui_checks": ui, "stopping_checks": stop,
               "public_evidence_sha256": {name: sha(folder / name) for name in ("post_exit_ui.json", "post_exit_audit.json", "session.json", "assignment.json")},
               "measures": original["measures"], "clear_attempts": original["clear_attempts"],
               "clear_successes": original["clear_successes"], "clear_failures": original["clear_attempts"] - original["clear_successes"]}
        rows.append(row)
        issues.extend({"case_id": assignment["case_id"], "issue": e} for e in errors)
    incident = load(round_root / "startup_incident.json")
    if incident.get("official_cases_started") != 0 or incident.get("robot_requests_sent") != 0:
        issues.append({"case_id": "__startup__", "issue": "startup_incident_cannot_be_excluded_as_zero_case_zero_request"})
    groups = []
    for problem in (3, 4):
        for arm in ("baseline", "candidate"):
            values = [r for r in rows if r["problem"] == problem and r["arm"] == arm]
            groups.append({"problem": problem, "arm": arm, "cases": len(values),
                           **{k: sum(r[k] for r in values) for k in ("N", "Ndir", "Nomni", "measures", "clear_attempts", "clear_successes", "clear_failures")}})
    summary = {"status": "passed_full_public_evidence" if not issues and primary["n_issues"] == 0 else "issues_found",
               "n_cases": len(rows), "supplement_issue_count": len(issues), "primary_issue_count": primary["n_issues"],
               "issues": issues, "groups": groups, "total_sources": sum(r["N"] for r in rows),
               "total_directional_sources": sum(r["Ndir"] for r in rows), "total_omnidirectional_sources": sum(r["Nomni"] for r in rows),
               "total_failed_clear_attempts": sum(r["clear_failures"] for r in rows), "failed_cases": sum(r["result_status"] != "complete" for r in primary_cases.values()),
               "stopping_certificate_counts": dict(Counter(r["stopping_checks"]["certificate_type"] for r in rows)),
               "startup_incident": {"official_cases_started": 0, "robot_requests_sent": 0, "sha256": sha(round_root / "startup_incident.json"),
                                    "counted_as_failed_case": False},
               "primary_overall_sha256": sha(output / "overall.json"), "audit_script_sha256": sha(Path(__file__))}
    (output / "public_evidence_cases.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf8")
    (output / "public_evidence_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf8")
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--round-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    r = run(a.round_root, a.output)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    raise SystemExit(int(r["status"] == "issues_found"))
