"""Whole-case calibration from public, accepted feedback and post-exit counts.

This module performs no network/UI operations and never reads case.json hidden
coordinates. Measurements are observable proxies, not samples of true error.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import csv
import hashlib
import json
import math
import random
import statistics

SCHEMA_VERSION = 1
SPLIT_SALT = "cumcm-b-calibration-v1"
OUTCOMES = ("direction", "near", "no_signal")
CONDITION_KEYS = ("problem", "protocol", "phase", "station_id", "existence_before",
                  "cleared_before", "existence_post")


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def assign_split(case_key, *, salt=SPLIT_SALT, fit_fraction=.75):
    """Call before collecting a case; all its action rows inherit this split."""
    if not 0 < fit_fraction < 1:
        raise ValueError("fit_fraction must lie strictly between 0 and 1")
    digest = hashlib.sha256(f"{salt}:{case_key}".encode()).hexdigest()
    return {"case_key": str(case_key), "split_hash": digest,
            "split": "fit" if int(digest[:16], 16) / 2**64 < fit_fraction else "development",
            "split_salt": salt, "fit_fraction": fit_fraction}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _first(*values):
    return next((v for v in values if v is not None), None)


def _read_json(path, issues):
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("expected an object")
        return value
    except (ValueError, OSError) as exc:
        issues.append(f"{path.name}: {type(exc).__name__}")
        return {}


def _read_lines(path, issues):
    if not path.exists():
        return []
    values = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        issues.append(f"{path.name}: {type(exc).__name__}")
        return values
    for index, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("expected an object")
            values.append(value)
        except ValueError:
            issues.append(f"{path.name}:{index}: invalid JSON object")
    return values


def unique_accepted_actions(request_rows, issues=None):
    """Yield (original_row_index, row), applying each accepted ID at most once.

    A conflict keeps the first confirmed action and records an audit issue.
    Rejected/timeout rows never consume an ID. Missing IDs cannot be proven
    unique and are retained with an issue. No transport operation occurs.
    """
    issues = issues if issues is not None else []
    seen = {}
    for sequence, row in enumerate(request_rows):
        if (row.get("response") or {}).get("accepted") is not True:
            continue
        body = row.get("request") or {}
        request_id = body.get("request_id")
        fingerprint = canonical_hash([row.get("path"), body])
        if request_id is not None and request_id in seen:
            if seen[request_id] != fingerprint:
                issues.append("accepted request ID reused for different action")
            continue
        if request_id is None:
            issues.append("accepted action missing request_id; cannot verify idempotent deduplication")
        else:
            seen[request_id] = fingerprint
        yield sequence, row


def _position(value):
    if isinstance(value, dict):
        value = [value.get("x"), value.get("y")]
    if isinstance(value, (tuple, list)) and len(value) == 2 and all(_number(x) for x in value):
        return tuple(float(x) for x in value)
    return None


def _point_key(position):
    return tuple(round(v, 9) for v in position) if position else None


def _match_key(channel, position, virtual):
    return (channel, _point_key(position), round(virtual, 6) if _number(virtual) else None)


def _angle_delta(a, b):
    return (a - b + 180) % 360 - 180


def _area(points):
    try:
        points = [_position(p) for p in points]
        if len(points) < 3 or any(p is None for p in points):
            return 0. if points and all(p is not None for p in points) else None
        return abs(sum(a[0] * b[1] - a[1] * b[0] for a, b in zip(points, points[1:] + points[:1]))) / 2
    except TypeError:
        return None


def discover_cases(roots):
    """Retain incomplete/partially written case directories as audit rows."""
    paths = set()
    for root in map(Path, roots):
        if not root.exists():
            raise FileNotFoundError(root)
        for filename in ("requests.jsonl", "assignment.json", "post_exit_audit.json", "result.json"):
            paths.update(p.parent.resolve() for p in root.rglob(filename))
            if root.is_dir() and (root / filename).exists():
                paths.add(root.resolve())
    return sorted(paths)


def extract_case(directory, *, salt=SPLIT_SALT, fit_fraction=.75):
    directory = Path(directory)
    issues = []
    result = _read_json(directory / "result.json", issues)
    audit = _read_json(directory / "post_exit_audit.json", issues)
    assignment = _read_json(directory / "assignment.json", issues)
    requests = _read_lines(directory / "requests.jsonl", issues)
    decisions = _read_lines(directory / "decisions.jsonl", issues)
    config = result.get("config") or {}
    problem = _first(assignment.get("problem"), result.get("problem"), audit.get("problem"), config.get("problem"))
    if problem not in (3, 4):
        issues.append("problem unknown or invalid")
        problem = None
    environment = _first(assignment.get("environment"), result.get("environment"), audit.get("environment"), "unknown")
    source_kind = ("official" if str(environment).startswith("official") else
                   "local" if environment in ("self_built", "local", "local_calibration", "self_built_fixed_scenario") else "unknown")
    case_code = _first(audit.get("case_code"), result.get("case_code"))
    first_enter = next((r for r in requests if r.get("path") == "/enter"), {})
    opaque_enter = canonical_hash(first_enter.get("request", {})) if first_enter else None
    declared_key = _first(assignment.get("case_key"), assignment.get("assignment_id"), assignment.get("case_id"))
    split_key = str(_first(declared_key, case_code, result.get("case_id"), opaque_enter, str(directory)))
    split_info = assign_split(split_key, salt=salt, fit_fraction=fit_fraction)
    declared_split = assignment.get("split")
    if declared_split in ("fit", "development"):
        split_info["split"] = declared_split
        split_info["split_origin"] = "predeclared_assignment"
        split_info["split_hash"] = _first(assignment.get("split_hash"), assignment.get("allocation_hash"), split_info["split_hash"])
        split_info["declared_hash_method"] = assignment.get("split_method", "predeclared external allocation; may use within-protocol hash rank")
    elif declared_split is not None:
        issues.append("invalid declared split; excluded from fitting")
        split_info.update(split="invalid", split_origin="invalid_assignment")
    else:
        split_info["split_origin"] = "retrospective_whole_case_hash_exploration_only"
    # Analysis identity distinguishes model/protocol instances. Common local
    # seeds are a separate resampling cluster, never a reason to overwrite an
    # instance produced by a different mechanism.
    identity = str(_first(case_code, assignment.get("scenario_id"), result.get("case_id"), declared_key, opaque_enter, str(directory)))
    model_candidate = _first(assignment.get("model_candidate"), result.get("model_candidate"), assignment.get("model_id"), "unspecified")
    protocol = _first(assignment.get("protocol"), result.get("protocol"))
    if protocol is None:
        protocol = "legacy_adaptive_" + canonical_hash(config)[:10] if config else "unknown"
    if not isinstance(protocol, str):
        protocol = json.dumps(protocol, sort_keys=True, ensure_ascii=False)
    group_id = canonical_hash([source_kind, problem, identity,
                               model_candidate if source_kind == "local" else None,
                               protocol if source_kind == "local" else None])
    common_case_id = str(_first(assignment.get("common_case_id"), identity))
    resampling_group_id = canonical_hash([source_kind, problem, common_case_id])
    n = _first(audit.get("N"), audit.get("source_total_post_exit"), audit.get("source_total"),
               audit.get("n"), result.get("source_total"))
    ndir = _first(audit.get("Ndir"), audit.get("directional_total_post_exit"), audit.get("n_directed"))
    composition_source = "post_exit_audit" if audit else "result_aggregate"
    if n is not None and (not _number(n) or int(n) != n or not 10 <= n <= 16):
        issues.append("N outside the declared legal support; excluded from composition fit")
        n = None
    n = int(n) if n is not None else None
    if problem == 3 and ndir is None:
        ndir = 0
        composition_source += "+Q3_omnidirectional_fact"
    if ndir is not None and (not _number(ndir) or int(ndir) != ndir or ndir < 0 or ndir > 16 or
                            (n is not None and ndir > n) or (problem == 3 and ndir != 0)):
        issues.append("Ndir invalid; excluded from joint composition fit")
        ndir = None
    ndir = int(ndir) if ndir is not None else None
    decision_by_id, decision_by_key, coverage_points = {}, {}, {}
    planned_coverage_points = []
    for d in decisions:
        if d.get("event") == "start":
            planned_coverage_points = [p for raw in d.get("coverage_points", []) if (p := _position(raw)) is not None]
            coverage_points.update({_point_key(p): str(i) for i, p in enumerate(planned_coverage_points)})
        if d.get("event") != "measure":
            continue
        if d.get("request_id"):
            decision_by_id[d["request_id"]] = d
        key = _match_key(d.get("channel", (d.get("knowledge") or {}).get("channel")),
                         _position(d.get("position")), d.get("virtual_time_s", (d.get("response") or {}).get("virtual_time_s")))
        decision_by_key[key] = d
    measurements, proxies, contractions, constraints = [], [], [], []
    cleared, known_exists = set(), set()
    previous_measures, previous_area = defaultdict(list), {}
    entered = exited = False
    accepted_count = 0
    rejected_count = sum((r.get("response") or {}).get("accepted") is not True for r in requests)
    unique_actions = list(unique_accepted_actions(requests, issues))
    duplicate_count = len(requests) - rejected_count - len(unique_actions)
    for sequence, request in unique_actions:
        response, body = request.get("response") or {}, request.get("request") or {}
        path, request_id = request.get("path"), body.get("request_id")
        accepted_count += 1
        if path == "/enter":
            entered = True
        elif path == "/exit":
            exited = True
        elif path in ("/measure", "/clear"):
            channel, position = body.get("channel"), _position(body.get("position"))
            if not isinstance(channel, int) or isinstance(channel, bool) or not 1 <= channel <= 20 or position is None:
                issues.append("accepted action has invalid public coordinates/channel")
                continue
            is_cleared = channel in cleared
            if path == "/clear":
                clear_result = response.get("clear_result")
                if clear_result not in ("success", "no_target_in_range"):
                    issues.append("unknown clear_result")
                    continue
                constraints.append({"channel": channel, "kind": clear_result, "position": position,
                                    "cleared_before": is_cleared, "radius_m": 20,
                                    "meaning": "success constrains original source to a 20m disk; center is not true source"})
                if clear_result == "success":
                    cleared.add(channel)
                    known_exists.add(channel)
                continue
            outcome = response.get("measure_result")
            if outcome not in OUTCOMES or (outcome == "direction" and not _number(response.get("svd_deg"))):
                issues.append("unknown/invalid accepted measurement feedback")
                continue
            virtual = response.get("virtual_time_s")
            decision = decision_by_id.get(request_id) or decision_by_key.get(_match_key(channel, position, virtual)) or {}
            phase = decision.get("phase") or ("coverage" if decision.get("reason") == "global_coverage" else
                                               "adaptive_local" if decision.get("reason") else "unknown")
            station = decision.get("station_id")
            if station is None and phase == "coverage":
                station = coverage_points.get(_point_key(position))
            station = str(station) if station is not None else "xy:" + ",".join(f"{v:.9f}" for v in position)
            row = {"case_group_id": group_id, "split": split_info["split"], "source_kind": source_kind,
                   "problem": problem, "protocol": protocol, "sequence": sequence,
                   "request_id_hash": canonical_hash(request_id) if request_id else None,
                   "phase": phase, "station_id": station, "x": position[0], "y": position[1],
                   "channel": channel, "outcome": outcome, "visible": outcome in ("direction", "near"),
                   "svd_deg": response.get("svd_deg") if outcome == "direction" else None,
                   "existence_before": "known_existing" if channel in known_exists else "unknown",
                   "cleared_before": is_cleared, "any_cleared_before": bool(cleared), "virtual_time_s": virtual,
                   "decision_matched": bool(decision), "measurement_design": decision.get("measurement_design")}
            history = [p for p in previous_measures[channel] if p["cleared_before"] == is_cleared]
            if history:
                prior_same = next((p for p in reversed(history) if (p["x"], p["y"]) == position), None)
                others = [p for p in history if (p["x"], p["y"]) != position]
                nearest = min(others, key=lambda p: math.hypot(p["x"] - position[0], p["y"] - position[1])) if others else None
                for kind, prior in (("same_point_repeat", prior_same), ("nearest_observed_point", nearest)):
                    if prior is None:
                        continue
                    separation = math.hypot(prior["x"] - position[0], prior["y"] - position[1])
                    if kind == "nearest_observed_point" and separation > 50:
                        continue
                    proxies.append({"case_group_id": group_id, "split": split_info["split"], "problem": problem,
                                    "protocol": protocol, "phase": phase, "channel": channel, "kind": kind,
                                    "sequence": sequence, "previous_sequence": prior["sequence"],
                                    "separation_m": separation, "previous_outcome": prior["outcome"], "outcome": outcome,
                                    "reported_angle_delta_deg": _angle_delta(row["svd_deg"], prior["svd_deg"])
                                    if row["svd_deg"] is not None and prior["svd_deg"] is not None else None,
                                    "interpretation": "observable report relation; NOT true sensor error"})
            knowledge = decision.get("knowledge") or {}
            area = _area(knowledge.get("hull"))
            if area is not None:
                old_area = previous_area.get(channel)
                contractions.append({"case_group_id": group_id, "split": split_info["split"], "problem": problem,
                                     "protocol": protocol, "phase": phase, "channel": channel, "sequence": sequence,
                                     "outcome": outcome, "prior_hull_area_m2": old_area, "hull_area_m2": area,
                                     "area_ratio": area / old_area if old_area and old_area > 0 else None,
                                     "interpretation": "algorithmic feasible-set proxy, not true localization error"})
                previous_area[channel] = area
            constraints.append({"channel": channel, "kind": outcome, "position": position,
                                "reported_deg": row["svd_deg"], "cleared_before": is_cleared})
            measurements.append(row)
            previous_measures[channel].append(row)
            if row["visible"] and not is_cleared:
                known_exists.add(channel)
    absent = set((result.get("stop_evidence") or {}).get("absent_channels", [])) if result.get("status") == "complete" else set()
    for row in measurements:
        row["existence_post"] = ("confirmed_existing" if row["channel"] in known_exists else
                                  "certified_absent" if row["channel"] in absent else "unknown")
    file_hashes = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                   for name in ("assignment.json", "result.json", "post_exit_audit.json", "requests.jsonl", "decisions.jsonl")
                   if (directory / name).exists()}
    case = {"directory": str(directory.resolve()), "case_group_id": group_id, "case_instance_id": group_id,
            "resampling_group_id": resampling_group_id, "common_case_id": common_case_id, "case_code": case_code,
            "source_kind": source_kind, "environment": environment, "problem": problem, "protocol": protocol,
            "survey_seed": assignment.get("survey_seed"), **split_info,
            "model_candidate": model_candidate,
            "composition_model": _first(assignment.get("composition_model"), result.get("composition_model")),
            "radius_mode": _first(assignment.get("radius_mode"), result.get("radius_mode")),
            "error_mode": _first(assignment.get("error_mode"), result.get("error_mode")),
            "correlation_length": _first(assignment.get("correlation_length"), assignment.get("correlation_length_m"),
                                         result.get("correlation_length"), result.get("correlation_length_m")),
            "attempt_started": bool(assignment.get("attempt_started") or requests or result or audit),
            "planned_coverage_points": planned_coverage_points, "planned_coverage_count": len(planned_coverage_points),
            "confirmed_existing_channels": sorted(known_exists), "certified_absent_channels": sorted(absent),
            "N": n, "Ndir": ndir, "composition_source": composition_source,
            "status": result.get("status", "unknown"), "error": result.get("error"),
            "completed": result.get("status") == "complete", "enter_confirmed": entered, "exit_confirmed": exited,
            "accepted_actions": accepted_count, "unaccepted_attempts": rejected_count, "duplicate_responses": duplicate_count,
            "accepted_measures": len(measurements), "clear_successes": result.get("clear_successes", len(cleared)),
            "total_virtual_time_s": result.get("total_virtual_time_s"),
            "program_real_time_s": result.get("program_real_time_s"), "walk_distance_m": result.get("walk_distance_m"),
            "measures": result.get("measures"), "switches": result.get("switches"),
            "clear_attempts": result.get("clear_attempts"), "average_clear_time_s": result.get("average_clear_time_s"),
            "issues": issues, "file_sha256": file_hashes}
    return {"case": case, "measurements": measurements, "proxies": proxies, "contractions": contractions,
            "constraints": {"case_group_id": group_id, "problem": problem, "epsilon_deg": config.get("epsilon_deg"),
                            "shared_latent_parameters": "per source: one g, one R in [1000,1500], one fixed type and u",
                            "clauses": constraints}}


def visibility_matrices(measurements):
    groups = defaultdict(list)
    for row in measurements:
        key = tuple(row[k] for k in ("case_group_id", "problem", "protocol", "phase", "station_id", "x", "y"))
        groups[key].append(row)
    result = []
    for key, rows in groups.items():
        cells = [[] for _ in range(20)]
        for row in rows:
            cells[row["channel"] - 1].append({"outcome": row["outcome"], "cleared_before": row["cleared_before"],
                                               "sequence": row["sequence"], "existence_before": row["existence_before"],
                                               "any_cleared_before": row.get("any_cleared_before", row["cleared_before"])})
        mask = [bool(cell) for cell in cells]
        first = [cell[0]["outcome"] if cell else None for cell in cells]
        result.append({**dict(zip(("case_group_id", "problem", "protocol", "phase", "station_id", "x", "y"), key)),
                       "channels": list(range(1, 21)), "observed_mask": mask, "first_outcomes": first,
                       "all_feedback": cells, "all_20_observed": all(mask),
                       "all_20_before_any_clear": all(mask) and all(not cell[0]["any_cleared_before"] for cell in cells)})
    return result


def channel_visibility_patterns(cases, measurements, *, adjacent_pair_count=6):
    """Joint same-channel patterns over the recorded, frozen coverage design.

    Unobserved cells remain None. Only complete, pre-clear, all-channel designs
    contribute joint summary probabilities. Source existence is an offline
    evidence stratum, not hidden truth supplied to a solver. The pair selection
    rule is fixed by geometry, never by observed responses.
    """
    per_case = defaultdict(list)
    for row in measurements:
        per_case[row["case_group_id"]].append(row)
    patterns, summaries, pairs = [], [], []
    unique = {r["case_group_id"]: r for r in cases}
    for identity, case in unique.items():
        points = [_point_key(_position(p)) for p in case.get("planned_coverage_points", [])]
        if not points or any(p is None for p in points) or len(set(points)) != len(points):
            continue
        design_hash = canonical_hash(points)[:16]
        cells = [[None for _ in points] for _ in range(20)]
        lookup = {p: i for i, p in enumerate(points)}
        for row in sorted(per_case[identity], key=lambda r: r["sequence"]):
            point = _point_key((row["x"], row["y"]))
            if row["phase"] != "coverage" or row.get("any_cleared_before", row["cleared_before"]) or point not in lookup:
                continue
            channel, station = row["channel"] - 1, lookup[point]
            if cells[channel][station] is None:
                cells[channel][station] = row["visible"]
        complete_design = all(all(v is not None for v in pattern) for pattern in cells)
        existing = set(case.get("confirmed_existing_channels", []))
        absent = set(case.get("certified_absent_channels", []))
        existing_patterns = [cells[ch - 1] for ch in sorted(existing) if 1 <= ch <= 20]
        summary = {"case_group_id": identity, "problem": case["problem"], "protocol": case["protocol"],
                   "split": case["split"], "source_kind": case["source_kind"], "design_hash": design_hash,
                   "station_positions": points, "n_planned_stations": len(points),
                   "complete_all_channel_preclear_design": complete_design,
                   "n_confirmed_existing_channels": len(existing_patterns),
                   "confirmed_count_equals_post_exit_N": len(existing_patterns) == case.get("N") if case.get("N") is not None else None,
                   "visible_station_count_distribution": None, "metrics": {},
                   "pair_selection_rule": f"first {adjacent_pair_count} shortest undirected geometric pairs; distance then route indices; fixed without feedback",
                   "interpretation": "case-level probabilities among offline confirmed-existing channels, not independent source samples"}
        for channel, values in enumerate(cells, 1):
            patterns.append({"case_group_id": identity, "problem": case["problem"], "protocol": case["protocol"],
                             "split": case["split"], "source_kind": case["source_kind"], "design_hash": design_hash,
                             "channel": channel, "existence_post": "confirmed_existing" if channel in existing else
                             "certified_absent" if channel in absent else "unknown",
                             "station_indices": list(range(len(points))), "station_positions": points,
                             "visibility": values, "observed_mask": [v is not None for v in values],
                             "visible_station_count": sum(values) if all(v is not None for v in values) else None,
                             "complete_all_channel_preclear_design": complete_design})
        if complete_design and existing_patterns:
            counts = Counter(sum(values) for values in existing_patterns)
            distribution = [{"visible_stations": k, "channels": counts[k],
                             "probability": counts[k] / len(existing_patterns)} for k in range(len(points) + 1)]
            summary["visible_station_count_distribution"] = distribution
            prefix = "joint_" + design_hash + "_"
            for row in distribution:
                summary["metrics"][prefix + f"visible_count_probability_{row['visible_stations']:03d}"] = row["probability"]
            summary["metrics"][prefix + "mean_visible_station_fraction"] = statistics.mean(
                sum(values) / len(points) for values in existing_patterns)
            geometric_pairs = sorted((math.dist(points[i], points[j]), i, j) for i in range(len(points))
                                     for j in range(i + 1, len(points)))[:adjacent_pair_count]
            for distance, i, j in geometric_pairs:
                both = statistics.mean(float(v[i] and v[j]) for v in existing_patterns)
                discordant = statistics.mean(float(v[i] != v[j]) for v in existing_patterns)
                pair = {"case_group_id": identity, "problem": case["problem"], "protocol": case["protocol"],
                        "design_hash": design_hash, "station_indices": [i, j],
                        "station_positions": [points[i], points[j]], "separation_m": distance,
                        "confirmed_existing_channels": len(existing_patterns),
                        "both_visible_probability": both, "discordant_probability": discordant}
                pairs.append(pair)
                summary["metrics"][prefix + f"pair_{i:03d}_{j:03d}_both_visible_probability"] = both
                summary["metrics"][prefix + f"pair_{i:03d}_{j:03d}_discordant_probability"] = discordant
        summaries.append(summary)
    return {"channel_patterns": patterns, "case_summaries": summaries, "adjacent_pairs": pairs}


def joint_prior(problem):
    return {(n, d): 1 / 7 / (1 if problem == 3 else n + 1)
            for n in range(10, 17) for d in ([0] if problem == 3 else range(n + 1))}


def _distribution(mapping):
    return [{"n": n, "n_directed": d, "probability": probability}
            for (n, d), probability in sorted(mapping.items())]


def _quantile(values, q):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * q
    lo, hi = math.floor(position), math.ceil(position)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def bootstrap_mean(values, *, repeats=500, seed=0):
    values = [float(v) for v in values if _number(v)]
    mean = statistics.mean(values) if values else None
    if len(values) < 2 or repeats < 2:
        return {"n_cases": len(values), "mean": mean, "ci95": [None, None], "bootstrap_unit": "whole_case"}
    rng = random.Random(seed)
    boot = [statistics.mean(rng.choices(values, k=len(values))) for _ in range(repeats)]
    return {"n_cases": len(values), "mean": mean, "ci95": [_quantile(boot, .025), _quantile(boot, .975)],
            "bootstrap_unit": "whole_case"}


def fit_composition(cases, problem, *, prior_effective_cases=2., bootstrap_repeats=500, seed=0):
    if prior_effective_cases <= 0:
        raise ValueError("prior_effective_cases must be positive")
    selected = [r for r in cases if r["problem"] == problem and r["split"] == "fit" and
                r["source_kind"] == "official" and r.get("attempt_started", True) and r["N"] is not None and r["Ndir"] is not None]
    unique = {r["case_group_id"]: r for r in selected}
    pairs = [(r["N"], r["Ndir"]) for r in unique.values()]
    counts = Counter(pairs)
    n, prior = len(pairs), joint_prior(problem)
    empirical = {pair: count / n for pair, count in counts.items()} if n else {}
    smooth = {pair: (counts[pair] + prior_effective_cases * probability) / (n + prior_effective_cases)
              for pair, probability in prior.items()}
    rng, bootstrap = random.Random(seed), defaultdict(list)
    if n >= 2:
        for _ in range(bootstrap_repeats):
            replicate = Counter(rng.choices(pairs, k=n))
            for pair in counts:
                bootstrap[pair].append(replicate[pair] / n)
    intervals = [{"n": p[0], "n_directed": p[1], "empirical_probability": empirical[p],
                  "ci95": [_quantile(bootstrap[p], .025), _quantile(bootstrap[p], .975)]} for p in sorted(counts)]
    return {"count_distribution": _distribution(smooth), "n_fit_known_joint": n,
            "status": "fitted_composition_only" if n >= 30 else "exploratory_sparse_joint_counts",
            "composition_candidates": {
                "empirical_joint": {"count_distribution": _distribution(empirical), "status": "fit" if n else "unavailable"},
                "smoothed_joint": {"count_distribution": _distribution(smooth), "prior_effective_cases": prior_effective_cases,
                                   "status": "fit" if n else "prior_only"},
                "broad_joint": {"count_distribution": _distribution(prior), "status": "assumption"}},
            "empirical_cell_bootstrap": intervals, "bootstrap_unit": "whole_case",
            "fit_group_ids": sorted(unique), "unknown_joint_excluded_from_fit": sum(
                r["problem"] == problem and r["split"] == "fit" and r["source_kind"] == "official" and
                r.get("attempt_started", True) and (r["N"] is None or r["Ndir"] is None) for r in cases)}


def composition_predictive_checks(cases, models):
    result = []
    for problem in (3, 4):
        heldout = {r["case_group_id"]: r for r in cases if r["problem"] == problem and r["split"] == "development"
                   and r["source_kind"] == "official" and r["N"] is not None and r["Ndir"] is not None}
        for name, model in models[str(problem)]["composition_candidates"].items():
            distribution = {(r["n"], r["n_directed"]): r["probability"] for r in model["count_distribution"]}
            nll, brier, zeros = [], [], 0
            for row in heldout.values():
                p = distribution.get((row["N"], row["Ndir"]), 0.)
                if p > 0:
                    nll.append(-math.log(p))
                else:
                    zeros += 1
                brier.append(1 - 2 * p + sum(x * x for x in distribution.values()))
            result.append({"problem": problem, "candidate": name, "heldout_cases": len(heldout),
                           "zero_probability_observations": zeros,
                           "mean_joint_nll": statistics.mean(nll) if nll and not zeros else None,
                           "mean_joint_brier": statistics.mean(brier) if brier and distribution else None,
                           "status": "evaluated" if heldout and distribution else "insufficient_data",
                           "interpretation": "development cases did not enter fit; no automatic candidate selection"})
    return result


def conditional_feedback(measurements, *, repeats=300, seed=0):
    groups = defaultdict(list)
    for row in measurements:
        if row["source_kind"] == "official" and row["split"] == "fit":
            groups[tuple(row.get(k) for k in CONDITION_KEYS)].append(row)
    output = []
    rng = random.Random(seed)
    for key, rows in sorted(groups.items(), key=lambda item: str(item[0])):
        per_case = defaultdict(Counter)
        for row in rows:
            per_case[row["case_group_id"]][row["outcome"]] += 1
        count = Counter()
        for values in per_case.values():
            count.update(values)
        total = sum(count.values())
        boot = defaultdict(list)
        if len(per_case) >= 2:
            for _ in range(repeats):
                draw = Counter()
                for case in rng.choices(list(per_case.values()), k=len(per_case)):
                    draw.update(case)
                for outcome in OUTCOMES:
                    boot[outcome].append(draw[outcome] / sum(draw.values()))
        output.append({**dict(zip(CONDITION_KEYS, key)), "n_cases": len(per_case), "n_measurements": total,
                       "counts": dict(count), "probabilities": {o: count[o] / total for o in OUTCOMES},
                       "ci95": {o: [_quantile(boot[o], .025), _quantile(boot[o], .975)] for o in OUTCOMES},
                       "bootstrap_unit": "whole_case", "purpose": "protocol-conditioned observable checks only"})
    return output


def feedback_predictive_checks(measurements, conditionals):
    lookup = {tuple(r.get(k) for k in CONDITION_KEYS): r for r in conditionals}
    groups = defaultdict(list)
    for row in measurements:
        if row["split"] == "invalid":
            continue
        if row["split"] != "development" and row["source_kind"] != "local":
            continue
        reference = lookup.get(tuple(row.get(k) for k in CONDITION_KEYS))
        p = ((reference["counts"].get(row["outcome"], 0) + .5) /
             (reference["n_measurements"] + 1.5)) if reference else None
        groups[(row["source_kind"], row["problem"], row["protocol"], row["case_group_id"])].append(p)
    return [{"source_kind": k[0], "problem": k[1], "protocol": k[2], "case_group_id": k[3],
             "matched_measurements": sum(p is not None for p in values), "measurements": len(values),
             "mean_matched_feedback_nll": statistics.mean(-math.log(p) for p in values if p is not None)
             if any(p is not None for p in values) else None,
             "smoothing": "0.5 per observable category; not a fitted sensor noise model"} for k, values in groups.items()]


def cost_summaries(cases, *, repeats=500):
    groups = defaultdict(list)
    seen = set()
    for row in cases:
        if not row.get("attempt_started", True):
            continue
        identity = (row["case_group_id"], row["protocol"], row.get("model_candidate"))
        if identity in seen:
            continue
        seen.add(identity)
        groups[(row["source_kind"], row["problem"], row["protocol"], row["split"], row.get("model_candidate", "unspecified"))].append(row)
    summaries = []
    for key, rows in sorted(groups.items(), key=lambda item: str(item[0])):
        complete = [r for r in rows if r["completed"]]
        summaries.append({**dict(zip(("source_kind", "problem", "protocol", "split", "model_candidate"), key)),
                          "attempts": len(rows), "complete_cases": len(complete),
                          "incomplete_or_unknown": len(rows) - len(complete),
                          "completion_fraction": bootstrap_mean([int(r["completed"]) for r in rows], repeats=repeats),
                          "all_attempts_observed_virtual_s": bootstrap_mean([r["total_virtual_time_s"] for r in rows], repeats=repeats),
                          "complete_only_virtual_s": bootstrap_mean([r["total_virtual_time_s"] for r in complete], repeats=repeats),
                          "complete_only_average_clear_s": bootstrap_mean([r["average_clear_time_s"] for r in complete], repeats=repeats),
                          "warning": "incomplete time is censored effort, never a fast completion"})
    return summaries


def _compare_scalar(official, local, *, metric, repeats=300, seed=0, absolute_tolerance=.1):
    """Two-sample whole-case bootstrap, with an analysis-fixed practical margin.

    'matched' means the 95% interval is inside the stated margin, rather than
    merely failing to reject a difference. Sparse comparisons stay unresolved.
    """
    official = [float(x) for x in official if _number(x)]
    local = [float(x) for x in local if _number(x)]
    a = statistics.mean(official) if official else None
    b = statistics.mean(local) if local else None
    result = {"metric": metric, "n_official_development": len(official), "n_local": len(local),
              "official_mean": a, "local_mean": b, "difference_local_minus_official": b - a if a is not None and b is not None else None,
              "practical_margin": absolute_tolerance, "difference_ci95": [None, None],
              "status": "insufficient_evidence", "screen_flag": False, "bootstrap_unit": "whole_case"}
    if min(len(official), len(local)) < 5:
        return result
    rng = random.Random(seed)
    draws = [statistics.mean(rng.choices(local, k=len(local))) - statistics.mean(rng.choices(official, k=len(official)))
             for _ in range(repeats)]
    lo, hi = _quantile(draws, .025), _quantile(draws, .975)
    result["difference_ci95"] = [lo, hi]
    if lo is None or hi is None:
        return result
    if lo > absolute_tolerance or hi < -absolute_tolerance:
        result["status"] = "screen_flag"
        result["screen_flag"] = True
    elif lo >= -absolute_tolerance and hi <= absolute_tolerance:
        result["status"] = "matched"
    return result


def _joint_two_sample(official, local, *, repeats=300, seed=0):
    official = list({r.get("resampling_group_id", r["case_group_id"]): r for r in official}.values())
    local = list({r.get("resampling_group_id", r["case_group_id"]): r for r in local}.values())
    official = [(r["N"], r["Ndir"]) for r in official if r["N"] is not None and r["Ndir"] is not None]
    local = [(r["N"], r["Ndir"]) for r in local if r["N"] is not None and r["Ndir"] is not None]
    def tv(a, b):
        ca, cb = Counter(a), Counter(b)
        return .5 * sum(abs(ca[k] / len(a) - cb[k] / len(b)) for k in ca.keys() | cb.keys())
    result = {"metric": "joint_composition_total_variation", "n_official_development": len(official),
              "n_local": len(local), "observed_tv": tv(official, local) if official and local else None,
              "permutation_p": None, "bootstrap_tv_ci95": [None, None], "practical_margin": .25,
              "status": "insufficient_evidence", "resampling_unit": "whole_case_joint_pair"}
    if min(len(official), len(local)) < 5:
        return result
    rng, pool = random.Random(seed), official + local
    exceed = 0
    for _ in range(repeats):
        shuffled = rng.sample(pool, len(pool))
        exceed += tv(shuffled[:len(official)], shuffled[len(official):]) >= result["observed_tv"] - 1e-12
    result["permutation_p"] = (exceed + 1) / (repeats + 1)
    tv_draws = [tv(rng.choices(official, k=len(official)), rng.choices(local, k=len(local))) for _ in range(repeats)]
    result["bootstrap_tv_ci95"] = [_quantile(tv_draws, .025), _quantile(tv_draws, .975)]
    if result["observed_tv"] > .25 and result["permutation_p"] < .025:
        result["status"] = "screen_flag"
    elif result["bootstrap_tv_ci95"][1] is not None and result["bootstrap_tv_ci95"][1] <= .25:
        result["status"] = "matched"
    result["interpretation"] = "non-rejection is not equivalence; joint composition remains uncertain unless richer evidence is available"
    return result


def _metric_values(rows, metrics, name):
    """One case-cluster vote even if a local scenario has repeated instances."""
    groups = defaultdict(list)
    for row in rows:
        value = metrics[row["case_group_id"]].get(name)
        if _number(value):
            groups[row.get("resampling_group_id", row["case_group_id"])].append(value)
    return [statistics.mean(values) for values in groups.values()]


def _replicated_joint_screen(official, local, metrics, check, *, repeats=300):
    """Require the same core joint discrepancy in two fixed case-hash halves.

    This is an exploratory stability check, not family-wise error control or
    independent external replication. Empty/small halves never reject a model.
    """
    name = check["metric"]
    halves = []
    for half in (0, 1):
        def chosen(rows):
            return [r for r in rows if int(canonical_hash(
                ["mechanism-screen-half-v1", r.get("resampling_group_id", r["case_group_id"])])[:8], 16) % 2 == half]
        halves.append(_compare_scalar(_metric_values(chosen(official), metrics, name),
                                      _metric_values(chosen(local), metrics, name), metric=name, repeats=repeats,
                                      seed=half, absolute_tolerance=check["practical_margin"]))
    stable = all(h["status"] == "screen_flag" for h in halves) and all(
        h["difference_local_minus_official"] * check["difference_local_minus_official"] > 0 for h in halves)
    return {"metric": name, "stable_same_direction": stable, "hash_halves": halves,
            "interpretation": "exploratory split stability, not a formal rejection test"}


def mechanism_checks(cases, measurements, proxies, contractions, matrices, *, joint_visibility=None, repeats=300):
    """Compare genuine local protocol replications against official development.

    No latent parameter is fitted here. Each candidate is assessed by observable
    evidence; missing coverage/probes cannot become a default 'validated'.
    """
    unique_cases = {(r["case_group_id"], r["protocol"], r.get("model_candidate")): r
                    for r in cases if r.get("attempt_started", True) and r["split"] != "invalid"}
    cases = list(unique_cases.values())
    metrics = defaultdict(dict)
    for row in cases:
        destination = metrics[row["case_group_id"]]
        destination["completion_fraction"] = float(row["completed"])
        destination["N"] = row["N"]
        destination["Ndir"] = row["Ndir"]
        # Complete-only efficiency; failed effort is separately reported above.
        for field in ("total_virtual_time_s", "walk_distance_m", "measures", "clear_attempts"):
            destination["complete_only_" + field] = row.get(field) if row["completed"] else None
        if row["completed"] and _number(row.get("clear_attempts")) and _number(row.get("clear_successes")):
            destination["complete_only_failed_clears"] = row["clear_attempts"] - row["clear_successes"]
    grouped = defaultdict(lambda: defaultdict(list))
    for row in measurements:
        identity = row["case_group_id"]
        if row["phase"] == "survey" and not row["any_cleared_before"]:
            grouped[identity]["survey_extra_measurements"].append(1.)
            grouped[identity]["survey_no_signal_fraction"].append(float(row["outcome"] == "no_signal"))
            design = row.get("measurement_design") or {}
            # These are nominal distances/bearings relative to a frozen
            # estimated point. They must never be relabelled true g distances.
            distance = design.get("nominal_distance_m")
            bearing = design.get("nominal_bearing_deg")
            if _number(distance):
                distance_bin = int(distance // 250) * 250
                grouped[identity][f"survey_nominal_distance_{distance_bin}_visibility"].append(float(row["visible"]))
            if _number(bearing):
                bearing_bin = int((round(bearing, 6) % 360) // 90) * 90
                grouped[identity][f"survey_nominal_bearing_{bearing_bin}_visibility"].append(float(row["visible"]))
    for matrix in matrices:
        if matrix["phase"] != "coverage" or not matrix["all_20_before_any_clear"]:
            continue
        # Include coordinates in the metric key: equal labels at different
        # physical sites are not interchangeable designs.
        site = f"{matrix['station_id']}@{matrix['x']:.4f},{matrix['y']:.4f}"
        grouped[matrix["case_group_id"]]["coverage_station_" + site + "_visibility"].append(
            sum(x in ("direction", "near") for x in matrix["first_outcomes"]) / 20)
    for row in proxies:
        value = row.get("reported_angle_delta_deg")
        if _number(value):
            grouped[row["case_group_id"]][row["kind"] + "_absolute_report_delta_deg"].append(abs(value))
    for row in contractions:
        if _number(row.get("area_ratio")):
            grouped[row["case_group_id"]][row["phase"] + "_mean_hull_area_ratio"].append(row["area_ratio"])
    for identity, features in grouped.items():
        for name, values in features.items():
            metrics[identity][name] = sum(values) if name == "survey_extra_measurements" else statistics.mean(values)
    joint_visibility = joint_visibility or channel_visibility_patterns(cases, measurements)
    for summary in joint_visibility["case_summaries"]:
        metrics[summary["case_group_id"]].update(summary["metrics"])
    candidates = defaultdict(list)
    for row in cases:
        if row["source_kind"] == "local":
            candidates[(row.get("model_candidate", "unspecified"), row["problem"], row["protocol"])].append(row)
    reports = []
    for (candidate, problem, protocol), local in sorted(candidates.items(), key=lambda item: str(item[0])):
        official = [r for r in cases if r["source_kind"] == "official" and r["split"] == "development"
                    and r["problem"] == problem and r["protocol"] == protocol]
        names = set(k for r in official + local for k in metrics[r["case_group_id"]])
        checks = []
        for name in sorted(names):
            a = _metric_values(official, metrics, name)
            b = _metric_values(local, metrics, name)
            finite_a = [v for v in a if _number(v)]
            reference = statistics.mean(finite_a) if finite_a else 0
            margin = (1. if name in ("N", "Ndir") else .1 if any(s in name for s in ("fraction", "visibility", "ratio", "probability"))
                      else .5 if "delta_deg" in name else max(1., .25 * abs(reference)))
            checks.append(_compare_scalar(a, b, metric=name, repeats=repeats,
                                          seed=int(canonical_hash([candidate, problem, protocol, name])[:8], 16),
                                          absolute_tolerance=margin))
        composition = _joint_two_sample(official, local, repeats=repeats, seed=problem or 0)
        screened = [c["metric"] for c in checks + [composition] if c["status"] == "screen_flag"]
        matched = [c["metric"] for c in checks if c["status"] == "matched"]
        required = ["completion_fraction", "N", "Ndir", "complete_only_failed_clears", "complete_only_walk_distance_m"]
        if protocol == "survey":
            required += ["survey_extra_measurements", "survey_no_signal_fraction", "same_point_repeat_absolute_report_delta_deg"]
        joint_names = sorted(n for n in names if n.startswith("joint_"))
        has_joint_distribution = any("visible_count_probability_" in n for n in joint_names)
        has_joint_pairs = any("both_visible_probability" in n for n in joint_names) and any("discordant_probability" in n for n in joint_names)
        joint_gate = has_joint_distribution and has_joint_pairs and all(n in matched for n in joint_names)
        repeated = [_replicated_joint_screen(official, local, metrics, c, repeats=repeats)
                    for c in checks if c["status"] == "screen_flag" and c["metric"].startswith("joint_")]
        stable_bias = [r["metric"] for r in repeated if r["stable_same_direction"]]
        status = ("biased" if stable_bias else "matched" if not screened and joint_gate and composition["status"] == "matched" and
                  all(n in matched for n in required) else "insufficient_evidence")
        reports.append({"model_candidate": candidate, "problem": problem, "protocol": protocol,
                        "official_reference": "development_only", "n_official_development": len(official), "n_local": len(local),
                        "status": status, "screen_flag": bool(screened), "screen_flag_metrics": screened,
                        "biased_metrics": stable_bias, "joint_split_stability": repeated, "matched_metrics": matched,
                        "joint_visibility_evidence_gate": joint_gate, "required_joint_metrics": joint_names,
                        "required_observable_metrics": required, "joint_composition": composition, "metrics": checks,
                        "decision": "deprioritize this candidate for matched-protocol simulation" if status == "biased" else
                                    "retain as observably compatible, with unidentified mechanisms still varied" if status == "matched" else
                                    "retain uncertainty; no calibration approval from missing or weak evidence",
                        "limits": "single pointwise differences only screen; biased requires repeated core joint discrepancy and remains exploratory; shared local seeds correlate comparisons; matched is not recovered official g/R/u/noise"})
    return {"schema_version": 1, "candidate_checks": reports,
            "status": "awaiting_local_protocol_replications" if not reports else "observable_checks_only",
            "minimum_cases_each_side": 5,
            "bias_gate": "same core joint-visibility metric exceeds the margin in the same direction in each deterministic whole-case hash half, >=5 cases per side per half; exploratory only",
            "match_gate": "joint composition, all required costs/probes, complete joint visible-count distribution and fixed geometric pair metrics show practical equivalence; no screen flags",
            "practical_margins": "1 source for N/Ndir; 0.10 for feedback/visibility/area ratios; 0.5 deg report differences; max(1,25% official mean) for counts/distances/time",
            "warnings": ["do not equate independent composition fit with mechanism calibration",
                         "no official development observations enter composition fit",
                         "failure costs are censored and cannot win an efficiency comparison",
                         "multiple diagnostic metrics are not family-wise calibrated significance claims"]}


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def mechanism_handoff(cases, model_checks):
    """Explicit candidate-retention record; unresolved is never relabelled fit."""
    definitions = {r.get("model_candidate"): r for r in cases if r["source_kind"] == "local"}
    grouped = defaultdict(list)
    for check in model_checks["candidate_checks"]:
        grouped[check["model_candidate"]].append(check)
    rows = []
    for candidate, checks in sorted(grouped.items()):
        source = definitions.get(candidate, {})
        status = ("biased" if any(c["status"] == "biased" for c in checks) else "matched" if checks and
                  all(c["status"] == "matched" for c in checks) else "unresolved")
        rows.append({"id": candidate, "radius_mode": source.get("radius_mode"), "error_mode": source.get("error_mode"),
                     "correlation_length": source.get("correlation_length"), "status": status,
                     "screen_flag": any(c.get("screen_flag") for c in checks),
                     "evidence_groups": [{"problem": c["problem"], "protocol": c["protocol"], "status": c["status"],
                                          "n_official_development": c["n_official_development"], "n_local": c["n_local"]} for c in checks],
                     "retention_reason": "exploratory stable joint discrepancy; exclude from retained list pending review" if status == "biased" else
                                         "retain uncertainty; insufficient evidence is not validation" if status == "unresolved" else
                                         "observable compatibility only; latent mechanisms remain unidentified"})
        if rows[-1]["correlation_length"] is None:
            rows[-1].pop("correlation_length")
            rows[-1]["missing_parameters"] = ["correlation_length"]
            rows[-1]["parameter_note"] = "not logged; a downstream default remains a declared simulation assumption, not an estimate"
    return rows


def _write_jsonl(path, values):
    with path.open("w", encoding="utf-8") as stream:
        for row in values:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def _write_csv(path, rows):
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()})


def calibrate(roots, output, *, compare_roots=(), exploratory=False, salt=SPLIT_SALT,
              fit_fraction=.75, prior_effective_cases=2., bootstrap_repeats=500):
    if bootstrap_repeats < 2:
        raise ValueError("bootstrap_repeats must be at least 2")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    features = output / "features"
    features.mkdir(exist_ok=True)
    bundles = [extract_case(p, salt=salt, fit_fraction=fit_fraction) for p in discover_cases([*roots, *compare_roots])]
    cases = [b["case"] for b in bundles]
    # Reject split leakage across paired strategies or duplicate case copies.
    group_splits = defaultdict(set)
    group_compositions = defaultdict(set)
    for row in cases:
        group_splits[row["case_group_id"]].add(row["split"])
        if row["N"] is not None and row["Ndir"] is not None:
            group_compositions[row["case_group_id"]].add((row["N"], row["Ndir"]))
    for bundle in bundles:
        case = bundle["case"]
        if len(group_splits[case["case_group_id"]]) > 1 or len(group_compositions[case["case_group_id"]]) > 1:
            case["split"] = "invalid"
            case["issues"].append("same whole case has conflicting splits or composition; excluded from fit/check")
            for kind in ("measurements", "proxies", "contractions"):
                for row in bundle[kind]:
                    row["split"] = "invalid"
    measurements, seen_measurements = [], set()
    for bundle in bundles:
        for row in bundle["measurements"]:
            identity = (row["case_group_id"], row["request_id_hash"])
            if row["request_id_hash"] is not None and identity in seen_measurements:
                continue
            seen_measurements.add(identity)
            measurements.append(row)
    proxies = list({(row["case_group_id"], row["sequence"], row["kind"]): row
                    for b in bundles for row in b["proxies"]}.values())
    contractions = list({(row["case_group_id"], row["sequence"]): row
                         for b in bundles for row in b["contractions"]}.values())
    models = {str(problem): fit_composition(cases, problem, prior_effective_cases=prior_effective_cases,
                                           bootstrap_repeats=bootstrap_repeats, seed=problem) for problem in (3, 4)}
    official = list({r["case_group_id"]: r for r in cases if r["source_kind"] == "official" and r["attempt_started"]}.values())
    sparse = exploratory or any(models[str(p)]["n_fit_known_joint"] < 30 for p in (3, 4))
    model = {"schema_version": SCHEMA_VERSION,
             "model_id": "composition-" + canonical_hash(models)[:16],
             "model_status": "exploratory" if sparse else "fitted_composition_only",
             "provenance": {"n_official_attempts": len(official), "n_official_cases": len({r["case_group_id"] for r in official if r["enter_confirmed"]}),
                            "n_fit": sum(m["n_fit_known_joint"] for m in models.values()),
                            "roots": list(map(str, roots)), "compare_roots": list(map(str, compare_roots)),
                            "split_unit": "whole_case", "split_salt": salt, "fit_fraction": fit_fraction,
                            "retrospective_assignments": sum(r["split_origin"].startswith("retrospective") for r in cases),
                            "bootstrap_repeats": bootstrap_repeats},
             "problems": models,
             "uncertainty_preserved": {
                 "radius_models": ["min_1000", "max_1500", "broad_uniform_1000_1500"],
                 "error_models": ["fixed_location_hash", "spatially_correlated", "bounded_extreme"],
                 "layout_models": ["broad_disk", "boundary", "clustered", "outward_directed_stress"],
                 "orientation_models": ["broad_uniform", "outward_stress"],
                 "status": "unidentified mechanisms; assumptions retained, not estimated from completion time"}}
    conditionals = conditional_feedback(measurements, repeats=min(bootstrap_repeats, 300))
    checks = {"composition": composition_predictive_checks(cases, models),
              "feedback": feedback_predictive_checks(measurements, conditionals)}
    costs = cost_summaries(cases, repeats=bootstrap_repeats)
    matrices = visibility_matrices(measurements)
    joint_visibility = channel_visibility_patterns(cases, measurements)
    model_checks = mechanism_checks(cases, measurements, proxies, contractions, matrices,
                                    joint_visibility=joint_visibility, repeats=min(bootstrap_repeats, 500))
    model["mechanism_calibration_status"] = model_checks["status"]
    model["provenance"]["n_planned_unstarted"] = sum(not r["attempt_started"] for r in cases)
    _write_json(output / "model.json", model)
    _write_json(output / "predictive_checks.json", checks)
    _write_json(output / "cost_summaries.json", costs)
    _write_json(output / "model_checks.json", model_checks)
    handoff = mechanism_handoff(cases, model_checks)
    _write_json(output / "mechanism_assessments.json", handoff)
    _write_json(output / "mechanism_candidates.json", [row for row in handoff if row["status"] != "biased"])
    _write_jsonl(features / "cases.jsonl", cases)
    _write_csv(features / "cases.csv", cases)
    _write_jsonl(features / "measurements.jsonl", measurements)
    _write_csv(features / "measurements.csv", measurements)
    _write_jsonl(features / "visibility_matrices.jsonl", matrices)
    _write_jsonl(features / "channel_visibility_patterns.jsonl", joint_visibility["channel_patterns"])
    _write_csv(features / "channel_visibility_patterns.csv", joint_visibility["channel_patterns"])
    _write_jsonl(features / "joint_visibility_case_summaries.jsonl", joint_visibility["case_summaries"])
    _write_csv(features / "joint_visibility_pairs.csv", joint_visibility["adjacent_pairs"])
    _write_jsonl(features / "response_conditionals.jsonl", conditionals)
    _write_csv(features / "response_conditionals.csv", conditionals)
    _write_csv(features / "observable_pairs.csv", proxies)
    _write_csv(features / "contractions.csv", contractions)
    _write_jsonl(features / "joint_constraints.jsonl", [b["constraints"] for b in bundles])
    text = ["# 整局日志校准与可观测检验", "", f"模型状态：{model['model_status']}。读取 {len(cases)} 个案例目录，"
            f"其中官方 {len(official)} 个实际尝试、{model['provenance']['n_official_cases']} 个已确认进入案例；"
            f"另有 {model['provenance']['n_planned_unstarted']} 个仅预分配而未启动的目录，不进入尝试和成本分母。",
            "", "这里只估计源总数与定向数的联合分布 P(N,Ndir)，不把两张独立直方图相乘。"
            "无 N 或 Ndir 的案例保留在审计/成本表，但不进入该联合拟合；问题3的 Ndir=0 来自题设。",
            "", "## 组成与划分", "", "|问题|fit已知联合组成局数|全部已知(N,Ndir)|development已知局数|", "|---|---:|---|---:|"]
    for problem in (3, 4):
        known = [r for r in official if r["problem"] == problem and r["N"] is not None and r["Ndir"] is not None]
        counts = Counter((r["N"], r["Ndir"]) for r in known)
        text.append(f"|{problem}|{models[str(problem)]['n_fit_known_joint']}|{dict(counts)}|{sum(r['split']=='development' for r in known)}|")
    text.extend(["", f"缺少预分配记录而使用追溯整局哈希的尝试数：{model['provenance']['retrospective_assignments']}。"
                 "旧演练分析只能用于探索，不能包装为前瞻验证。新实验须在进入前持久化 assignment.json；"
                 "development 不进入任何 fit 计数，也不自动用于选择候选模型。", "",
                 "联合候选包含经验分布、总先验权重为 " + str(prior_effective_cases) +
                 " 局的 Dirichlet 平滑分布和宽联合先验。先验按 N 均匀、Ndir|N 均匀构造；"
                 "这是明确的假设，经验项仍保留 N 与 Ndir 的依赖。空经验分布标 unavailable，不冒充已校准。",
                 "", "## 反馈与矩阵", "", f"提取 {len(measurements)} 次去重后 accepted 测量，"
                 f"{len(proxies)} 条同点/邻点报告关系，{len(contractions)} 条算法外包面积记录。",
                 "", "反馈按问题、采集协议、阶段、站点、测前存在认知、测前已清除状态与事后存在证据条件化。"
                 "事后存在标签仅用于离线分层，不反馈在线策略。未测频道 mask=false、值为 null；"
                 "no_signal 只表示一次真实无信号反馈。全20频道且清除前测量的固定站点矩阵才适合作设计一致的比较。",
                 "", "channel_visibility_patterns.* 保留同一频道跨全部计划测点的联合模式与掩码。"
                 "joint_visibility_case_summaries.jsonl 给出每局已确认存在频道的可见站点数分布；"
                 "joint_visibility_pairs.csv 给出六个几何最近站点对的 both-visible 与 discordant 概率。"
                 "站点对按距离、路线索引确定，不按反馈选取；这是当前分析冻结规则，不追称旧数据采集前预注册。"
                 "概率先在整局内聚合，再按局计算不确定性；同局的频道与测量不是独立样本。",
                 "", "方向变化和同点重复值不是相对未知真实方向的误差；邻点还包含几何变化。"
                 "外包面积是算法代理，不是真定位误差。clear success 只给出以提交点为圆心的20m约束，提交点不是真值。",
                 "", "## 成本与不确定性", "", "|来源/问题/协议/划分/候选|尝试|完成|全部尝试观测耗时均值/s|完整局耗时均值/s|", "|---|---:|---:|---:|---:|"])
    for c in costs:
        text.append(f"|{c['source_kind']}/{c['problem']}/{c['protocol']}/{c['split']}/{c['model_candidate']}|{c['attempts']}|{c['complete_cases']}|"
                    f"{c['all_attempts_observed_virtual_s']['mean']}|{c['complete_only_virtual_s']['mean']}|")
    text.extend(["", "失败与未知状态不删除；未完成耗时是截至停止的投入，不能作为较快完成。"
                 "先报告完成率，再比较完整局成本。置信区间按整局重采样，动作行从不独立抽样；"
                 "不足2局的区间记 null，零观察事件不证明真实概率为零。",
                 "", "## 留出检验和输出", "", "predictive_checks.json 报告联合组成 NLL/Brier、"
                 "零预测概率次数，以及同协议条件可匹配的 development/本地反馈 NLL。"
                 "无法匹配的站点或协议不强行合并；覆盖率与未匹配数需一同查看。"
                 "这些检验只反映记录中的采样设计，不能识别未知 g、R、u 或完整误差场。",
                 "", "model.json 为组成模型；features/cases.* 保留失败和缺失；measurements.* 为公开反馈；"
                 "visibility_matrices.jsonl 带观测掩码；observable_pairs.csv、contractions.csv 为代理；"
                 "joint_constraints.jsonl 保留相同源共享参数的相容约束摘要。源文件 SHA256 进入案例审计。",
                 "", "半径、噪声、位置与发射朝向没有充分可辨识证据，继续保留多种宽分布与极端压力机制；"
                 "禁止根据清除耗时反向调成某个假想官方场景。"])
    text.extend(["", "## 相同协议的本地候选机制检验", "", "model_checks.json 只将真正复用 baseline/survey 的本地记录"
                 "与同问题、同协议的官方 development 案例比较。至少每侧5局才尝试区间判别；"
                 "差异95%整局自助区间落入预设实质等价范围才记单项 matched，整段落在范围外记 screen_flag，"
                 "其余及缺字段均记 insufficient_evidence。matched 仅代表所测可观察指标兼容。",
                 "", "检查联合(N,Ndir)的两样本总变差/置换检验，完整清除前20频道标准站点可见率，"
                 "survey追加测量数与无信号率、相对冻结估计点的名义距离/方位分层、同点/邻点报告关系、"
                 "外包面积收缩、完成率、失败清除数与路程。不同协议、不同实际站点不能互补成匹配。"
                 "候选 matched 还要求完整跨站联合分布、固定站点对指标及联合组成均达到实质兼容，"
                 "仅站点边缘可见率接近不够。单项偏差不能直接拒绝整个机制；只有核心联合可见性偏差"
                 "在预定整局哈希两半均以同方向超出容限、且各半每侧至少5局，才记探索性 biased。",
                 "", "|本地候选|问题|协议|官方development局|本地局|证据状态|", "|---|---:|---|---:|---:|---|"])
    for check in model_checks["candidate_checks"]:
        text.append(f"|{check['model_candidate']}|{check['problem']}|{check['protocol']}|{check['n_official_development']}|"
                    f"{check['n_local']}|{check['status']}|")
    if not model_checks["candidate_checks"]:
        text.append("|尚无相同协议本地重复|—|—|—|0|insufficient_evidence|")
    text.extend(["", "这些多指标检查是探索性筛查，未做整个指标族的同时显著性控制；"
                 "biased 提醒检查或降低候选优先级，不能单凭一次筛查宣布真实机制已被证明。"
                 "首轮每问20 survey的3:1划分只留下5局development，两半稳定检查通常证据不足；"
                 "增加本地局数不能代替新的官方留出证据。"
                 "development若据此参与模型保留，下一轮或最终验收必须另用未消费案例。"])
    if sparse:
        text.extend(["", "**当前样本只支持探索性描述，不支持宣称强校准、官方生成律恢复或精确泛化保证。**"])
    (output / "report.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    return model
