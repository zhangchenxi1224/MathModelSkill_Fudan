"""Independent read-only stage 2/3 audit; closed confirmation until selection.

Imports only our independent auditors, never the policy or local environment.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import datetime as dt
import gzip
import importlib.util
import json
import math
from pathlib import Path

_spec = importlib.util.spec_from_file_location("local_stage_audit_shared", Path(__file__).with_name("audit_local_stage1.py"))
shared = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shared)
digest, sha, load, issue = shared.digest, shared.sha, shared.load, shared.issue


def check_design(manifest, previous):
    ignored = {"unexpected_stage_or_baseline", "manifest_declared_count_mismatch", "predeclared_partition_hash_rank_mismatch"}
    issues = [r for r in shared.check_manifest(manifest) if r["code"] not in ignored]
    stage = manifest.get("stage")
    if stage not in (2, 3):
        return issues + [issue("unexpected_later_stage")]
    baseline = "normal" if stage == 2 else "joint"
    if manifest.get("baseline") != baseline:
        issues.append(issue("later_stage_baseline_mismatch"))
    planned = sum(len(manifest.get("arm_definitions", {}).get(str(c["problem"]), [])) for c in manifest["scenarios"])
    if manifest.get("planned_strategy_runs") != planned or manifest.get("distinct_scenarios") != len(manifest["scenarios"]):
        issues.append(issue("later_stage_planned_count_mismatch"))
    for problem in (3, 4):
        arms = manifest.get("arm_definitions", {}).get(str(problem), [])
        wanted = {"normal", "nosignal"} if stage == 2 else {"joint", "immediate", "scan_then_clear"}
        if {a["variant"] for a in arms} != wanted or len(arms) != len(wanted):
            issues.append(issue("later_stage_arm_set_mismatch", problem))
        limit = manifest.get("selected_local_limits", {}).get(str(problem))
        if limit not in (1, 2, 3, 5):
            issues.append(issue("invalid_inherited_local_limit", problem))
            continue
        for arm in arms:
            expected = shared.expected_config(problem, f"joint_triangular_l{limit}")
            expected["scheduling"] = "joint" if stage == 2 else arm["variant"]
            expected_class = arm["variant"] if stage == 2 else manifest["selected_sensing_modes"][str(problem)]
            if arm.get("solver_config") != expected or arm.get("solver_class") != expected_class:
                issues.append(issue("single_factor_config_mismatch", f"P{problem}:{arm['variant']}"))
            if (expected_class == "normal" and arm.get("nosignal_config") is not None) or (expected_class == "nosignal" and not isinstance(arm.get("nosignal_config"), dict)):
                issues.append(issue("nosignal_config_missing_or_unexpected", f"P{problem}:{arm['variant']}"))
    groups = defaultdict(list)
    for case in manifest["scenarios"]:
        groups[case["pool"], case["problem"], case["composition_model"]].append(case)
    for group in groups.values():
        # The generator preallocates before adding the descriptive stage prefix.
        ordered = sorted(group, key=lambda c: shared.rank_seed(manifest["master_seed"], "confirmation_allocation", c["case_id"].removeprefix(f"stage{stage}-")))
        for i, case in enumerate(ordered):
            expected = "confirmation" if i < len(group) // 5 else "development"
            if case.get("partition") != expected:
                issues.append(issue("later_stage_preallocated_hash_rank_mismatch", case["case_id"]))
    signatures = lambda m: {digest({k: c[k] for k in ("problem", "sources", "error_field")}) for c in m["scenarios"]}
    old_seeds, old_worlds, old_refs = set(), set(), set()
    for old in previous:
        clone = dict(old)
        expected = clone.pop("manifest_sha256", None)
        if digest(clone) != expected:
            issues.append(issue("prior_manifest_hash_mismatch", old.get("stage")))
        old_seeds.update(c["seed"] for c in old["scenarios"])
        old_worlds.update(signatures(old))
        old_refs.add((old["stage"], old["manifest_sha256"], old["master_seed"]))
    expected_refs = {(r["stage"], r["manifest_sha256"], r["master_seed"]) for r in manifest.get("previous_manifests", [])}
    if old_refs != expected_refs or not set(range(1, stage)) <= {r[0] for r in old_refs}:
        issues.append(issue("missing_or_mismatched_prior_manifest_evidence"))
    if old_seeds & {c["seed"] for c in manifest["scenarios"]} or old_worlds & signatures(manifest):
        issues.append(issue("fresh_world_or_seed_overlaps_earlier_stage"))
    return issues


def check_nosignal_log(path, result, arm):
    issues, counts, pending = [], Counter(), None
    with gzip.open(path, "rt", encoding="utf8") as stream:
        for line, raw in enumerate(stream, 1):
            event = json.loads(raw)
            if event.get("event") == "start" and event.get("nosignal_config") != arm.get("nosignal_config"):
                issues.append(issue("nosignal_start_config_mismatch", line))
            if event.get("event") == "choose_measurement":
                info = event.get("selection") or {}
                counts["nosignal_choices"] += 1
                counts["nosignal_changed_choices"] += int(bool(info.get("selection_changed_from_baseline")))
                counts["nosignal_baseline_fallbacks"] += int("baseline_fallback_reason" in info)
                counts["nosignal_convex_mixed_choices"] += int(bool(info.get("convex_visibility_mixed_candidates")))
                # A documented whole-choice baseline fallback carries the
                # original selector's evaluation schema, without branch weights.
                evaluations = [] if "baseline_fallback_reason" in info else info.get("evaluations") or []
                if evaluations:
                    for evaluation in evaluations:
                        probabilities = evaluation.get("branch_probabilities") or {}
                        terms = evaluation.get("cost_terms_s") or {}
                        if (set(probabilities) != {"direction", "near", "no_signal"}
                                or any(not shared.number(p) or not 0 <= p <= 1 for p in probabilities.values())
                                or abs(sum(probabilities.values()) - 1) > 1e-12):
                            issues.append(issue("nosignal_branch_weights_invalid", line))
                            continue
                        score = terms["movement"] + terms["measurement"] + sum(probabilities[k] * terms[t] for k, t in
                            (("direction", "direction_branch_proxy"), ("near", "near_branch_clear"), ("no_signal", "no_signal_immediate_fallback")))
                        if abs(score - evaluation["score_s"]) > 1e-9:
                            issues.append(issue("nosignal_reported_score_mismatch", line))
                    selected = min(evaluations, key=lambda e: e["score_s"])
                    if info.get("selected") != selected:
                        issues.append(issue("nosignal_selected_not_logged_score_minimum", line))
                    pending = (event.get("target_channel"), shared.point(selected["point"]))
            elif event.get("event") == "measure" and pending is not None:
                if (event.get("knowledge", {}).get("channel"), shared.point(event.get("position"))) != pending:
                    issues.append(issue("nosignal_selected_point_not_actual_next_measure", line))
                pending = None
    if pending is not None:
        issues.append(issue("nosignal_choice_without_following_measure"))
    for field in ("nosignal_choices", "nosignal_changed_choices", "nosignal_baseline_fallbacks", "nosignal_convex_mixed_choices"):
        if result.get(field) != counts[field]:
            issues.append(issue("nosignal_result_counter_mismatch", field))
    return {"issues": issues, **dict(counts),
            "scope": "Logged branch normalization, heuristic score, argmin, subsequent observable action and counters only; no assertion of a calibrated hidden-state posterior."}


def audit_stage(directory, output, *, partition="development", selection=None, complete_only=False, project=None):
    directory, output = Path(directory).resolve(), Path(output).resolve()
    project = Path(project).resolve() if project else Path(__file__).resolve().parents[1]
    manifest, frozen = load(directory / "manifest.json"), load(directory / "source_manifest.json")
    freeze = shared.check_freeze(directory, project)
    previous = [load(project / name) for name in frozen["inputs"] if name.endswith("manifest.json") and
                (project / name).resolve() != (directory / "manifest.json").resolve() and "scenarios" in load(project / name)]
    issues = freeze["issues"] + check_design(manifest, previous)
    choice = None
    if partition not in ("development", "confirmation"):
        raise ValueError("Invalid partition")
    if partition == "confirmation":
        if selection is None:
            raise ValueError("Confirmation requires explicit frozen selection")
        choice = load(Path(selection))
        clone = dict(choice)
        expected = clone.pop("selection_sha256", None)
        if (digest(clone) != expected or choice.get("manifest_sha256") != manifest["manifest_sha256"]
                or not choice.get("frozen_before_confirmation") or choice.get("selection_basis") != "development_only"
                or digest(load(directory / "development/results.json")) != choice.get("development_results_sha256")):
            raise ValueError("Selection or its development input changed")
    allocation = load(directory / partition / "allocation.json")
    if allocation != {"manifest_sha256": manifest["manifest_sha256"], "partition": partition,
                      "selection_sha256": choice.get("selection_sha256") if choice else None}:
        issues.append(issue("partition_allocation_freeze_mismatch"))
    cases = [c for c in manifest["scenarios"] if c["partition"] == partition]
    rows, pending = [], []
    for i, case in enumerate(cases):
        arms = manifest["arm_definitions"][str(case["problem"])]
        if choice:
            allowed = {manifest["baseline"], choice["selected_variants"][str(case["problem"])]}
            if not allowed <= {a["variant"] for a in arms}:
                raise ValueError("Unregistered confirmation selection")
            arms = [a for a in arms if a["variant"] in allowed]
        paths = [(arm, directory / partition / "runs" / case["pool"] / case["case_id"] / arm["variant"]) for arm in arms]
        missing = [f"{arm['variant']}/{name}" for arm, path in paths for name in ("result.json", "requests.jsonl.gz", "decisions.jsonl.gz") if not (path / name).exists()]
        if missing:
            pending.append({"case_id": case["case_id"], "missing": missing})
            if not complete_only:
                issues.append(issue("missing_later_stage_run_artifacts", case["case_id"]))
            continue
        case_rows = []
        for arm, path in paths:
            row = shared.audit_arm(case, arm["variant"], path, None, arm_definition=arm, manifest=manifest)
            if arm["solver_class"] == "nosignal":
                extra = check_nosignal_log(path / "decisions.jsonl.gz", load(path / "result.json"), arm)
                row["nosignal_log_audit"] = extra
                row["issues"].extend(extra["issues"])
                row["audit_status"] = "issues_found" if row["issues"] else "passed"
            case_rows.append(row)
        for field in ("scenario_sha256", "sources_sha256", "error_field_sha256", "policy_core_sha256"):
            if len({r[field] for r in case_rows}) != 1:
                issues.append(issue("later_stage_pair_hash_mismatch", f"{case['case_id']}:{field}"))
        rows.extend(case_rows)
        if (i + 1) % 50 == 0:
            print(json.dumps({"stage": manifest["stage"], "partition": partition, "cases_audited": i + 1, "runs": len(rows)}), flush=True)
    reports = shared.check_development_reports(directory, rows, baseline=manifest["baseline"], partition=partition) if not pending else None
    if reports:
        issues.extend(reports["issues"])
    issue_rows = [{"case_id": "__stage__", **r} for r in issues] + [{"case_id": row["case_id"], "variant": row["variant"], **r} for row in rows for r in row["issues"]]
    overall = {"schema_version": 1, "stage": manifest["stage"], "partition": partition, "audit_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
               "status": "issues_found" if issue_rows else "passed_complete_prefix" if pending else "passed_full_partition",
               "planned_cases": len(cases), "audited_cases": len({r["case_id"] for r in rows}), "audited_runs": len(rows),
               "complete_runs": sum(r["evaluation_complete"] for r in rows), "pending_cases": pending,
               "issue_count": len(issue_rows), "issue_counts": dict(Counter(r["code"] for r in issue_rows)),
               "unselected_confirmation_result_paths_opened": 0, "confirmation_result_paths_opened": len(rows) if choice else 0,
               "totals": {k: sum(r.get(k, 0) for r in rows) for k in ("n_request_rows", "unique_accepted_actions", "measures", "clear_attempts", "clear_successes", "fixed_error_angles_checked")},
               "max_response_time_error_s": max((r["max_response_time_error_s"] for r in rows), default=0),
               "source_freeze": freeze, "report_checks": reports, "manifest_sha256": manifest["manifest_sha256"],
               "selection_sha256": choice.get("selection_sha256") if choice else None,
               "audit_source_sha256": {p.name: sha(p) for p in (Path(__file__), Path(shared.__file__), Path(shared.replay_module.__file__))},
               "scope": "Independent accepted-action replay, fixed local feedback, same-world pair and source hashes, single-factor definitions, fresh-world separation and frozen partition; NoSignal logs additionally checked for arithmetic and actual chosen action. No hidden official files or runtime imports of policy/simulator."}
    output.mkdir(parents=True, exist_ok=True)
    for name, value in (("overall.json", overall), ("cases.json", rows), ("issues.json", issue_rows)):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf8")
    shared.replay_module.write_csv(output / "cases.csv", [{k: v for k, v in row.items() if k not in ("issues", "source_files_sha256", "nosignal_log_audit")} for row in rows])
    shared.replay_module.write_csv(output / "issues.csv", issue_rows)
    return overall


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--partition", choices=("development", "confirmation"), default="development")
    p.add_argument("--selection", type=Path)
    p.add_argument("--complete-only", action="store_true")
    args = p.parse_args(argv)
    result = audit_stage(args.input, args.output, partition=args.partition, selection=args.selection, complete_only=args.complete_only)
    print(json.dumps({k: v for k, v in result.items() if k not in ("source_freeze", "pending_cases")}, ensure_ascii=False, indent=2))
    return int(bool(result["issue_count"]))


if __name__ == "__main__":
    raise SystemExit(main())
