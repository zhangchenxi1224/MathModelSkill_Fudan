"""Read-only post-hoc DEVELOPMENT worst-pair diagnostics for P4 refinement.

Opens only development/results.json, selected development journals, and their
individual scenario files. Never opens a confirmation table or executes a policy.
Truth is used only to explain logged measurements after the evaluation ended.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path

from diagnose_paired_regressions import COMPONENTS, load, replay_costs, rows_gzip, save, sha
from bsolver.geometry import minimum_enclosing_circle

ROOT = Path(__file__).resolve().parents[1]
ARMS = ("current", "coverage", "local_cover", "combined_cover")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def area(poly):
    return abs(sum(a[0]*b[1]-a[1]*b[0] for a, b in zip(poly, poly[1:]+poly[:1])))/2 if poly else 0.


def select_worst(rows):
    if not rows or any(r.get("partition") != "development" or r.get("problem") != 4 for r in rows):
        raise ValueError("Only nonempty P4 DEVELOPMENT rows are accepted")
    index = {}
    for row in rows:
        key = row["case_id"], row["variant"]
        if row["variant"] not in ARMS or key in index:
            raise ValueError("Unknown arm or duplicate case/arm")
        index[key] = row
    groups = defaultdict(list)
    for (case, variant), base in index.items():
        if variant != "current":
            continue
        variants = {arm: index[case, arm] for arm in ARMS}
        for row in variants.values():
            for key in ("scenario_sha256", "error_field_sha256", "world_sha256", "policy_core_sha256", "pool", "partition"):
                if row[key] != base[key]:
                    raise ValueError(f"Pair mismatch: {key}")
        delta = variants["combined_cover"]["failure_penalized_time_s"]-base["failure_penalized_time_s"]
        groups[base["pool"]].append((delta, case, variants))
    selected = [sorted(group, key=lambda p: (-p[0], p[1]))[0] for _, group in sorted(groups.items())]
    return selected, {pool: len(group) for pool, group in groups.items()}


def visible_truth(source, point):
    dx, dy = point[0]-source["position"][0], point[1]-source["position"][1]
    distance = math.hypot(dx, dy)
    angle = source.get("direction_deg")
    projection = None if angle is None else math.cos(math.radians(angle))*dx+math.sin(math.radians(angle))*dy
    tolerance = 8*math.ulp(max(1., abs(dx), abs(dy)))
    visible = distance <= source["radius"] and (projection is None or projection >= -tolerance)
    return {"visible": visible, "distance_m": distance, "radius_m": source["radius"],
            "directed_projection_m": projection,
            "reason": "visible" if visible else "beyond_radius" if distance > source["radius"] else "outside_fixed_direction_halfplane"}


def summarize_arm(root, pool, case, expected, scenario):
    folder = root/"development"/"runs"/pool/case/expected["variant"]
    paths = {"result": folder/"result.json", "requests": folder/"requests.jsonl.gz", "decisions": folder/"decisions.jsonl.gz"}
    actual = load(paths["result"])
    for key in ("case_id", "variant", "partition", "scenario_sha256", "error_field_sha256", "total_virtual_time_s", "evaluation_complete"):
        if actual[key] != expected[key]:
            raise ValueError(f"Journal result mismatch: {key}")
    for name in ("requests", "decisions"):
        if sha(paths[name]) != expected["artifact_sha256"][paths[name].name]:
            raise ValueError("Frozen journal SHA256 mismatch")
    decisions = rows_gzip(paths["decisions"])
    replay = replay_costs(rows_gzip(paths["requests"]), decisions)
    if abs(replay["total_replayed_s"]-actual["total_virtual_time_s"]) > 3e-5:
        raise ValueError("Total virtual time does not reconcile")
    for key in ("measures", "switches", "clear_attempts", "clear_successes"):
        if replay["counts"].get(key, 0) != actual[key]:
            raise ValueError("Accepted action count differs from recorded result")
    starts = [d for d in decisions if d["event"] == "start"]
    if len(starts) != 1:
        raise ValueError("Expected one start")
    points = starts[0]["coverage_points"]
    station_lookup = {tuple(p): i for i, p in enumerate(points)}
    decision_lookup = {d["sequence"]: d for d in decisions}
    sources = {s["channel"]: s for s in scenario["sources"]}
    stations, channels, reasons = {}, {}, {}
    cleared, detected = set(), set()
    for action in replay["actions"]:
        ch, seq = action["channel"], action["decision_sequence"]
        d = decision_lookup[seq]
        reason = action["reason_or_certificate"]
        if reason not in reasons:
            reasons[reason] = {"count": 0, "components_s": {k: 0. for k in COMPONENTS}}
        reasons[reason]["count"] += 1
        for key in COMPONENTS:
            reasons[reason]["components_s"][key] += action["component_s"][key]
        if ch not in channels:
            channels[ch] = {"first_positive": None, "local_measures": [], "clear_success": None,
                            "failed_clear_attempts": 0, "fallback_attempts": [],
                            "components_s": {k: 0. for k in COMPONENTS}}
        data = channels[ch]
        for key in COMPONENTS:
            data["components_s"][key] += action["component_s"][key]
        if reason == "global_coverage":
            idx = station_lookup[tuple(action["position"])]
            if idx not in stations:
                stations[idx] = {"station_index_zero_based": idx, "position": points[idx], "channels": [],
                                 "positive_channels": [], "outcomes": {}, "first_time_s": action["virtual_time_s"],
                                 "cleared_before_station": sorted(cleared), "detected_before_station": sorted(detected)}
            station = stations[idx]
            station["channels"].append(ch)
            station["outcomes"][str(ch)] = action["outcome"]
            station["last_measure_time_s"] = action["virtual_time_s"]
            if action["outcome"] != "no_signal":
                station["positive_channels"].append(ch)
        else:
            idx = None
        if action["event"] == "measure":
            source = sources.get(ch)
            if source is not None and ch not in cleared:
                fact = visible_truth(source, action["position"])
                if fact["visible"] != (action["outcome"] != "no_signal"):
                    raise ValueError("Post-hoc visibility does not match the recorded outcome")
            if action["outcome"] in ("direction", "near") and data["first_positive"] is None:
                detected.add(ch)
                data["first_positive"] = {"sequence": seq, "virtual_time_s": action["virtual_time_s"],
                                          "position": action["position"], "station_index_zero_based": idx,
                                          "outcome": action["outcome"], "hull_area_m2": area(d["knowledge"]["hull"]),
                                          "truth_geometry_posthoc": visible_truth(source, action["position"])}
            if reason == "local_active_sensing":
                data["local_measures"].append({"sequence": seq, "virtual_time_s": action["virtual_time_s"],
                                               "position": action["position"], "outcome": action["outcome"],
                                               "hull_area_m2": area(d["knowledge"]["hull"]),
                                               "truth_geometry_posthoc": visible_truth(source, action["position"])})
        if action["event"] == "clear":
            if action["outcome"] == "success":
                cleared.add(ch)
                data["clear_success"] = {"sequence": seq, "virtual_time_s": action["virtual_time_s"],
                                          "position": action["position"], "certificate": d["certificate"]}
            else:
                data["failed_clear_attempts"] += 1
            if reason == "optical_grid_attempt":
                data["fallback_attempts"].append({"sequence": seq, "position": action["position"],
                                                  "move_s": action["component_s"]["move_s"],
                                                  "outcome": action["outcome"], "certificate": d["certificate"]})
    for ch, data in channels.items():
        fallback = data["fallback_attempts"]
        data["fallback_summary"] = {"attempts": len(fallback),
                                    "failed_attempts": sum(a["outcome"] != "success" for a in fallback),
                                    "approach_move_s": fallback[0]["move_s"] if fallback else 0.,
                                    "within_fallback_move_s": sum(a["move_s"] for a in fallback[1:])}
        plans = [d for d in decisions if d["event"] == "refined_cover_plan" and d["target_channel"] == ch]
        data["refined_cover_plans"] = [{"sequence": d["sequence"], "virtual_time_s": d["virtual_time_s"],
                                         "plan": {k: v for k, v in d["plan"].items() if k != "centers"}}
                                        for d in plans]
    # Reconstruct only public knowledge at each actual station departure. The
    # unchanged joint scheduler serves a target iff its MEC detour is <=800m;
    # a next-station request exposes a deferral even though no defer event exists.
    snapshots, deferrals = {}, []
    previous_station, previous_position = None, (0., 0.)
    for action in replay["actions"]:
        if action["reason_or_certificate"] == "global_coverage":
            idx = station_lookup[tuple(action["position"])]
            if previous_station is not None and idx != previous_station:
                candidates = []
                for ch, snap in snapshots.items():
                    if snap["status"] != "detected":
                        continue
                    center, radius = minimum_enclosing_circle(snap["hull"])
                    detour = math.dist(previous_position, center)+math.dist(center, points[idx])-math.dist(previous_position, points[idx])
                    candidates.append({"channel": ch, "mec_center": center, "mec_radius_m": radius,
                                       "detour_m": detour})
                candidates.sort(key=lambda c: (c["detour_m"], c["channel"]))
                if candidates:
                    if candidates[0]["detour_m"] <= actual["config"]["joint_detour_m"]-1e-7:
                        raise ValueError("Reconstructed scheduler deferral contradicts its fixed detour rule")
                    deferrals.append({"after_station_index_zero_based": previous_station,
                                      "next_station_index_zero_based": idx,
                                      "next_request_decision_sequence": action["decision_sequence"],
                                      "position_at_deferral": previous_position,
                                      "threshold_m": actual["config"]["joint_detour_m"],
                                      "pending_candidates_sorted_by_detour": candidates})
            previous_station = idx
        decision = decision_lookup[action["decision_sequence"]]
        snapshots[decision["knowledge"]["channel"]] = decision["knowledge"]
        previous_position = action["position"]
    visited = list(stations)
    prefix_walk = sum(math.dist(a, b) for a, b in zip([(0., 0.)]+[points[i] for i in visited], [points[i] for i in visited]))
    discoveries = sorted([dict(channel=ch, **data["first_positive"]) for ch, data in channels.items() if data["first_positive"]], key=lambda d: d["sequence"])
    successes = sorted([dict(channel=ch, **data["clear_success"]) for ch, data in channels.items() if data["clear_success"]], key=lambda d: d["sequence"])
    after_all_detected = [a for a in replay["actions"] if discoveries and a["decision_sequence"] > discoveries[-1]["sequence"]]
    return {"variant": expected["variant"], "source_sha256": {k: sha(p) for k, p in paths.items()},
            "total_virtual_time_s": actual["total_virtual_time_s"], "components_s": replay["components_s"],
            "counts": replay["counts"], "timing_residual_s": actual["total_virtual_time_s"]-replay["total_replayed_s"],
            "stop_evidence": actual["stop_evidence"], "evaluation_complete": actual["evaluation_complete"],
            "full_route_point_count": len(points), "visited_station_count": len(visited),
            "visited_station_indices_zero_based": visited, "ordered_visited_stations": list(stations.values()),
            "geometric_visited_station_prefix_length_m": prefix_walk,
            "prefix_length_scope": "Connect actual scan stations from origin without localization detours; geometric explanatory quantity, not an extra tariff component.",
            "initial_station_truth_visible_channels": [ch for ch, s in sorted(sources.items()) if visible_truth(s, points[0])["visible"]],
            "discoveries_in_order": discoveries, "successful_clears_in_order": successes,
            "joint_deferrals_reconstructed_from_public_state": deferrals,
            "after_last_true_source_first_positive_posthoc": {
                "scope": "Last first detection identified using completed-case source count; strategy was not supplied hidden N.",
                "remaining_time_s": actual["total_virtual_time_s"]-discoveries[-1]["virtual_time_s"] if discoveries else None,
                "global_measurements": sum(a["reason_or_certificate"] == "global_coverage" for a in after_all_detected),
                "components_s": {k: sum(a["component_s"][k] for a in after_all_detected) for k in COMPONENTS}},
            "per_reason": reasons, "channels": channels, "diagnostics": replay["diagnostics"],
            "accepted_actions": replay["actions"]}


def diagnose(study_root):
    study_root = Path(study_root).resolve()
    rows_path = study_root/"development"/"results.json"
    rows = load(rows_path)
    selected, populations = select_worst(rows)
    output = {"version": "p4-development-worst-actual-journal-v1", "scope": "development_only_posthoc_selected_extremes",
              "selection_rule": "Largest signed combined_cover minus current failure-penalized virtual time in each DEVELOPMENT pool; lexicographic case ID breaks ties.",
              "limitations": ["Three selected extremes are descriptive and selected after observing development results, not fresh inference or representative effect estimates.",
                              "No confirmation performance is read. No policy is executed or changed. No new candidate is constructed.",
                              "Scenario truth is read only for the selected completed cases to verify visibility and explain logged source discoveries.",
                              "Action costs attach movement to its destination action; per-channel or reason breakdown is exact accounting, not an isolated causal effect.",
                              "Same-world four-arm differences show interactions; do not extrapolate one extreme to a population."],
              "sources": {"development_results": str(rows_path), "development_results_sha256": sha(rows_path),
                          "script_sha256": sha(__file__), "replay_dependency_sha256": sha(Path(__file__).with_name("diagnose_paired_regressions.py")),
                          "accepted_filter_sha256": sha(ROOT/"src"/"bsolver"/"calibration.py"),
                          "geometry_sha256": sha(ROOT/"src"/"bsolver"/"geometry.py"),
                          "reviewed_scheduler_sha256": sha(ROOT/"src"/"bsolver"/"strategy.py"),
                          "reviewed_simulator_sha256": sha(ROOT/"src"/"bsolver"/"simulator.py")},
              "development_worlds_per_pool": populations, "physical_development_runs": len(rows),
              "development_hull_invariant_violations": sum(len(r["hull_invariant_violations"]) for r in rows),
              "actual_development_failures": [{"case_id": r["case_id"], "variant": r["variant"], "status": r["status"], "error": r.get("error")}
                                              for r in rows if not r["evaluation_complete"]],
              "future_research_directions_not_implemented": [
                  "研究首次发现/清除前缀成本与覆盖完整路线成本的差异；新路线更短的保证仅针对完成全部站点的构造。",
                  "独立评估已有16个不同阳性频道时优先清理已发现集合的策略，再由16次成功清除取得原停止证书；不得在仅发现16个时冒称已完成。",
                  "将顺路清理的固定800m阈值与尚需扫描的剩余成本比较，避免已知目标长期排队；需另行预注册并在新世界评估。",
                  "局部覆盖优化可同时报告完整路线界和首次命中前缀；较少格数/较小最坏界并不逐场景支配旧网格。",
                  "把原点首扫作为独立未来因素评估；本轮首站南移的直接43.084764s和信息变化不能仅用其中一个解释全部差异。"],
              "no_new_candidate_this_round": True, "worst_pairs": []}
    for delta, case, variants in selected:
        pool = variants["current"]["pool"]
        scenario_path = study_root/"scenarios"/(case+".json")
        scenario = load(scenario_path)
        if scenario["partition"] != "development" or scenario["case_id"] != case:
            raise ValueError("Selected scenario is not the expected DEVELOPMENT case")
        case_without_hash = {k: v for k, v in scenario.items() if k != "scenario_sha256"}
        if (digest(case_without_hash) != scenario["scenario_sha256"]
                or scenario["scenario_sha256"] != variants["current"]["scenario_sha256"]
                or digest(scenario["error_field"]) != variants["current"]["error_field_sha256"]
                or digest({k: scenario[k] for k in ("problem", "sources", "error_field")}) != variants["current"]["world_sha256"]):
            raise ValueError("Selected scenario content does not match frozen pair hashes")
        arms = {arm: summarize_arm(study_root, pool, case, variants[arm], scenario) for arm in ARMS}
        old, new = arms["current"], arms["combined_cover"]
        components = {key: new["components_s"][key]-old["components_s"][key] for key in COMPONENTS}
        if abs(sum(components.values())-(new["total_virtual_time_s"]-old["total_virtual_time_s"])) > 3e-5:
            raise ValueError("Pair component delta does not reconcile")
        channel_deltas = []
        for ch in sorted(set(old["channels"]) | set(new["channels"])):
            a, b = old["channels"].get(ch), new["channels"].get(ch)
            ac = a["components_s"] if a else {k: 0. for k in COMPONENTS}
            bc = b["components_s"] if b else {k: 0. for k in COMPONENTS}
            channel_deltas.append({"channel": ch, "attached_time_delta_s": sum(bc.values())-sum(ac.values()),
                                   "component_deltas_s": {k: bc[k]-ac[k] for k in COMPONENTS},
                                   "failed_clear_delta": (b["failed_clear_attempts"] if b else 0)-(a["failed_clear_attempts"] if a else 0)})
        t = {arm: arms[arm]["total_virtual_time_s"] for arm in ARMS}
        local_changes = []
        fallback_changes = []
        for ch in sorted(old["channels"]):
            a, b = old["channels"][ch], new["channels"][ch]
            if a["local_measures"] and b["local_measures"]:
                ao, bo = a["local_measures"][0], b["local_measures"][0]
                if ao["outcome"] != bo["outcome"]:
                    local_changes.append({"channel": ch, "current": ao, "combined_cover": bo,
                                          "current_fallback": a["fallback_summary"],
                                          "combined_fallback": b["fallback_summary"]})
            a = arms["coverage"]["channels"][ch]
            if a["fallback_summary"]["attempts"] or b["fallback_summary"]["attempts"]:
                fallback_changes.append({"channel": ch,
                                          "same_local_measure_observable_record": bool(a["local_measures"] and b["local_measures"] and all(a["local_measures"][0][k] == b["local_measures"][0][k] for k in ("position", "outcome", "hull_area_m2"))),
                                          "coverage_old_fallback": a["fallback_summary"], "combined_new_fallback": b["fallback_summary"],
                                          "fallback_attempt_delta": b["fallback_summary"]["attempts"]-a["fallback_summary"]["attempts"]})
        interpretation = {
            "all_sources_cleared_in_both": bool(old["evaluation_complete"] and new["evaluation_complete"]),
            "both_stop_certificate_types": [old["stop_evidence"]["type"], new["stop_evidence"]["type"]],
            "initial_station_current": old["ordered_visited_stations"][0]["position"],
            "initial_station_combined": new["ordered_visited_stations"][0]["position"],
            "direct_initial_movement_delta_s": new["accepted_actions"][0]["component_s"]["move_s"]-old["accepted_actions"][0]["component_s"]["move_s"],
            "initial_positive_lost_channels": sorted(set(old["initial_station_truth_visible_channels"])-set(new["initial_station_truth_visible_channels"])),
            "initial_positive_gained_channels": sorted(set(new["initial_station_truth_visible_channels"])-set(old["initial_station_truth_visible_channels"])),
            "visited_station_count_current_combined": [old["visited_station_count"], new["visited_station_count"]],
            "geometric_scan_prefix_length_delta_m": new["geometric_visited_station_prefix_length_m"]-old["geometric_visited_station_prefix_length_m"],
            "global_measure_count_delta": new["per_reason"]["global_coverage"]["count"]-old["per_reason"]["global_coverage"]["count"],
            "local_measure_count_current_combined": [len([a for a in old["accepted_actions"] if a["reason_or_certificate"] == "local_active_sensing"]),
                                                     len([a for a in new["accepted_actions"] if a["reason_or_certificate"] == "local_active_sensing"])],
            "local_measure_outcome_changes": local_changes,
            "same_new_coverage_fallback_comparison": sorted(fallback_changes, key=lambda d: -d["fallback_attempt_delta"]),
            "accounting_conclusion": "全部差值来自已确认动作成本；失败清除是合法光学尝试，未发生清除失败误报或未完成误报。",
            "interaction_scope": "同一世界四臂可比较实际结果，但路线改变观察点、方向读数、服务次序和首次命中位置；不能把combined减current全部归因于新局部网格。"}
        output["worst_pairs"].append({"pool": pool, "case_id": case, "delta_virtual_time_s": t["combined_cover"]-t["current"],
                                      "delta_failure_penalized_s": delta, "component_deltas_s": components,
                                      "scenario_file_sha256": sha(scenario_path), "scenario_sha256": variants["current"]["scenario_sha256"],
                                      "error_field_sha256": variants["current"]["error_field_sha256"],
                                      "truth_post_evaluation": {k: scenario[k] for k in ("seed", "n", "n_directed", "layout", "radius_mode", "error_mode", "mechanism_id", "sources")},
                                      "four_arm_times_s": t,
                                      "explanatory_facts": interpretation,
                                      "four_arm_deltas_s": {"coverage_minus_current": t["coverage"]-t["current"],
                                                            "local_cover_minus_current": t["local_cover"]-t["current"],
                                                            "combined_minus_coverage": t["combined_cover"]-t["coverage"],
                                                            "combined_minus_local_cover": t["combined_cover"]-t["local_cover"],
                                                            "interaction_difference_of_differences": t["combined_cover"]-t["coverage"]-t["local_cover"]+t["current"]},
                                      "channel_delta_destination_attached_descending": sorted(channel_deltas, key=lambda d: -d["attached_time_delta_s"]),
                                      "arms": arms})
    audited_arms = [arm for pair in output["worst_pairs"] for arm in pair["arms"].values()]
    output["journal_audit_summary"] = {
        "selected_runs_audited": len(audited_arms),
        "accepted_measure_and_clear_actions": sum(len(a["accepted_actions"]) for a in audited_arms),
        "max_absolute_total_time_residual_s": max(abs(a["timing_residual_s"]) for a in audited_arms),
        "all_source_and_journal_hash_checks_passed": True,
        "all_reconstructed_joint_deferrals_match_800m_rule": True,
        "all_preclear_measurement_visibility_matches_selected_case_truth": True,
        "confirmation_results_opened": False,
    }
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, default=ROOT/"results"/"p4_refinement")
    parser.add_argument("--output", type=Path, default=ROOT/"results"/"p4_refinement_diagnostics"/"development_regressions.json")
    args = parser.parse_args(argv)
    if "confirmation" in args.output.parts or args.output.resolve().is_relative_to(args.study.resolve()):
        raise ValueError("Diagnostic output must be separate from frozen study inputs")
    result = diagnose(args.study)
    save(args.output, result)
    print(json.dumps({"output": str(args.output), "sha256": sha(args.output),
                      "cases": [{"pool": p["pool"], "case_id": p["case_id"], "delta_s": p["delta_virtual_time_s"],
                                 "components": p["component_deltas_s"], "four_arm_times_s": p["four_arm_times_s"]}
                                for p in result["worst_pairs"]]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
