"""Read-only development audit of frozen paired local experiments.

No solver/simulator imports. Confirmation result paths are never opened. The
public-request replay is shared only with our independent official log auditor;
synthetic feedback is independently recomputed from the disclosed local manifest.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics
import zipfile

_spec = importlib.util.spec_from_file_location("independent_request_replay", Path(__file__).with_name("audit_round_integrity.py"))
replay_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(replay_module)
digest, number, point = replay_module.digest, replay_module.number, replay_module.point
BASELINE = "joint_triangular_l5"
LIMITS = {BASELINE: 5, "joint_triangular_l1": 1, "joint_triangular_l2": 2, "joint_triangular_l3": 3}
CORE_NAMES = ["__init__.py", "cli.py", "coverage.py", "experiments.py", "geometry.py", "knowledge.py",
              "protocol.py", "sensing.py", "simulator.py", "strategy.py"]
CORE_SHA = "1b673484453104a5d87d4605d30b20826a3b7ec64769fd6ce8d3c8f7a1ad24f6"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def issue(code, detail=""):
    return {"code": code, "detail": detail}


def rank_seed(seed, *parts):
    payload = json.dumps([seed, *parts], sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return int.from_bytes(hashlib.blake2b(payload.encode(), digest_size=8).digest(), "big")


def check_manifest(manifest):
    issues, seen, groups = [], set(), defaultdict(list)
    clone = dict(manifest)
    expected = clone.pop("manifest_sha256", None)
    if digest(clone) != expected:
        issues.append(issue("manifest_content_hash_mismatch"))
    cases = manifest.get("scenarios", [])
    if manifest.get("stage") != 1 or manifest.get("baseline") != BASELINE:
        issues.append(issue("unexpected_stage_or_baseline"))
    if manifest.get("frozen_core_sha256") != CORE_SHA:
        issues.append(issue("manifest_core_hash_mismatch"))
    if manifest.get("official_formal_authorized") is not False:
        issues.append(issue("manifest_not_local_only"))
    if manifest.get("distinct_scenarios") != len(cases) or manifest.get("planned_strategy_runs") != 4 * len(cases):
        issues.append(issue("manifest_declared_count_mismatch"))
    pools = Counter(c.get("pool") for c in cases)
    if any(pools[k] != n for k, n in manifest.get("pool_sizes", {}).items()):
        issues.append(issue("manifest_pool_count_mismatch"))
    for case in cases:
        identity = case.get("case_id")
        if identity in seen:
            issues.append(issue("duplicate_manifest_case", identity))
        seen.add(identity)
        clone = dict(case)
        expected = clone.pop("scenario_sha256", None)
        if digest(clone) != expected:
            issues.append(issue("scenario_content_hash_mismatch", identity))
        sources = case.get("sources", [])
        channels = [s.get("channel") for s in sources]
        if (case.get("n") != len(sources) or len(set(channels)) != len(channels)
                or case.get("n_directed") != sum(s.get("direction_deg") is not None for s in sources)):
            issues.append(issue("source_composition_mismatch", identity))
        if not 10 <= len(sources) <= 16 or any(not isinstance(c, int) or isinstance(c, bool) or not 1 <= c <= 20 for c in channels):
            issues.append(issue("illegal_source_count_or_channel", identity))
        if case.get("problem") not in (3, 4) or (case.get("problem") == 3 and case.get("n_directed")):
            issues.append(issue("illegal_problem_or_directional_composition", identity))
        for source in sources:
            position = point(source.get("position"))
            radius, direction = source.get("radius"), source.get("direction_deg")
            if (position is None or math.hypot(*position) > 1800 + 1e-8 or not number(radius)
                    or not 1000 <= radius <= 1500 or (direction is not None and not number(direction))):
                issues.append(issue("illegal_source_geometry", identity))
        groups[case.get("pool"), case.get("problem"), case.get("composition_model")].append(case)
    for group in groups.values():
        ordered = sorted(group, key=lambda c: rank_seed(manifest["master_seed"], "confirmation_allocation", c["case_id"]))
        for index, case in enumerate(ordered):
            expected = "confirmation" if index < len(group) // 5 else "development"
            if case.get("partition") != expected:
                issues.append(issue("predeclared_partition_hash_rank_mismatch", case["case_id"]))
    return issues


def check_freeze(directory, project):
    issues, files = [], []
    manifest_path, archive_path = directory / "source_manifest.json", directory / "source_snapshot.zip"
    if not manifest_path.is_file() or not archive_path.is_file():
        return {"issues": [issue("missing_source_freeze_or_archive")], "files": []}
    frozen = load(manifest_path)
    # This field hashes the canonical source-file -> SHA mapping, not ZIP bytes.
    # ZIP container bytes have their own observed SHA below; every member is
    # independently compared with its predispatch expected content SHA.
    if digest(frozen.get("files", {})) != frozen.get("source_bundle_sha256"):
        issues.append(issue("source_bundle_mapping_hash_mismatch"))
    with zipfile.ZipFile(archive_path) as archive:
        for category in ("files", "inputs"):
            for name, expected in frozen.get(category, {}).items():
                path = (project / name).resolve()
                current = sha(path) if path.is_relative_to(project.resolve()) and path.is_file() else None
                try:
                    archived = hashlib.sha256(archive.read(name)).hexdigest()
                except KeyError:
                    archived = None
                files.append({"category": category, "file": name, "expected_sha256": expected,
                              "current_sha256": current, "archived_sha256": archived})
                if current != expected or archived != expected:
                    issues.append(issue("frozen_source_or_input_drift", name))
    aggregate = hashlib.sha256()
    for name in CORE_NAMES:
        path = project / "src/bsolver" / name
        if path.is_file():
            aggregate.update(name.encode())
            aggregate.update(path.read_bytes())
    if aggregate.hexdigest() != CORE_SHA:
        issues.append(issue("current_core_aggregate_mismatch"))
    manifest = load(directory / "manifest.json")
    if manifest.get("manifest_sha256") != frozen.get("manifest_sha256"):
        issues.append(issue("source_freeze_manifest_reference_mismatch"))
    for name in frozen.get("inputs", {}):
        if name.endswith("model_frozen.json") and (project / name).is_file():
            if digest(load(project / name)) != manifest.get("calibration_model_sha256"):
                issues.append(issue("canonical_calibration_input_mismatch"))
        if name.endswith("mechanisms_selected.json") and (project / name).is_file():
            if load(project / name) != manifest.get("mechanism_candidates"):
                issues.append(issue("selected_mechanism_manifest_mismatch"))
    return {"issues": issues, "files": files, "source_manifest_sha256": sha(manifest_path),
            "source_archive_sha256": sha(archive_path), "core_sha256": aggregate.hexdigest(),
            "runner_sha256": frozen.get("files", {}).get("src/bsolver/round_experiments.py")}


def fixed_error(spec, channel, position):
    """Independent evaluation of the declared, synthetic FixedErrorField-v1."""
    x, y = (0.0 if v == 0 else float(v) for v in position)
    def unit(token):
        encoded = f"{spec['seed']}|{token}".encode("ascii")
        return int.from_bytes(hashlib.blake2b(encoded, digest_size=8).digest(), "big") / (2**64 - 1)
    mode, scale = spec["mode"], spec["correlation_length_m"]
    if mode == "deterministic":
        return 2 * unit(f"{channel}|{x.hex()}|{y.hex()}") - 1
    phase = 2 * math.pi * unit(f"phase|{channel}")
    if mode == "correlated":
        return max(-1., min(1., .45 * math.sin(x / scale + phase) + .35 * math.cos(y / scale - .7 * phase)
                               + .20 * math.sin((x + y) / (2 * scale) + 1.3 * phase)))
    if mode == "extreme":
        return 1. if unit(f"cell|{channel}|{math.floor(x / scale)}|{math.floor(y / scale)}") >= .5 else -1.
    raise ValueError(f"Unsupported frozen field {mode}")


def check_feedback(case, accepted):
    sources, cleared, issues, counts = {s["channel"]: s for s in case["sources"]}, set(), [], Counter()
    rounded_us, last = 0, (0., 0.)
    receiver = 1
    for action in accepted:
        body, response, path = action["request"], action["response"], action["path"]
        if path not in ("/measure", "/clear"):
            continue
        location, channel = point(body.get("position")), body.get("channel")
        if location is None:
            issues.append(issue("invalid_feedback_position", action["line"]))
            continue
        source = sources.get(channel)
        distance = math.dist(location, source["position"]) if source else math.inf
        active = source is not None and channel not in cleared
        if path == "/clear":
            success = active and distance <= 20
            expected = "success" if success else "no_target_in_range"
            counts["clear_feedbacks_checked"] += 1
            if response.get("clear_result") != expected:
                issues.append(issue("clear_feedback_contradicts_local_truth", action["line"]))
            if success:
                cleared.add(channel)
            action_cost = 5 if success else 3
        else:
            visible = active and distance <= source["radius"] if source else False
            if visible and source.get("direction_deg") is not None and distance > 0:
                dx, dy = location[0] - source["position"][0], location[1] - source["position"][1]
                angle = math.radians(source["direction_deg"] % 360)
                visible = math.cos(angle) * dx + math.sin(angle) * dy >= -8 * math.ulp(max(1., abs(dx), abs(dy)))
            expected = "no_signal" if not visible else "near" if distance <= 5 else "direction"
            counts["measure_feedbacks_checked"] += 1
            if response.get("measure_result") != expected:
                issues.append(issue("measure_category_contradicts_local_truth", action["line"]))
            if expected == "direction":
                bearing = math.degrees(math.atan2(source["position"][1] - location[1], source["position"][0] - location[0])) % 360
                report = round((bearing + fixed_error(case["error_field"], channel, location)) % 360, 2) % 360
                counts["fixed_error_angles_checked"] += 1
                if response.get("svd_deg") != report:
                    issues.append(issue("direction_contradicts_declared_fixed_error_field", action["line"]))
            action_cost = 5 + int(channel != receiver)
            receiver = channel
        rounded_us += round(math.dist(last, location) / 5 * 1_000_000) + action_cost * 1_000_000
        last = location
        if response.get("virtual_time_s") != rounded_us / 1_000_000:
            issues.append(issue("exact_microsecond_accounting_mismatch", action["line"]))
    return {"issues": issues, "counts": dict(counts), "truth_cleared_channels": sorted(cleared),
            "exact_rounded_virtual_time_s": rounded_us / 1_000_000}


def expected_config(problem, variant):
    return {"problem": problem, "sensing": "active", "scheduling": "joint", "coverage": "triangular",
            "spacing": None, "epsilon_deg": 1.0051, "clear_radius": 19.999, "local_measure_limit": LIMITS[variant],
            "joint_detour_m": 800., "bin_width_deg": 4., "radius_weight": 2., "reserve_real_s": 5.,
            "max_virtual_s": 360000., "nearest_safe": True, "opportunistic_known_measurements": False}


def audit_arm(case, variant, directory, runner_hash):
    issues = []
    result = load(directory / "result.json")
    with gzip.open(directory / "requests.jsonl.gz", "rt", encoding="utf-8") as stream:
        requests = [json.loads(line) for line in stream if line.strip()]
    replay = replay_module.replay_requests(requests)
    issues.extend(replay["issues"])
    feedback = check_feedback(case, replay["accepted_actions"])
    issues.extend(feedback["issues"])
    for field in ("case_id", "problem", "pool", "composition_model", "partition", "mechanism_id", "mechanism_status",
                  "layout", "radius_mode", "error_mode", "n", "n_directed", "scenario_sha256"):
        if result.get(field) != case.get(field):
            issues.append(issue("result_manifest_identity_mismatch", field))
    expected = {"variant": variant, "policy_core_sha256": CORE_SHA, "runner_sha256": runner_hash,
                "error_field_sha256": digest(case["error_field"]), "config": expected_config(case["problem"], variant),
                "environment": "self_built_fixed_scenario", "source_total": len(case["sources"])}
    for field, value in expected.items():
        if result.get(field) != value:
            issues.append(issue("result_frozen_design_mismatch", field))
    for field in ("walk_distance_m", "switches", "measures", "clear_attempts", "clear_successes"):
        claimed = result.get(field)
        if not number(claimed) or abs(claimed - replay["counts"][field]) > (1e-6 if field == "walk_distance_m" else 0):
            issues.append(issue("result_action_count_mismatch", field))
    for field in ("total_virtual_time_s", "independently_accounted_time_s"):
        claimed = result.get(field)
        if not number(claimed) or abs(claimed - replay["computed_virtual_time_s"]) > replay["time_tolerance_at_end_s"]:
            issues.append(issue("result_cost_formula_mismatch", field))
    if result.get("total_virtual_time_s") != feedback["exact_rounded_virtual_time_s"]:
        issues.append(issue("result_exact_microsecond_time_mismatch"))
    truth_channels = feedback["truth_cleared_channels"]
    all_cleared = len(truth_channels) == len(case["sources"])
    env = result.get("environment_summary") or {}
    for field, value in {"source_count": len(case["sources"]), "cleared_count": len(truth_channels),
                         "cleared_channels": truth_channels, "all_cleared": all_cleared,
                         "current_channel": replay["state"]["current_channel"],
                         "accepted_actions": replay["counts"]["unique_accepted_actions"],
                         "virtual_time_s": feedback["exact_rounded_virtual_time_s"]}.items():
        if env.get(field) != value:
            issues.append(issue("environment_summary_mismatch", field))
    if point(env.get("position")) != tuple(replay["state"]["position"]):
        issues.append(issue("environment_final_position_mismatch"))
    stats = env.get("stats") or {}
    for field, source in {"walk_distance": "walk_distance_m", "switches": "switches", "measures": "measures",
                          "clear_attempts": "clear_attempts", "successes": "clear_successes"}.items():
        if not number(stats.get(field)) or abs(stats[field] - replay["counts"][source]) > (1e-6 if field == "walk_distance" else 0):
            issues.append(issue("environment_stats_mismatch", field))
    if stats.get("failures") != replay["counts"]["clear_attempts"] - replay["counts"]["clear_successes"]:
        issues.append(issue("environment_failures_mismatch"))
    complete = bool(result.get("status") == "complete" and all_cleared and not result.get("error") and not result.get("hull_invariant_violations"))
    if result.get("evaluation_complete") != complete:
        issues.append(issue("evaluation_complete_mismatch"))
    if complete and (not replay["state"]["exited"] or replay["unresolved_last_request"]):
        issues.append(issue("complete_exit_unconfirmed"))
    expected_penalty = result.get("total_virtual_time_s") if complete else 360000.
    if result.get("failure_penalized_time_s") != expected_penalty:
        issues.append(issue("failure_penalty_mismatch"))
    if result.get("clear_fraction") != len(truth_channels) / len(case["sources"]):
        issues.append(issue("clear_fraction_mismatch"))
    with gzip.open(directory / "decisions.jsonl.gz", "rt", encoding="utf-8") as stream:
        start = json.loads(stream.readline())
    if start.get("event") != "start" or start.get("config") != expected["config"]:
        issues.append(issue("decision_start_frozen_config_mismatch"))
    return {"case_id": case["case_id"], "variant": variant, "problem": case["problem"], "pool": case["pool"],
            "partition": case["partition"], "composition_model": case["composition_model"], "mechanism_id": case["mechanism_id"],
            "scenario_sha256": case["scenario_sha256"], "sources_sha256": digest(case["sources"]),
            "error_field_sha256": digest(case["error_field"]), "policy_core_sha256": result.get("policy_core_sha256"),
            "runner_sha256": result.get("runner_sha256"), "result_content_sha256": digest(result),
            "total_virtual_time_s": result.get("total_virtual_time_s"),
            "failure_penalized_time_s": result.get("failure_penalized_time_s"),
            "reported_hull_invariant_violations": len(result.get("hull_invariant_violations", [])),
            "audit_status": "issues_found" if issues else "passed",
            "issues": issues, "evaluation_complete": complete, "n_request_rows": len(requests),
            **replay["counts"], **feedback["counts"], "computed_virtual_time_s": replay["computed_virtual_time_s"],
            "max_response_time_error_s": replay["max_response_time_error_s"],
            "max_state_time_error_s": replay["max_state_time_error_s"],
            "source_files_sha256": {name: sha(directory / name) for name in ("result.json", "requests.jsonl.gz", "decisions.jsonl.gz")}}


def check_development_reports(directory, audited):
    """Verify selection inputs and point estimates; bootstrap CIs are not replayed."""
    issues = []
    folder = directory / "development"
    names = ("results.json", "paired_cases.json", "paired_summary.json")
    if any(not (folder / name).is_file() for name in names):
        return {"issues": [issue("missing_development_summary_artifacts")]}
    rows, pairs, summaries = (load(folder / name) for name in names)
    index = {(r["case_id"], r["variant"]): r for r in audited}
    claimed = {(r["case_id"], r["variant"]): r for r in rows}
    if len(claimed) != len(rows) or set(claimed) != set(index):
        issues.append(issue("development_aggregate_case_or_arm_set_mismatch"))
    for key in set(index) & set(claimed):
        if digest(claimed[key]) != index[key]["result_content_sha256"]:
            issues.append(issue("development_aggregate_row_not_identical_to_audited_result", str(key)))
    wanted = {k for k in index if k[1] != BASELINE}
    seen = set()
    for row in pairs:
        key = row["case_id"], row["variant"]
        if key in seen or key not in wanted:
            issues.append(issue("development_paired_duplicate_or_unexpected", str(key)))
            continue
        seen.add(key)
        candidate, base = index[key], index[row["case_id"], BASELINE]
        both = candidate["evaluation_complete"] and base["evaluation_complete"]
        expected = {"partition": "development", "baseline_variant": BASELINE,
                    "baseline_complete": base["evaluation_complete"], "candidate_complete": candidate["evaluation_complete"],
                    "both_complete": both, "completion_delta": int(candidate["evaluation_complete"]) - int(base["evaluation_complete"]),
                    "penalized_time_delta_s": candidate["failure_penalized_time_s"] - base["failure_penalized_time_s"],
                    "completed_time_delta_s": candidate["total_virtual_time_s"] - base["total_virtual_time_s"] if both else None,
                    "completed_reduction_pct": 100 * (base["total_virtual_time_s"] - candidate["total_virtual_time_s"]) / base["total_virtual_time_s"] if both else None,
                    "baseline_total_virtual_time_s": base["total_virtual_time_s"], "candidate_total_virtual_time_s": candidate["total_virtual_time_s"]}
        if any(row.get(k) != v for k, v in expected.items()):
            issues.append(issue("development_pair_delta_mismatch", str(key)))
    if seen != wanted:
        issues.append(issue("development_pair_set_incomplete"))
    summary_keys = set()
    for summary in summaries:
        key = tuple(summary.get(k) for k in ("pool", "problem", "composition_model", "mechanism_id", "partition", "noise_model", "variant"))
        if key in summary_keys:
            issues.append(issue("duplicate_development_summary_group", str(key)))
        summary_keys.add(key)
        group = [r for r in pairs if r["pool"] == summary["pool"] and r["problem"] == summary["problem"]
                 and r["composition_model"] == summary["composition_model"] and r["variant"] == summary["variant"]
                 and (summary["mechanism_id"] == "__design_mixture__" or r["mechanism_id"] == summary["mechanism_id"])
                 and (summary["partition"] == "all" or r["partition"] == summary["partition"])
                 and (summary["noise_model"] == "all" or r["error_mode"] == summary["noise_model"])]
        if not group:
            issues.append(issue("empty_development_summary_group", str(key)))
            continue
        differences = [r["completed_time_delta_s"] for r in group if r["both_complete"]]
        expected = {"n_pairs": len(group), "baseline_failures": sum(not r["baseline_complete"] for r in group),
                    "candidate_failures": sum(not r["candidate_complete"] for r in group),
                    "completion_delta_mean": statistics.fmean(r["completion_delta"] for r in group),
                    "penalized_time_delta_mean_s": statistics.fmean(r["penalized_time_delta_s"] for r in group),
                    "both_complete_n": len(differences),
                    "completed_time_delta_mean_s": statistics.fmean(differences) if differences else None,
                    "completed_time_delta_median_s": statistics.median(differences) if differences else None,
                    "completed_regression_rate": sum(v > 0 for v in differences) / len(differences) if differences else None,
                    "completed_worst_regression_s": max(differences) if differences else None,
                    "mean_completed_reduction_pct": statistics.fmean(r["completed_reduction_pct"] for r in group if r["both_complete"]) if differences else None}
        for field, value in expected.items():
            observed = summary.get(field)
            if (value is None and observed is not None) or (value is not None and (not number(observed) or abs(observed - value) > 1e-8)):
                issues.append(issue("development_summary_count_or_point_estimate_mismatch", f"{key}:{field}"))
    expected_summary_keys = {(r["pool"], r["problem"], r["composition_model"], mechanism, partition, noise, r["variant"])
                             for r in pairs for mechanism in ("__design_mixture__", r["mechanism_id"])
                             for partition in ("all", r["partition"]) for noise in ("all", r["error_mode"])}
    if summary_keys != expected_summary_keys:
        issues.append(issue("development_summary_group_set_mismatch"))
    return {"issues": issues, "result_rows_checked": len(rows), "paired_rows_checked": len(pairs),
            "summary_groups_checked": len(summaries), "bootstrap_intervals_recomputed": False,
            "file_sha256": {name: sha(folder / name) for name in names}}


def audit_stage(directory, output, *, project=None, complete_only=False, partition="development", selection=None, check_reports=False):
    directory, output = Path(directory).resolve(), Path(output).resolve()
    project = Path(project).resolve() if project else Path(__file__).resolve().parents[1]
    manifest = load(directory / "manifest.json")
    freeze = check_freeze(directory, project)
    issues = freeze["issues"] + check_manifest(manifest)
    choice = None
    if partition not in ("development", "confirmation"):
        raise ValueError("Only explicit development/confirmation audit supported")
    if partition == "confirmation":
        if selection is None:
            raise ValueError("Confirmation requires the previously frozen selection file")
        selection = Path(selection).resolve()
        choice = load(selection)
        clone = dict(choice)
        expected = clone.pop("selection_sha256", None)
        if (digest(clone) != expected or choice.get("manifest_sha256") != manifest["manifest_sha256"]
                or not choice.get("frozen_before_confirmation_review") or choice.get("selection_basis") != "development_only"):
            raise ValueError("Invalid or unfrozen confirmation selection")
        if sha(directory / "development/results.json") != choice.get("development_file_sha256"):
            raise ValueError("Selected development input has changed")
        for problem in ("3", "4"):
            if choice.get("selected_variants", {}).get(problem) not in LIMITS:
                raise ValueError("Invalid selected variant")
    # Filter BEFORE touching any run artifact. Only selected confirmation arms
    # become accessible after the explicit selection-freeze prerequisite.
    selected = [c for c in manifest["scenarios"] if c["partition"] == partition]
    rows, pending = [], []
    for index, case in enumerate(selected):
        variants = LIMITS if partition == "development" else dict.fromkeys([BASELINE, choice["selected_variants"][str(case["problem"])]])
        paths = {arm: directory / "runs" / case["pool"] / case["case_id"] / arm for arm in variants}
        missing = [f"{arm}/{name}" for arm, path in paths.items() for name in ("result.json", "requests.jsonl.gz", "decisions.jsonl.gz") if not (path / name).is_file()]
        if missing:
            pending.append({"case_id": case["case_id"], "missing": missing})
            if not complete_only:
                issues.append(issue("missing_selected_case_artifacts", case["case_id"]))
            continue
        arms = [audit_arm(case, arm, path, freeze.get("runner_sha256")) for arm, path in paths.items()]
        for field in ("scenario_sha256", "sources_sha256", "error_field_sha256", "policy_core_sha256", "runner_sha256"):
            if len({r[field] for r in arms}) != 1:
                issues.append(issue("paired_world_or_code_hash_mismatch", f"{case['case_id']}:{field}"))
        rows.extend(arms)
        if (index + 1) % 50 == 0:
            print(json.dumps({"development_cases_examined": index + 1, "audited_runs": len(rows)}, ensure_ascii=False), flush=True)
    reports = check_development_reports(directory, rows) if check_reports and partition == "development" and not pending else None
    if reports:
        issues.extend(reports["issues"])
    issue_rows = [{"case_id": "__stage__", **r} for r in issues] + [
        {"case_id": row["case_id"], "variant": row["variant"], **r} for row in rows for r in row["issues"]]
    totals = ["n_request_rows", "unique_accepted_actions", "measures", "clear_attempts", "clear_successes",
              "fixed_error_angles_checked", "measure_feedbacks_checked", "clear_feedbacks_checked"]
    overall = {"schema_version": 1, "audit_version": "local-stage1-development-independent-v1",
               "audit_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "input": str(directory),
               "status": "issues_found" if issue_rows else f"no_complete_{partition}_cases" if not rows else "passed_complete_prefix" if pending else f"passed_full_{partition}",
               "manifest_sha256": manifest.get("manifest_sha256"), "development_only": partition == "development", "partition": partition,
               "confirmation_result_paths_opened": len(rows) if partition == "confirmation" else 0,
               "unselected_confirmation_result_paths_opened": 0,
               "confirmation_scenarios_not_audited": sum(c["partition"] == "confirmation" for c in manifest["scenarios"]) if partition == "development" else len(pending),
               "planned_selected_cases": len(selected), "audited_selected_cases": len({r["case_id"] for r in rows}),
               "audited_selected_runs": len(rows),
               "planned_development_cases": len(selected) if partition == "development" else 0,
               "audited_development_cases": len(rows) // 4 if partition == "development" else 0,
               "audited_development_runs": len(rows) if partition == "development" else 0, "pending_case_count": len(pending),
               "evaluation_complete_runs": sum(r["evaluation_complete"] for r in rows),
               "issue_count": len(issue_rows), "issue_counts": dict(Counter(r["code"] for r in issue_rows)),
               "totals": {k: sum(r.get(k, 0) for r in rows) for k in totals},
               "max_response_time_error_s": max((r["max_response_time_error_s"] for r in rows), default=0),
               "max_state_time_error_s": max((r["max_state_time_error_s"] for r in rows), default=0),
               "freeze": freeze, "pending_cases": pending, "development_report_checks": reports,
               "selection_sha256": sha(selection) if selection else None,
               "scope": f"Local manifest/source archive plus {partition} requests, results and decision start only. Confirmation access limited to explicitly frozen arms. No official calls. Every unique accepted local feedback independently checked against fixed source/error definitions; decisions beyond start not independently geometrically replayed.",
               "audit_script_sha256": sha(Path(__file__)), "request_replay_script_sha256": sha(Path(replay_module.__file__))}
    output.mkdir(parents=True, exist_ok=True)
    for name, value in (("overall.json", overall), ("cases.json", rows), ("issues.json", issue_rows)):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    replay_module.write_csv(output / "cases.csv", [{k: v for k, v in r.items() if k not in ("issues", "source_files_sha256")} for r in rows])
    replay_module.write_csv(output / "issues.csv", issue_rows)
    return overall


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--complete-only", action="store_true")
    parser.add_argument("--partition", choices=("development", "confirmation"), default="development")
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--check-reports", action="store_true")
    args = parser.parse_args(argv)
    result = audit_stage(args.input, args.output, project=args.project, complete_only=args.complete_only,
                         partition=args.partition, selection=args.selection, check_reports=args.check_reports)
    print(json.dumps({k: v for k, v in result.items() if k not in ("freeze", "pending_cases")}, ensure_ascii=False, indent=2))
    return 1 if result["issue_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
