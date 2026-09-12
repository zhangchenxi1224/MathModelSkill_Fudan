"""Read-only independent replay of public round logs; no solver/API/UI imports.

The official protocol rounds virtual timestamps to microseconds. The audit
reports raw residuals and allows at most 1 microsecond per unique action plus
1 microsecond at entry. No missing record is silently treated as a pass.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import random
import re
import zipfile


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def point(value):
    if isinstance(value, dict):
        value = (value.get("x"), value.get("y"))
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(number(v) for v in value):
        return tuple(float(v) for v in value)
    return None


def close(a, b, tolerance=1e-7):
    return a is not None and b is not None and math.dist(a, b) <= tolerance


def read_json(path, issues, required=False):
    if not path.exists():
        if required:
            issues.append({"kind": "missing", "code": "missing_file", "detail": path.name})
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value
    except (ValueError, OSError) as exc:
        issues.append({"kind": "error", "code": "invalid_json", "detail": f"{path.name}: {type(exc).__name__}"})
        return {}


def read_rows(path, issues):
    if not path.exists():
        issues.append({"kind": "missing", "code": "missing_file", "detail": path.name})
        return []
    rows = []
    for line, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("object required")
            rows.append(value)
        except ValueError:
            issues.append({"kind": "error", "code": "invalid_jsonl", "line": line, "detail": path.name})
    return rows


def replay_requests(rows, *, per_action_time_tolerance=1e-6):
    """Recompute solely from request positions, paths and accepted feedback."""
    issues, records, accepted = [], [], []
    state = {"position": (0., 0.), "current_channel": 1, "entered": False, "exited": False}
    counts = {"walk_distance_m": 0., "switches": 0, "measures": 0, "clear_attempts": 0,
              "clear_successes": 0, "unique_accepted_actions": 0, "accepted_response_rows": 0,
              "rejected_response_rows": 0, "unconfirmed_transport_rows": 0, "duplicate_accepted_responses": 0}
    cleared, ids, confirmed = set(), {}, {}
    pending_id = None
    max_time_error, max_state_time_error = 0., 0.

    def add(code, line, detail="", kind="error"):
        issues.append({"kind": kind, "code": code, "line": line, "detail": detail})

    def virtual():
        return (counts["walk_distance_m"] / 5 + counts["switches"] + 5 * counts["measures"] +
                3 * counts["clear_attempts"] + 2 * counts["clear_successes"])

    def check_state(snapshot, label, line):
        nonlocal max_state_time_error
        if not isinstance(snapshot, dict):
            add("missing_state", line, label, "missing")
            return
        for field in ("position", "current_channel", "virtual_time_s", "cleared_count", "cleared_channels", "entered", "exited"):
            if field not in snapshot:
                add("missing_state_field", line, f"{label}.{field}", "missing")
                continue
            if field == "position":
                good = close(point(snapshot[field]), state[field])
            elif field == "virtual_time_s":
                value = snapshot[field]
                error = abs(value - virtual()) if number(value) else None
                if error is not None:
                    max_state_time_error = max(max_state_time_error, error)
                good = error is not None and error <= per_action_time_tolerance * (counts["unique_accepted_actions"] + 1)
            elif field == "cleared_count":
                good = snapshot[field] == len(cleared)
            elif field == "cleared_channels":
                good = snapshot[field] == sorted(cleared)
            else:
                good = snapshot[field] == state[field]
            if not good:
                add("state_mismatch", line, f"{label}.{field}")

    for line, row in enumerate(rows, 1):
        body, response, path = row.get("request") or {}, row.get("response"), row.get("path")
        request_id = body.get("request_id")
        fingerprint = digest([path, body])
        check_state(row.get("state_before"), "state_before", line)
        if request_id is None:
            add("missing_request_id", line, kind="missing")
            request_id = f"missing-row-{line}"
        if request_id in ids and ids[request_id] != fingerprint:
            add("request_id_body_conflict", line)
        if pending_id is not None and request_id != pending_id:
            add("new_id_while_prior_response_unresolved", line)
        success = isinstance(response, dict) and response.get("accepted") is True
        rejected = isinstance(response, dict) and response.get("accepted") is False
        response_expected = virtual()
        duplicate = request_id in confirmed
        if success:
            ids[request_id] = fingerprint
            counts["accepted_response_rows"] += 1
            if duplicate:
                counts["duplicate_accepted_responses"] += 1
                prior = confirmed[request_id]
                response_expected = prior["virtual_time_s"]
                if digest(response) != prior["response_hash"]:
                    add("cached_response_changed", line)
            else:
                if state["exited"]:
                    add("new_accepted_action_after_exit", line)
                if path == "/enter":
                    if state["entered"]:
                        add("duplicate_enter_with_new_id", line)
                    state["entered"] = True
                elif path in ("/measure", "/clear"):
                    if not state["entered"]:
                        add("action_before_enter", line)
                    position, channel = point(body.get("position")), body.get("channel")
                    if position is None or not isinstance(channel, int) or isinstance(channel, bool) or not 1 <= channel <= 20:
                        add("invalid_action_coordinates_or_channel", line)
                    else:
                        counts["walk_distance_m"] += math.dist(state["position"], position)
                        state["position"] = position
                        if path == "/measure":
                            counts["switches"] += int(channel != state["current_channel"])
                            state["current_channel"] = channel
                            counts["measures"] += 1
                            outcome = response.get("measure_result")
                            if outcome not in ("near", "direction", "no_signal"):
                                add("invalid_measure_result", line)
                            if outcome == "direction" and (not number(response.get("svd_deg")) or not 0 <= response["svd_deg"] < 360):
                                add("invalid_reported_angle", line)
                        else:
                            # /clear moves even when it fails and never tunes
                            # the measurement receiver channel.
                            counts["clear_attempts"] += 1
                            if response.get("clear_result") == "success":
                                if channel in cleared:
                                    add("repeat_success_same_channel", line)
                                cleared.add(channel)
                                counts["clear_successes"] += 1
                            elif response.get("clear_result") != "no_target_in_range":
                                add("invalid_clear_result", line)
                elif path == "/exit":
                    if not state["entered"]:
                        add("exit_before_enter", line)
                    state["exited"] = True
                else:
                    add("unknown_accepted_path", line)
                counts["unique_accepted_actions"] += 1
                response_expected = virtual()
                confirmed[request_id] = {"virtual_time_s": response_expected, "response_hash": digest(response)}
                accepted.append({"line": line, "request": body, "response": response, "path": path,
                                 "computed_virtual_time_s": response_expected})
            pending_id = None
        elif rejected:
            counts["rejected_response_rows"] += 1
            # Attachment 2: rejected responses report a placeholder zero, not
            # the current virtual clock. A rejected ID is not consumed and may
            # be reused after correcting the rejected request's fields.
            response_expected = 0.0
            if request_id not in confirmed:
                ids.pop(request_id, None)
            pending_id = None
        else:
            ids[request_id] = fingerprint
            counts["unconfirmed_transport_rows"] += 1
            pending_id = request_id
        residual = None
        if isinstance(response, dict) and "accepted" in response:
            reported = response.get("virtual_time_s")
            if not number(reported):
                add("missing_response_virtual_time", line, kind="missing")
            else:
                residual = reported - response_expected
                max_time_error = max(max_time_error, abs(residual))
                if abs(residual) > per_action_time_tolerance * (counts["unique_accepted_actions"] + 1):
                    add("response_time_mismatch", line, f"residual_s={residual:.12g}")
        check_state(row.get("state_after"), "state_after", line)
        records.append({"request_line": line, "path": path, "accepted": success, "rejected": rejected,
                        "duplicate_accepted": success and duplicate, "reported_virtual_time_s": response.get("virtual_time_s") if isinstance(response, dict) else None,
                        "computed_response_virtual_time_s": response_expected, "residual_s": residual})
    return {"counts": counts, "state": {**state, "cleared_channels": sorted(cleared)},
            "computed_virtual_time_s": virtual(), "max_response_time_error_s": max_time_error,
            "max_state_time_error_s": max_state_time_error,
            "time_tolerance_at_end_s": per_action_time_tolerance * (counts["unique_accepted_actions"] + 1),
            "unresolved_last_request": pending_id is not None, "issues": issues, "action_audit": records,
            "accepted_actions": accepted}


def audit_survey(assignment, result, decisions, accepted):
    issues = []
    def fail(code, detail="", missing=False):
        issues.append({"kind": "missing" if missing else "error", "code": code, "detail": detail})
    starts = [d for d in decisions if d.get("event") == "start"]
    plans = [d for d in decisions if d.get("event") == "survey_plan"]
    if len(starts) != 1 or len(plans) != 1:
        fail("survey_start_or_plan_missing_or_duplicate", missing=not starts or not plans)
    start, plan = starts[0] if starts else {}, plans[0] if plans else {}
    plan_decision_index = next((i for i, d in enumerate(decisions) if d is plan), None)
    coverage_decision_indices = [i for i, d in enumerate(decisions) if d.get("event") == "measure" and d.get("phase") == "coverage"]
    extra_decision_indices = [i for i, d in enumerate(decisions) if d.get("event") == "measure" and d.get("phase") == "survey"]
    if plan_decision_index is not None and ((coverage_decision_indices and plan_decision_index <= max(coverage_decision_indices)) or
                                            (extra_decision_indices and plan_decision_index >= min(extra_decision_indices))):
        fail("survey_plan_not_frozen_between_coverage_and_extra")
    seed = assignment.get("survey_seed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        fail("missing_assignment_survey_seed", missing=True)
    for d in decisions + [result]:
        if d.get("survey_seed") != seed:
            fail("survey_seed_mismatch", str(d.get("event", "result")))
            break
    points = [point(p) for p in start.get("coverage_points", [])]
    expected_count = {3: 140, 4: 620}.get(assignment.get("problem"))
    if any(p is None for p in points) or len(points) * 20 != expected_count or len(set(points)) != len(points):
        fail("survey_fixed_station_count_mismatch")
    coverage_hash = digest(start.get("coverage_points", []))
    if start.get("coverage_sha256") != coverage_hash or result.get("coverage_sha256") != coverage_hash:
        fail("survey_coverage_hash_mismatch")
    plan_hash = digest(plan.get("plan", []))
    if plan.get("plan_sha256") != plan_hash or result.get("plan_sha256") != plan_hash:
        fail("survey_plan_hash_mismatch")
    decisions_by_id = {}
    for d in decisions:
        if d.get("event") == "measure" and d.get("request_id"):
            if d["request_id"] in decisions_by_id:
                fail("duplicate_measure_decision_id")
            decisions_by_id[d["request_id"]] = d
    coverage, extras, cleanup, clear_lines = [], [], [], []
    actual_phase_counts = {p: Counter() for p in ("coverage", "survey", "cleanup")}
    extra_plan = [(target.get("channel"), probe) for target in plan.get("plan", []) for probe in target.get("probes", [])]
    for action in accepted:
        if action["path"] == "/clear":
            clear_lines.append(action["line"])
            continue
        if action["path"] != "/measure":
            continue
        body = action["request"]
        d = decisions_by_id.get(body.get("request_id"))
        if d is None:
            fail("accepted_survey_measure_missing_decision", str(action["line"]), True)
            continue
        if not close(point(d.get("position")), point(body.get("position"))) or d.get("target_channel", d.get("channel")) != body.get("channel"):
            fail("measure_decision_request_mismatch", str(action["line"]))
        dr = d.get("response") or {}
        if any(dr.get(k) != action["response"].get(k) for k in ("accepted", "measure_result", "svd_deg", "virtual_time_s")):
            fail("measure_decision_response_mismatch", str(action["line"]))
        phase = d.get("phase")
        if phase not in actual_phase_counts:
            fail("missing_or_invalid_survey_phase", str(action["line"]), True)
            continue
        actual_phase_counts[phase]["measures"] += 1
        {"coverage": coverage, "survey": extras, "cleanup": cleanup}[phase].append((action, d))
    wanted = [(p, channel) for p in points for channel in range(1, 21)]
    actual = [(point(a["request"].get("position")), a["request"].get("channel")) for a, _ in coverage]
    exact_coverage = (len(actual) == len(wanted) and all(close(a[0], b[0]) and a[1] == b[1] for a, b in zip(actual, wanted)))
    if not exact_coverage:
        fail("survey_all20_every_station_order_certificate_failed")
    if len(extras) != len(extra_plan):
        fail("survey_extra_count_mismatch")
    for (action, d), (channel, probe) in zip(extras, extra_plan):
        design = d.get("measurement_design") or {}
        if action["request"].get("channel") != channel or not close(point(action["request"].get("position")), point(probe.get("position"))):
            fail("survey_extra_frozen_plan_action_mismatch", str(action["line"]))
        if design.get("plan_sha256") != plan_hash or d.get("station_id") != probe.get("station_id"):
            fail("survey_extra_plan_hash_or_station_mismatch", str(action["line"]))
    scan_end = max((a["line"] for a, _ in coverage), default=0)
    extra_end = max((a["line"] for a, _ in extras), default=scan_end)
    if extras and min(a["line"] for a, _ in extras) <= scan_end:
        fail("survey_extra_before_full_coverage")
    if clear_lines and min(clear_lines) <= extra_end:
        fail("clear_before_full_survey_finished")
    eligible = sorted({a["request"]["channel"] for a, _ in coverage if a["response"].get("measure_result") in ("near", "direction")})
    if plan.get("eligible_channels") != eligible or result.get("eligible_channels") != eligible:
        fail("survey_eligible_channels_mismatch")
    sample_count = result.get("sample_count")
    if sample_count != 2:
        fail("nonstandard_round_survey_sample_count")
    per_target_probe_count = {3: 16, 4: 19}.get(assignment.get("problem"))
    if any(len(target.get("probes", [])) != per_target_probe_count for target in plan.get("plan", [])):
        fail("nonstandard_round_survey_probe_count")
    if isinstance(seed, int) and isinstance(sample_count, int) and 0 <= sample_count <= 20:
        sampled = random.Random(seed).sample(eligible, min(sample_count, len(eligible)))
        if plan.get("sampled_channels") != sampled or result.get("sampled_channels") != sampled or [t.get("channel") for t in plan.get("plan", [])] != sampled:
            fail("survey_seeded_sampling_mismatch")
    else:
        fail("survey_sample_count_missing", missing=True)
    for a in accepted:
        if a["path"] == "/clear":
            actual_phase_counts["cleanup"]["clear_attempts"] += 1
            actual_phase_counts["cleanup"]["clear_successes"] += int(a["response"].get("clear_result") == "success")
    for phase, fields in actual_phase_counts.items():
        claimed = (result.get("phase_stats") or {}).get(phase, {})
        for field in ("measures", "clear_attempts", "clear_successes"):
            if claimed.get(field) != fields[field]:
                fail("survey_phase_count_mismatch", f"{phase}.{field}")
    return {"issues": issues, "expected_coverage_measures": expected_count, "actual_coverage_measures": len(coverage),
            "actual_survey_measures": len(extras), "all20_every_actual_station_certificate": exact_coverage,
            "plan_sha256_recomputed": plan_hash, "coverage_sha256_recomputed": coverage_hash,
            "seeded_sampling_checked": isinstance(seed, int), "actual_phase_counts": actual_phase_counts}


def check_freeze(round_root, project_root):
    issues = []
    validation_path = round_root / "validation_freeze.json"
    is_validation = validation_path.exists()
    freeze = read_json(round_root / "baseline_freeze.json", issues, True)
    files = freeze.get("files") or {}
    if len(files) != 10:
        issues.append({"kind": "error", "code": "frozen_core_file_count", "detail": str(len(files))})
    records = []
    archive_path = round_root / "baseline_source.zip"
    if is_validation and not archive_path.exists():
        archive_path = round_root.parent / "round1/baseline_source.zip"
    archive = zipfile.ZipFile(archive_path) if archive_path.exists() else None
    if archive is None:
        issues.append({"kind": "missing", "code": "missing_frozen_source_archive", "detail": archive_path.name})
    try:
        for name, expected in files.items():
            path = (project_root / name).resolve()
            if not path.is_relative_to(project_root.resolve()):
                issues.append({"kind": "error", "code": "freeze_path_outside_project", "detail": name})
                continue
            actual = sha(path) if path.exists() else None
            archived = hashlib.sha256(archive.read(name)).hexdigest() if archive and name in archive.namelist() else None
            records.append({"file": name, "expected_sha256": expected, "current_sha256": actual,
                            "archive_sha256": archived, "current_matches": actual == expected, "archive_matches": archived == expected})
            if actual != expected or archived != expected:
                issues.append({"kind": "error", "code": "frozen_core_hash_mismatch", "detail": name})
    finally:
        if archive:
            archive.close()
    core_digest = hashlib.sha256()
    for record in sorted(records, key=lambda r: r["file"]):
        path = project_root / record["file"]
        if path.exists():
            core_digest.update(path.name.encode())
            core_digest.update(path.read_bytes())
    if core_digest.hexdigest() != freeze.get("source_sha256"):
        issues.append({"kind": "error", "code": "aggregate_core_hash_mismatch", "detail": ""})
    survey, survey_actual, validation = {}, None, None
    if not is_validation:
        survey = read_json(round_root / "survey_freeze.json", issues, True)
        survey_path = project_root / "src/bsolver/survey.py"
        survey_actual = sha(survey_path) if survey_path.exists() else None
        if survey_actual != survey.get("sha256") or not survey_actual:
            issues.append({"kind": "error", "code": "frozen_survey_hash_mismatch", "detail": "src/bsolver/survey.py"})
    else:
        validation = read_json(validation_path, issues, True)
        plan = read_json(round_root / "plan.json", issues, True)
        if not (round_root / "plan.json").exists() or sha(round_root / "plan.json") != validation.get("plan_sha256"):
            issues.append({"kind": "error", "code": "validation_plan_freeze_hash_mismatch", "detail": ""})
        candidate = Path(plan.get("candidate_file", "")).resolve()
        if not candidate.is_relative_to(project_root.resolve()) or not candidate.is_file():
            issues.append({"kind": "missing", "code": "validation_candidate_missing_or_outside_project", "detail": ""})
        elif sha(candidate) != validation.get("candidate_sha256") or sha(candidate) != plan.get("candidate_sha256"):
            issues.append({"kind": "error", "code": "validation_candidate_hash_mismatch", "detail": ""})
        expected_manifest_hash = validation.get("baseline_freeze_sha256")
        manifest_options = [round_root / "baseline_freeze.json", round_root.parent / "round1/baseline_freeze.json"]
        if not any(p.exists() and sha(p) == expected_manifest_hash for p in manifest_options):
            issues.append({"kind": "error", "code": "validation_baseline_manifest_hash_mismatch", "detail": ""})
        if validation.get("baseline_source_sha256") != freeze.get("source_sha256"):
            issues.append({"kind": "error", "code": "validation_baseline_aggregate_reference_mismatch", "detail": ""})
        for category in ("candidate_dependency_hashes", "runtime_hashes"):
            mapping = validation.get(category)
            if not isinstance(mapping, dict):
                issues.append({"kind": "missing", "code": "validation_dependency_manifest_missing", "detail": category})
                continue
            for name, expected in mapping.items():
                path = (project_root / name).resolve()
                if not path.is_relative_to(project_root.resolve()) or not path.is_file() or sha(path) != expected:
                    issues.append({"kind": "error", "code": "validation_dependency_hash_mismatch", "detail": name})
        if validation.get("case_count") != len(plan.get("cases", [])):
            issues.append({"kind": "error", "code": "validation_plan_count_mismatch", "detail": ""})
        if validation.get("formal_authorized") is not False or plan.get("formal_authorized") is not False:
            issues.append({"kind": "error", "code": "validation_not_practice_only", "detail": ""})
    return {"source_sha256": freeze.get("source_sha256"), "n_core_files": len(records), "files": records,
            "archive_source": str(archive_path.resolve()), "current_aggregate_core_sha256": core_digest.hexdigest(),
            "survey_sha256": survey_actual, "survey_expected_sha256": survey.get("sha256"),
            "freeze_kind": "independent_arm_validation" if is_validation else "baseline_and_survey",
            "validation_manifest": validation, "issues": issues}


def audit_case(directory, *, freeze=None):
    issues = []
    assignment = read_json(directory / "assignment.json", issues, True)
    result = read_json(directory / "result.json", issues, True)
    audit = read_json(directory / "post_exit_audit.json", issues, True)
    requests = read_rows(directory / "requests.jsonl", issues)
    decisions = read_rows(directory / "decisions.jsonl", issues)
    replay = replay_requests(requests)
    issues.extend(replay["issues"])
    tolerance = replay["time_tolerance_at_end_s"]
    for field in ("walk_distance_m", "switches", "measures", "clear_attempts", "clear_successes"):
        actual, claimed = replay["counts"][field], result.get(field)
        if not number(claimed):
            issues.append({"kind": "missing", "code": "missing_result_field", "detail": field})
        elif abs(actual - claimed) > (1e-6 if field == "walk_distance_m" else 0):
            issues.append({"kind": "error", "code": "result_count_mismatch", "detail": field})
    for label, value in (("result.total_virtual_time_s", result.get("total_virtual_time_s")),
                         ("result.independently_accounted_time_s", result.get("independently_accounted_time_s")),
                         ("post_exit_audit.total_virtual_time_s", audit.get("total_virtual_time_s"))):
        if not number(value):
            issues.append({"kind": "missing", "code": "missing_time_field", "detail": label})
        elif abs(value - replay["computed_virtual_time_s"]) > tolerance:
            issues.append({"kind": "error", "code": "aggregate_time_mismatch", "detail": label})
    if result.get("status") == "complete":
        n = audit.get("source_total_post_exit", audit.get("N"))
        if n is None:
            issues.append({"kind": "missing", "code": "post_exit_N_missing", "detail": ""})
        elif n != len(replay["state"]["cleared_channels"]) or audit.get("cleared") != n:
            issues.append({"kind": "error", "code": "false_complete_against_post_exit_count", "detail": ""})
        if not replay["state"]["exited"] or replay["unresolved_last_request"]:
            issues.append({"kind": "error", "code": "complete_exit_not_confirmed", "detail": ""})
        certificate_channels = (result.get("stop_evidence") or {}).get("cleared_channels")
        if certificate_channels != replay["state"]["cleared_channels"]:
            issues.append({"kind": "error", "code": "stop_evidence_cleared_channels_mismatch", "detail": ""})
    for field in ("case_id", "problem", "protocol", "split"):
        if field in audit and audit[field] != assignment.get(field):
            issues.append({"kind": "error", "code": "post_exit_assignment_mismatch", "detail": field})
    if assignment.get("arm") is not None:
        if result.get("arm") != assignment["arm"] or result.get("protocol") != assignment["arm"]:
            issues.append({"kind": "error", "code": "validation_result_arm_mismatch", "detail": ""})
        declared = assignment.get("policy_spec") or {}
        cfg = declared.get("solver_config", declared) if "solver_class" not in declared else declared.get("solver_config", {})
        for key, value in cfg.items():
            if key in ("solver_class", "nosignal_config"):
                continue
            if (result.get("config") or {}).get(key) != value:
                issues.append({"kind": "error", "code": "validation_declared_policy_result_mismatch", "detail": key})
        if "solver_class" in declared and (result.get("policy_spec") or {}).get("solver_class") != declared["solver_class"]:
            issues.append({"kind": "error", "code": "validation_solver_class_mismatch", "detail": ""})
        for key, value in (declared.get("nosignal_config") or {}).items():
            if ((result.get("policy_spec") or {}).get("nosignal_config") or {}).get(key) != value:
                issues.append({"kind": "error", "code": "validation_nosignal_config_mismatch", "detail": key})
    filename, case_code = audit.get("original_log_filename"), audit.get("case_code")
    original = {"filename": filename, "sha256": None, "bytes": None}
    if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".jlog"):
        issues.append({"kind": "missing", "code": "original_log_filename_missing_or_invalid", "detail": ""})
    else:
        path = directory / "original_logs" / filename
        if not path.exists():
            issues.append({"kind": "missing", "code": "original_log_copy_missing", "detail": filename})
        else:
            original.update(sha256=sha(path), bytes=path.stat().st_size)
            if original["sha256"] != audit.get("original_log_sha256") or original["bytes"] != audit.get("original_log_bytes"):
                issues.append({"kind": "error", "code": "original_log_copy_hash_or_size_mismatch", "detail": filename})
        if not case_code or not re.fullmatch(rf"practice-p{assignment.get('problem')}-\d+-{re.escape(case_code or '')}\.jlog", filename):
            issues.append({"kind": "error", "code": "original_filename_problem_or_case_mismatch", "detail": filename})
    if freeze and result.get("baseline_sha256") != freeze["source_sha256"]:
        issues.append({"kind": "error", "code": "case_baseline_freeze_hash_mismatch", "detail": ""})
    survey = None
    if assignment.get("protocol") == "survey":
        survey = audit_survey(assignment, result, decisions, replay["accepted_actions"])
        issues.extend(survey["issues"])
    return {"case_id": assignment.get("case_id", directory.name), "directory": str(directory.resolve()),
            "problem": assignment.get("problem"), "protocol": assignment.get("protocol"), "split": assignment.get("split"),
            "arm": assignment.get("arm"), "round": assignment.get("round"),
            "result_status": result.get("status"), "case_code": case_code,
            "audit_status": "issues_found" if issues else "passed", "issues": issues,
            "n_request_rows": len(requests), "n_decision_rows": len(decisions),
            **replay["counts"], "computed_virtual_time_s": replay["computed_virtual_time_s"],
            "reported_virtual_time_s": result.get("total_virtual_time_s"),
            "max_response_time_error_s": replay["max_response_time_error_s"],
            "max_state_time_error_s": replay["max_state_time_error_s"], "time_tolerance_at_end_s": tolerance,
            "final_state": replay["state"], "survey": survey, "original_log": original,
            "source_sha256": {name: sha(directory / name) for name in ("assignment.json", "requests.jsonl", "decisions.jsonl", "result.json", "post_exit_audit.json") if (directory / name).exists()},
            "action_audit": replay["action_audit"]}


def write_csv(path, rows):
    keys = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()})


def audit_round(round_root, output, *, project_root=None, complete_only=False):
    round_root, output = Path(round_root), Path(output)
    project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[1]
    freeze = check_freeze(round_root, project_root)
    plan_issues = []
    plan = read_json(round_root / "plan.json", plan_issues, True)
    allocation = {row.get("case_id"): row for row in plan.get("cases", [])}
    if len(allocation) != len(plan.get("cases", [])):
        plan_issues.append({"kind": "error", "code": "duplicate_plan_case_id", "detail": ""})
    cases_root = round_root / "cases" if (round_root / "cases").exists() else round_root
    paths = sorted({p.parent for name in ("assignment.json", "requests.jsonl", "result.json", "post_exit_audit.json")
                    for p in cases_root.rglob(name)})
    results, skipped, planned = [], [], []
    for directory in paths:
        assignment = read_json(directory / "assignment.json", [])
        planned_assignment = allocation.get(assignment.get("case_id"))
        if planned_assignment is None:
            plan_issues.append({"kind": "missing", "code": "case_missing_from_round_plan", "detail": directory.name})
        else:
            for field in ("problem", "protocol", "survey_seed", "allocation_hash", "split", "sequence",
                          "arm", "block", "round", "policy_spec", "allocation_seed"):
                if assignment.get(field) != planned_assignment.get(field):
                    plan_issues.append({"kind": "error", "code": "assignment_round_plan_mismatch", "detail": f"{directory.name}:{field}"})
        result = read_json(directory / "result.json", [])
        started = bool(assignment.get("attempt_started") or any((directory / name).exists() for name in
                       ("requests.jsonl", "result.json", "post_exit_audit.json", "launch_intent.json")))
        if not started:
            planned.append(directory.name)
        elif complete_only and (result.get("status") != "complete" or not (directory / "post_exit_audit.json").exists()):
            skipped.append({"case_id": directory.name, "status": result.get("status", "in_progress_or_unfinished"),
                            "reason": "complete-prefix snapshot excludes incomplete or not-yet-post-audited attempts"})
        else:
            results.append(audit_case(directory, freeze=freeze))
    duplicate_codes, duplicate_logs = defaultdict(list), defaultdict(list)
    for row in results:
        if row["case_code"]:
            duplicate_codes[row["case_code"]].append(row["case_id"])
        if row["original_log"]["filename"]:
            duplicate_logs[row["original_log"]["filename"]].append(row["case_id"])
    global_issues = freeze["issues"] + plan_issues + [
        {"kind": "error", "code": kind, "detail": values} for kind, mapping in
        (("duplicate_official_case_code", duplicate_codes), ("duplicate_original_log_filename", duplicate_logs))
        for values in mapping.values() if len(values) > 1]
    issue_rows = [{"case_id": row["case_id"], **issue} for row in results for issue in row["issues"]]
    issue_rows += [{"case_id": "__round__", **issue} for issue in global_issues]
    counts = Counter(row["audit_status"] for row in results)
    strata = defaultdict(list)
    for row in results:
        strata[(row["problem"], row["protocol"], row["arm"], row["split"])].append(row)
    status = ("issues_found" if issue_rows else "no_attempts_audited" if not results else
              "passed_complete_prefix" if complete_only else "passed_audited_attempts")
    overall = {"schema_version": 1, "audit_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
               "round_root": str(round_root.resolve()), "complete_prefix_only": complete_only,
               "overall_status": status,
               "n_case_directories": len(paths), "n_planned_unstarted": len(planned), "n_skipped_in_progress_or_incomplete": len(skipped),
               "n_audited_cases": len(results), "case_status_counts": dict(counts), "n_issues": len(issue_rows),
               "issue_counts": dict(Counter(r["code"] for r in issue_rows)),
               "missing_counts": dict(Counter(r["code"] for r in issue_rows if r["kind"] == "missing")),
               "n_request_rows": sum(r["n_request_rows"] for r in results),
               "n_unique_accepted_actions": sum(r["unique_accepted_actions"] for r in results),
               "n_accepted_measures": sum(r["measures"] for r in results),
               "n_clear_attempts": sum(r["clear_attempts"] for r in results),
               "n_clear_successes": sum(r["clear_successes"] for r in results),
               "max_response_time_error_s": max((r["max_response_time_error_s"] for r in results), default=0),
               "max_state_time_error_s": max((r["max_state_time_error_s"] for r in results), default=0),
               "n_survey_cases": sum(r["protocol"] == "survey" for r in results),
               "n_survey_full_station_certificates": sum(bool(r["survey"] and r["survey"]["all20_every_actual_station_certificate"]) for r in results),
               "audited_groups": [{"problem": key[0], "protocol": key[1], "arm": key[2], "split": key[3],
                                   "audited_cases": len(values), "passed_cases": sum(r["audit_status"] == "passed" for r in values),
                                   "complete_cases": sum(r["result_status"] == "complete" for r in values)}
                                  for key, values in sorted(strata.items(), key=lambda item: str(item[0]))],
               "arm_comparison": "audit counts only; official arms are independent cases, no same-case pairing or paired confidence interval",
               "freeze": freeze, "planned_case_ids": planned, "skipped_cases": skipped,
               "scope": "public request/decision/result/post-exit metadata and copied behavior-log hashes only; no hidden files; no external actions",
               "timing_policy": "independent L/5 + switches + 5*measures + 3*clear_attempts + 2*successes; tolerance 1 microsecond per unique action plus 1 microsecond; raw maximum residuals reported"}
    output.mkdir(parents=True, exist_ok=True)
    action_rows = [{"case_id": r["case_id"], **a} for r in results for a in r.pop("action_audit")]
    (output / "overall.json").write_text(json.dumps(overall, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (output / "cases.json").write_text(json.dumps(results, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    write_csv(output / "cases.csv", [{k: v for k, v in r.items() if k not in ("survey", "source_sha256", "final_state")} for r in results])
    write_csv(output / "issues.csv", issue_rows)
    write_csv(output / "actions.csv", action_rows)
    write_csv(output / "overall.csv", [{k: v for k, v in overall.items() if k not in ("freeze", "planned_case_ids", "skipped_cases")}])
    return overall


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--complete-only", action="store_true", help="Audit immutable completed and post-audited prefix; list pending/planned separately")
    args = parser.parse_args(argv)
    result = audit_round(args.round_root, args.output, project_root=args.project_root, complete_only=args.complete_only)
    print(json.dumps({k: result[k] for k in ("overall_status", "n_audited_cases", "n_planned_unstarted",
                     "n_skipped_in_progress_or_incomplete", "n_request_rows", "n_issues", "max_response_time_error_s")}, ensure_ascii=False))
    return 1 if result["n_issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
