"""Independent read-only audit for current/combined_cover P4 practice rounds.

No official client, HTTP transport, UI automation, solver or simulator is
imported.  The only writes are new audit artifacts in --output.  Saved source
archives are hashed without extraction; encrypted jlogs are never decrypted.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts"))
sys.path.insert(0, str(ROOT/"src"))
from audit_round_integrity import replay_requests, number, point, close, digest
from audit_post_exit_evidence import verify_ui
from bsolver.coverage import directional_points, order_route

CORE_SHA = "1b673484453104a5d87d4605d30b20826a3b7ec64769fd6ce8d3c8f7a1ad24f6"
ARM_NAMES = {"current", "combined_cover"}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_rows(path):
    result = []
    for line, text in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if text.strip():
            row = json.loads(text)
            if not isinstance(row, dict):
                raise ValueError(f"{Path(path).name}:{line}: expected an object")
            result.append(row)
    return result


def issue(code, detail="", *, kind="error"):
    return {"kind": kind, "code": code, "detail": detail}


def canonical_self_check(document, field):
    copy = dict(document)
    expected = copy.pop(field, None)
    return isinstance(expected, str) and digest(copy) == expected


def verify_stop(result, decisions, accepted, expected_points, arm, refined_certificate=None):
    """Check the ACTUAL unique accepted observations against frozen station data."""
    issues = []
    starts = [e for e in decisions if e.get("event") == "start"]
    finishes = [e for e in decisions if e.get("event") == "finish"]
    expected = [point(p) for p in expected_points]
    expected_n = 25 if arm == "combined_cover" else 31
    route_sha = digest(expected_points)
    if len(expected) != expected_n or None in expected or len(set(expected)) != len(expected):
        issues.append(issue("invalid_frozen_station_route"))
    if len(starts) != 1 or starts[0].get("coverage_points") != [list(p) for p in expected]:
        issues.append(issue("start_does_not_match_frozen_actual_route"))
    if len(finishes) != 1:
        issues.append(issue("missing_or_duplicate_finish_event"))
    certificate = result.get("stop_evidence") or {}
    if finishes and finishes[-1].get("result", {}).get("stop_evidence") != certificate:
        issues.append(issue("finish_and_result_stop_evidence_disagree"))
    if arm == "combined_cover":
        for label, document in [("result", result)]+[("start", e) for e in starts]:
            if document.get("coverage_points_sha256") != route_sha:
                issues.append(issue("refined_route_sha_mismatch", label))
            if document.get("refined_coverage_certificate") != refined_certificate:
                issues.append(issue("refined_coverage_certificate_mismatch", label))
            if document.get("coverage_variant") != "refined_directional_triangular":
                issues.append(issue("refined_coverage_variant_missing", label))
        if result.get("local_refinement_mode") != "cover":
            issues.append(issue("combined_arm_did_not_use_cover_mode"))

    successful, negative = set(), defaultdict(set)
    paths = Counter()
    for action in accepted:
        path, request, response = action["path"], action["request"], action["response"]
        paths[path] += 1
        if path == "/clear" and response.get("clear_result") == "success":
            successful.add(request["channel"])
        elif path == "/measure" and response.get("measure_result") == "no_signal":
            negative[request["channel"]].add(point(request["position"]))
    kind = certificate.get("type")
    if certificate.get("cleared_channels") != sorted(successful):
        issues.append(issue("stop_cleared_channel_set_mismatch"))
    absent = sorted(set(range(1, 21))-successful)
    if kind == "known_upper_bound":
        if len(successful) != 16:
            issues.append(issue("sixteen_source_stop_without_sixteen_distinct_successes"))
    elif kind == "per_channel_coverage":
        if certificate.get("points") != [list(p) for p in expected] or certificate.get("required_point_count") != expected_n:
            issues.append(issue("stop_does_not_match_frozen_actual_route"))
        if certificate.get("absent_channels") != absent:
            issues.append(issue("stop_absent_channel_set_mismatch"))
        if arm == "combined_cover":
            if (certificate.get("construction") != "refined_directional_triangular"
                    or certificate.get("coverage_points_sha256") != route_sha
                    or certificate.get("refined_coverage_certificate") != refined_certificate):
                issues.append(issue("refined_stop_geometry_certificate_mismatch"))
        for channel in absent:
            if certificate.get("checked_indices", {}).get(str(channel)) != list(range(expected_n)):
                issues.append(issue("absent_station_indices_incomplete", str(channel)))
            for index, p in enumerate(expected):
                if p not in negative[channel]:
                    # Coordinates are exactly frozen submitted floats.  Do not
                    # accept a nearby, altered station merely on a tolerance.
                    issues.append(issue("actual_negative_station_missing", f"channel={channel}, station={index}"))
    else:
        issues.append(issue("unrecognized_stopping_certificate"))
    if paths["/enter"] != 1 or paths["/exit"] != 1 or set(paths) != {"/enter", "/measure", "/clear", "/exit"}:
        issues.append(issue("four_endpoint_execution_count_mismatch", str(dict(paths))))
    return {"issues": issues, "type": kind, "actual_station_count": expected_n,
            "expected_route_sha256": route_sha, "successful_channels": sorted(successful),
            "absent_channels": absent, "accepted_path_counts": dict(paths)}


def verify_jlog(folder, audit, session, snapshot):
    filename, code = audit.get("original_log_filename"), session.get("case_code")
    issues, result = [], {"filename": filename, "sha256": None, "bytes": None}
    if (not isinstance(filename, str) or Path(filename).name != filename or not isinstance(code, str)
            or not re.fullmatch(rf"practice-p4-\d+-{re.escape(code or '')}\.jlog", filename)):
        return {**result, "issues": [issue("invalid_public_original_jlog_identity")]}
    if filename not in [item.get("name") for item in snapshot.get("items", [])]:
        issues.append(issue("jlog_filename_missing_from_public_ui"))
    path = folder/"original_logs"/filename
    if not path.is_file():
        issues.append(issue("original_jlog_copy_missing", filename, kind="missing"))
    else:
        result.update(sha256=sha(path), bytes=path.stat().st_size)
        if result["sha256"] != audit.get("original_log_sha256") or result["bytes"] != audit.get("original_log_bytes"):
            issues.append(issue("original_jlog_copy_hash_or_size_mismatch", filename))
    return {**result, "issues": issues}


def extra_protocol_checks(requests, replay):
    issues, robot_ids, first_attempt = [], set(), {}
    for line, entry in enumerate(requests, 1):
        body = entry.get("request") or {}
        path, response = entry.get("path"), entry.get("response") or {}
        rid = body.get("request_id")
        first_attempt.setdefault(rid, entry.get("monotonic_s"))
        robot_ids.add(body.get("robot_id"))
        if path not in {"/enter", "/measure", "/clear", "/exit"}:
            issues.append(issue("nonpublic_request_path", f"line={line}"))
        if body.get("arena_id") != "default":
            issues.append(issue("unexpected_arena_id", f"line={line}"))
        if response.get("accepted") is not True:
            continue
        if path == "/exit" and response.get("exit_reason") != "user_exit":
            issues.append(issue("accepted_exit_reason_mismatch", f"line={line}"))
        if path == "/measure" and response.get("measure_result") in ("near", "no_signal") and "svd_deg" in response:
            issues.append(issue("non_direction_response_has_angle", f"line={line}"))
        if path == "/enter":
            start, remaining = first_attempt[rid], response.get("remaining_real_duration_s")
            deadline = (entry.get("state_after") or {}).get("deadline")
            if not all(number(v) for v in (start, remaining, deadline)) or deadline > start+remaining+1e-6:
                issues.append(issue("deadline_not_conservative_from_first_enter_attempt", f"line={line}"))
    if len(robot_ids) != 1 or None in robot_ids:
        issues.append(issue("missing_or_mixed_robot_identity"))
    if replay["unresolved_last_request"]:
        issues.append(issue("unresolved_final_request_preserved"))
    return issues


def audit_case(folder, assignment, expected_points, arm, *, refined_certificate=None,
               bundle=None, frozen=None, bundle_sha256=None):
    issues = []
    required = ("assignment.json", "result.json", "requests.jsonl", "decisions.jsonl",
                "post_exit_audit.json", "session.json", "post_exit_ui.json")
    missing = [name for name in required if not (folder/name).is_file()]
    base = {"case_id": assignment["case_id"], "stage": assignment.get("stage", assignment.get("split")),
            "arm": assignment.get("arm"), "started": True, "directory": str(folder.resolve())}
    if missing:
        return {**base, "status": "issues_found", "issue_count": len(missing), "complete": False,
                "issues": [issue("started_case_missing_artifact", name, kind="missing") for name in missing],
                "request_record_count": len(json_rows(folder/"requests.jsonl")) if (folder/"requests.jsonl").is_file() else 0}
    try:
        saved_assignment, result, audit, session, ui = [load(folder/name) for name in
            ("assignment.json", "result.json", "post_exit_audit.json", "session.json", "post_exit_ui.json")]
        requests, decisions = json_rows(folder/"requests.jsonl"), json_rows(folder/"decisions.jsonl")
    except (ValueError, OSError, TypeError) as exc:
        return {**base, "status": "issues_found", "issue_count": 1, "complete": False,
                "issues": [issue("invalid_case_artifact", str(exc))]}
    if saved_assignment != assignment:
        issues.append(issue("assignment_differs_from_frozen_plan"))
    replay = replay_requests(requests)
    issues += replay["issues"]+extra_protocol_checks(requests, replay)
    tol = replay["time_tolerance_at_end_s"]
    for field in ("walk_distance_m", "switches", "measures", "clear_attempts", "clear_successes"):
        actual, reported = replay["counts"][field], result.get(field)
        if not number(reported) or abs(actual-reported) > (1e-6 if field == "walk_distance_m" else 0):
            issues.append(issue("result_action_count_mismatch", field))
    for name, value in (("result.total_virtual_time_s", result.get("total_virtual_time_s")),
                        ("result.independently_accounted_time_s", result.get("independently_accounted_time_s")),
                        ("post_exit_audit.total_virtual_time_s", audit.get("total_virtual_time_s"))):
        if not number(value) or abs(value-replay["computed_virtual_time_s"]) > tol:
            issues.append(issue("result_time_mismatch", name))
    for label, document in (("result", result), ("audit", audit)):
        for key in ("case_id", "problem", "protocol"):
            if document.get(key) != assignment.get(key):
                issues.append(issue("case_metadata_mismatch", f"{label}.{key}"))
    for key in ("arm", "stage"):
        if key in assignment and result.get(key) != assignment[key]:
            issues.append(issue("case_metadata_mismatch", f"result.{key}"))
    if result.get("status") != "complete" or result.get("error"):
        issues.append(issue("case_not_normally_complete", str(result.get("status"))))
    if not replay["state"]["exited"]:
        issues.append(issue("normal_exit_not_confirmed"))
    for key in ("source_total", "directional_total", "source_truth"):
        if result.get(key) is not None:
            issues.append(issue("pre_exit_result_contains_target_truth", key))
    if bundle:
        if result.get("config") != bundle["current_spec"]["solver_config"]:
            issues.append(issue("result_solver_config_differs_from_bundle"))
        if result.get("nosignal_config") != bundle["current_spec"]["nosignal_config"]:
            issues.append(issue("result_nosignal_config_differs_from_bundle"))
        if arm == "combined_cover" and result.get("refined_config") != bundle["refinement"]["refined_config"]:
            issues.append(issue("result_refined_config_differs_from_bundle"))
    if frozen and result.get("official_freeze_sha256") != frozen.get("freeze_sha256"):
        issues.append(issue("result_official_freeze_reference_mismatch"))
    if bundle_sha256 and result.get("policy_bundle_sha256") != bundle_sha256:
        issues.append(issue("result_bundle_reference_mismatch"))

    ui_check = verify_ui(ui, audit, 4, session)
    issues += [issue(code) for code in ui_check["issues"]]
    jlog = verify_jlog(folder, audit, session, ui)
    issues += jlog["issues"]
    stopping = verify_stop(result, decisions, replay["accepted_actions"], expected_points, arm, refined_certificate)
    issues += stopping["issues"]
    n, ndir = ui_check["N"], ui_check["Ndir"]
    successes = replay["state"]["cleared_channels"]
    if n != len(successes) or audit.get("cleared") != n:
        issues.append(issue("public_count_not_equal_distinct_clear_successes"))
    if n and (not number(result.get("average_clear_time_s")) or
              abs(result["average_clear_time_s"]-result["total_virtual_time_s"]/n) > tol):
        issues.append(issue("average_source_time_mismatch"))
    if not number(result.get("program_real_time_s")) or result["program_real_time_s"] < 0:
        issues.append(issue("program_wall_time_missing_or_invalid"))
    last_clear = max((a["response"]["virtual_time_s"] for a in replay["accepted_actions"]
                      if a["path"] == "/clear" and a["response"].get("clear_result") == "success"), default=None)
    full = result.get("status") == "complete" and not result.get("error") and n == len(successes)
    tail = result["total_virtual_time_s"]-last_clear if full and last_clear is not None else None
    return {**base, "status": "issues_found" if issues else "passed", "issue_count": len(issues),
            "issues": issues, "complete": full, "official_N": n, "official_Ndir": ndir,
            "total_virtual_time_s": result.get("total_virtual_time_s"),
            "computed_virtual_time_s": replay["computed_virtual_time_s"],
            "walk_distance_m": replay["counts"]["walk_distance_m"],
            **{k: replay["counts"][k] for k in ("measures", "switches", "clear_attempts", "clear_successes")},
            "failed_clear_attempts": replay["counts"]["clear_attempts"]-replay["counts"]["clear_successes"],
            "accepted_request_count": replay["counts"]["unique_accepted_actions"],
            "request_record_count": len(requests), "replay_counts": replay["counts"],
            "max_response_timing_error_s": replay["max_response_time_error_s"],
            "max_state_timing_error_s": replay["max_state_time_error_s"],
            "timing_tolerance_s": tol, "post_clear_stop_tail_s": tail,
            "last_source_clear_time_s": last_clear, "tail_is_post_exit_diagnostic_only": True,
            "program_real_time_s": result.get("program_real_time_s"),
            "stopping": stopping, "public_ui": ui_check, "original_jlog": jlog,
            "source_sha256": {name: sha(folder/name) for name in required}}


def verify_round_freeze(round_dir, project_root=ROOT):
    issues, file_checks = [], []
    plan, frozen, bundle = (load(round_dir/name) for name in ("plan.json", "official_freeze.json", "policy_bundle.json"))
    for document, field, label in ((plan, "plan_sha256", "plan"), (frozen, "freeze_sha256", "freeze"),
                                    (bundle, "bundle_sha256", "bundle")):
        if not canonical_self_check(document, field):
            issues.append(issue("canonical_self_hash_mismatch", label))
    if frozen.get("plan_sha256") != plan.get("plan_sha256") or frozen.get("plan_file_sha256") != sha(round_dir/"plan.json"):
        issues.append(issue("frozen_plan_reference_mismatch"))
    bundle_sha = sha(round_dir/"policy_bundle.json")
    if frozen.get("policy_bundle_sha256") != bundle_sha or plan.get("bundle_sha256") != bundle.get("bundle_sha256"):
        issues.append(issue("frozen_policy_bundle_reference_mismatch"))
    if plan.get("formal_authorized") is not False:
        issues.append(issue("plan_is_not_explicitly_practice_only"))
    sources = frozen.get("files") or {}
    required_sources = {"src/bsolver/refined_local.py", "src/bsolver/refined_coverage.py", "src/bsolver/protocol.py",
                        "scripts/run_p4_refined_official.py", "scripts/collect_official_round.py", "scripts/round_ui.ps1",
                        "scripts/run_p4_refinement.py"}
    if not required_sources.issubset(sources) or len(sources) != 18:
        issues.append(issue("frozen_source_file_set_incomplete"))
    archive = round_dir/"source_snapshot.zip"
    if not archive.is_file() or sha(archive) != frozen.get("source_snapshot_sha256"):
        issues.append(issue("source_snapshot_archive_hash_mismatch"))
    if archive.is_file():
        with zipfile.ZipFile(archive) as z:
            if set(z.namelist()) != set(sources) or len(z.namelist()) != len(sources):
                issues.append(issue("source_snapshot_archive_file_set_mismatch"))
            for name, expected in sources.items():
                path = (project_root/name).resolve()
                actual = sha(path) if path.is_relative_to(project_root.resolve()) and path.is_file() else None
                archived = hashlib.sha256(z.read(name)).hexdigest() if name in z.namelist() else None
                file_checks.append({"path": name, "expected": expected, "current": actual, "archived": archived})
                if actual != expected or archived != expected:
                    issues.append(issue("frozen_runtime_or_archive_source_changed", name))
    for name, expected in (frozen.get("input_files") or {}).items():
        path = (round_dir/name).resolve()
        if not path.is_relative_to(round_dir.resolve()) or not path.is_file() or sha(path) != expected:
            issues.append(issue("frozen_input_file_hash_mismatch", name))
    required_inputs = {"local_selection_input.json", "local_source_freeze_input.json", "current_candidate_input.json"}
    if not required_inputs.issubset(frozen.get("input_files") or {}):
        issues.append(issue("frozen_input_file_set_incomplete"))
    selection = load(round_dir/"local_selection_input.json")
    local_source = load(round_dir/"local_source_freeze_input.json")
    candidate = load(round_dir/"current_candidate_input.json")
    if (not canonical_self_check(selection, "selection_sha256")
            or frozen.get("local_selection_sha256") != selection.get("selection_sha256")
            or bundle.get("local_selection_sha256") != selection.get("selection_sha256")
            or "combined_cover" not in selection.get("selected_variants", [])):
        issues.append(issue("frozen_local_selection_reference_mismatch"))
    if (not canonical_self_check(local_source, "source_freeze_sha256")
            or bundle.get("local_source_freeze_sha256") != local_source.get("source_freeze_sha256")
            or bundle.get("local_manifest_sha256") != local_source.get("manifest_sha256")
            or bundle.get("refinement") != local_source.get("refinement")):
        issues.append(issue("frozen_local_refinement_reference_mismatch"))
    for name, expected in (local_source.get("source_sha256") or {}).items():
        if sources.get(name) != expected:
            issues.append(issue("official_freeze_differs_from_local_policy_source", name))
    if bundle.get("current_spec") != candidate.get("problems", {}).get("4"):
        issues.append(issue("current_arm_is_not_previous_frozen_candidate"))
    baseline = load(round_dir/"baseline_freeze.json")
    if baseline.get("source_sha256") != CORE_SHA:
        issues.append(issue("original_core_identity_mismatch"))
    for name, expected in baseline.get("files", {}).items():
        if sources.get(name) != expected:
            issues.append(issue("original_baseline_dependency_mismatch", name))
    refinement = bundle.get("refinement") or {}
    route, certificate = refinement.get("refined_route", []), refinement.get("coverage_certificate", {})
    if (len(route) != 25 or refinement.get("refined_route_sha256") != digest(route)
            or certificate.get("stations") != route or certificate.get("ordered_station_sha256") != digest(route)
            or certificate.get("point_count") != 25 or certificate.get("reception_distance_upper_bound_m", 1001) >= 1000
            or certificate.get("strict_projection_lower_bound_m", -1) <= 0):
        issues.append(issue("frozen_refined_route_certificate_inconsistent"))
    return plan, frozen, bundle, {"issues": issues, "files": file_checks,
                                "plan_sha256": plan.get("plan_sha256"), "freeze_sha256": frozen.get("freeze_sha256"),
                                "policy_bundle_sha256": bundle_sha}


def audit_round(round_dir, output, *, allow_partial=False, project_root=ROOT):
    round_dir, output = Path(round_dir).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Refusing to overwrite a previous audit; choose a new output directory")
    if output == round_dir or output.is_relative_to(round_dir/"cases"):
        raise ValueError("Audit output must not overlap original case data")
    plan, frozen, bundle, source_check = verify_round_freeze(round_dir, project_root)
    issues = [dict(case_id="__freeze__", **e) for e in source_check["issues"]]
    old_points = [list(p) for p in order_route(directional_points("triangular", None), (0., 0.))]
    new_points = bundle["refinement"]["refined_route"]
    rows, planned, seen = [], [], set()
    for sequence, assignment in enumerate(plan.get("cases", [])):
        identity, arm, stage = assignment.get("case_id"), assignment.get("arm"), assignment.get("stage")
        if (not isinstance(identity, str) or Path(identity).name != identity or any(c in identity for c in '/\\:')
                or identity in seen or arm not in ARM_NAMES or stage not in {"pilot", "validation"}):
            issues.append(issue("invalid_or_duplicate_case_allocation", str(identity)))
            continue
        seen.add(identity)
        if (assignment.get("problem") != 4 or assignment.get("protocol") != arm
                or assignment.get("split") != stage or assignment.get("pilot") != (stage == "pilot")
                or assignment.get("environment") != "official_practice"
                or assignment.get("sequence") != sequence):
            issues.append(issue("invalid_case_metadata", identity))
        folder = round_dir/"cases"/identity
        assignment_path = folder/"assignment.json"
        if not assignment_path.is_file() or load(assignment_path) != assignment:
            issues.append(dict(case_id=identity, **issue("assignment_differs_from_plan_or_missing")))
        started = any((folder/name).exists() for name in ("launch_intent.json", "session.json", "requests.jsonl",
                      "result.json", "post_exit_audit.json", "start_failure.json", "attempt_started.json"))
        if not started:
            planned.append({"case_id": identity, "stage": stage, "arm": arm, "status": "planned_not_started"})
            if not allow_partial:
                issues.append(dict(case_id=identity, **issue("planned_case_not_started", kind="missing")))
            continue
        row = audit_case(folder, assignment, new_points if arm == "combined_cover" else old_points, arm,
                         refined_certificate=bundle["refinement"]["coverage_certificate"],
                         bundle=bundle, frozen=frozen, bundle_sha256=source_check["policy_bundle_sha256"])
        rows.append(row)
        issues += [dict(case_id=identity, **e) for e in row["issues"]]
    actual_folders = {p.name for p in (round_dir/"cases").iterdir() if p.is_dir()}
    if actual_folders-seen:
        issues.append(issue("unallocated_case_directories", str(sorted(actual_folders-seen))))
    allocation_counts = Counter((c.get("stage"), c.get("arm")) for c in plan.get("cases", []))
    if allocation_counts != Counter({("pilot", "current"): 1, ("pilot", "combined_cover"): 1,
                                     ("validation", "current"): 30, ("validation", "combined_cover"): 30}):
        issues.append(issue("frozen_2pilot_60validation_allocation_mismatch", str(dict(allocation_counts))))
    groups = []
    for stage in ("pilot", "validation"):
        for arm in ("current", "combined_cover"):
            group = [r for r in rows if r["stage"] == stage and r["arm"] == arm]
            groups.append({"stage": stage, "arm": arm, "attempted": len(group),
                           "complete": sum(r.get("complete", False) for r in group),
                           "audit_passed": sum(r["status"] == "passed" for r in group),
                           "planned_not_started": sum(r["stage"] == stage and r["arm"] == arm for r in planned)})
    def total(field):
        return sum(r.get(field, 0) or 0 for r in rows)
    overall = {"schema_version": 1, "audit_utc": datetime.now(timezone.utc).isoformat(),
               "round": str(round_dir), "allow_partial": allow_partial,
               "status": "issues_found" if issues else "passed", "issue_count": len(issues), "issues": issues,
               "cases_count": len(rows), "planned_cases_count": len(plan.get("cases", [])),
               "planned_not_started_count": len(planned), "groups": groups,
               "accepted_request_count": total("accepted_request_count"), "request_record_count": total("request_record_count"),
               "official_N": total("official_N"), "official_Ndir": total("official_Ndir"),
               "walk_distance_m": total("walk_distance_m"), "measures": total("measures"),
               "switches": total("switches"), "clear_attempts": total("clear_attempts"),
               "clear_successes": total("clear_successes"), "failed_clear_attempts": total("failed_clear_attempts"),
               "failed_or_unresolved_started_cases": sum(not r.get("complete", False) for r in rows),
               "max_response_timing_error_s": max((r.get("max_response_timing_error_s", 0.) for r in rows), default=0.),
               "max_state_timing_error_s": max((r.get("max_state_timing_error_s", 0.) for r in rows), default=0.),
               "source_check": source_check, "audit_sources": {str(Path(__file__).resolve()): sha(Path(__file__)),
                   str(ROOT/"scripts/audit_round_integrity.py"): sha(ROOT/"scripts/audit_round_integrity.py"),
                   str(ROOT/"scripts/audit_post_exit_evidence.py"): sha(ROOT/"scripts/audit_post_exit_evidence.py")},
               "interpretation": "Pilot and validation remain separate; independent official cases are not paired. Planned unstarted cases are not failures. No official actions were sent."}
    output.mkdir(parents=True)
    for name, data in (("cases.json", rows), ("planned_unstarted.json", planned), ("overall.json", overall)):
        (output/name).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    return overall


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    report = audit_round(args.round, args.output, allow_partial=args.allow_partial)
    print(json.dumps({k: report[k] for k in ("status", "issue_count", "cases_count", "planned_not_started_count",
                                            "accepted_request_count", "request_record_count")}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
