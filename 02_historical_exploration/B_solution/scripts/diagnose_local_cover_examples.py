"""Read-only same-observation comparison of old and current-hull optical plans.

No RobotClient, simulator, HTTP transport, source positions or jlog decryption
are used.  Only this script and the requested new diagnostic output are created.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from bsolver.geometry import distance, distance_to_polygon
from bsolver.knowledge import ChannelKnowledge
from bsolver.refined_local import hull_cell_cover
from bsolver.sensing import optical_fallback_points


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def rows(path):
    for number, text in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if text.strip():
            yield number, json.loads(text)


def point(value):
    if isinstance(value, dict):
        value = value["x"], value["y"]
    return float(value[0]), float(value[1])


def event_key(channel, position, response, kind):
    field = "clear_result" if kind == "clear" else "measure_result"
    return int(channel), point(position), float(response["virtual_time_s"]), response[field]


def state_check(row, expected_position, expected_channel, expected_time):
    state = row["state_before"]
    if distance(point(state["position"]), expected_position) > 1e-8:
        raise ValueError("request state_before position does not match the accepted-action replay")
    if state["current_channel"] != expected_channel:
        raise ValueError("request state_before receiver channel does not match accepted-action replay")
    if abs(state["virtual_time_s"]-expected_time) > 1.1e-6:
        raise ValueError("request state_before virtual time does not match accepted-action replay")


def compare_plans(knowledge, position, current_channel, virtual_time, request_row,
                  request_id, decision_row, event, official_first_point):
    if knowledge.status != "detected" or knowledge.first_direction is None:
        raise ValueError("first optical attempt lacks a preceding detected direction state")
    post_hull = [point(p) for p in event["knowledge"]["hull"]]
    if len(post_hull) != len(knowledge.hull):
        raise ValueError("reconstructed pre-clear hull has a different vertex count")
    maximum_hull_difference = max((distance(a, b) for a, b in zip(post_hull, knowledge.hull)), default=0.)
    if maximum_hull_difference > 1e-8:
        raise ValueError("reconstructed pre-clear hull disagrees with public decision snapshot")

    full = optical_fallback_points(knowledge.first_direction)
    reverse = distance(position, full[-1]) < distance(position, full[0])
    if reverse:
        full.reverse()
    retained = [p for p in full if distance_to_polygon(knowledge.hull, p) <= 20.+1e-5]
    if not retained:
        raise ValueError("old policy's first optical attempt has no retained plan center")
    first_difference = distance(retained[0], official_first_point)
    if first_difference > 1e-8:
        raise ValueError("old offline plan does not reproduce the actual first optical request")
    old_walk = math.fsum(distance(a, b) for a, b in zip([position]+retained[:-1], retained))
    old_continuous = old_walk/5+3*len(retained)+2
    old_bound = old_continuous+len(retained)*1e-6
    plan = hull_cell_cover(knowledge.hull, position,
                           failed_positions=knowledge.failed_clear_positions, cell_side_m=28.)
    new_continuous = plan.walk_distance_m/5+3*len(plan.cells)+2
    delta = plan.cost_bound_s-old_bound

    return {
        "channel": knowledge.channel,
        "diagnostic_status": "same_prefix_optical_plan_comparison",
        "snapshot": {
            "request_row_1based": request_row, "request_id": request_id,
            "decision_row_1based": decision_row, "decision_sequence": event["sequence"],
            "cutoff": "immediately before the first accepted optical_grid_attempt; its response excluded",
            "position": position, "current_receiver_channel": current_channel,
            "virtual_time_s": virtual_time, "knowledge": knowledge.snapshot(),
            "observations_in_order": [
                {"position": obs.position, "result": obs.result, "angle_deg": obs.angle}
                for obs in knowledge.observations],
            "first_direction": {"position": knowledge.first_direction.position,
                                "angle_deg": knowledge.first_direction.angle},
            "accepted_observation_count": len(knowledge.observations),
        },
        "old_frozen_plan": {
            "source": "frozen NoSignalSolver optical_fallback_points plus current hull disk-disjoint filter",
            "generated_center_count": len(full), "retained_center_count": len(retained),
            "reverse_by_original_110_endpoints": reverse, "retained_centers_in_execution_order": retained,
            "walk_to_visit_all_retained_centers_m": old_walk,
            "continuous_cost_upper_bound_s": old_continuous,
            "timing_quantization_guard_s": len(retained)*1e-6,
            "cost_upper_bound_s": old_bound,
        },
        "new_offline_plan": {
            "mode": "cover", "cell_side_m": 28., **plan.summary(),
            "continuous_cost_upper_bound_s": new_continuous,
        },
        "new_minus_old_plan": {
            "retained_center_count": len(plan.cells)-len(retained),
            "full_route_walk_m": plan.walk_distance_m-old_walk,
            "cost_upper_bound_s": delta,
            "cost_upper_bound_fraction": delta/old_bound,
            "classification": "higher_upper_bound" if delta > 1e-8 else
                              "lower_upper_bound" if delta < -1e-8 else "equal_within_tolerance",
        },
        "checks": {"reconstructed_hull_vs_public_snapshot_max_vertex_difference_m": maximum_hull_difference,
                   "old_first_center_vs_actual_request_distance_m": first_difference,
                   "first_attempt_response_used_for_plan": False,
                   "later_observations_or_clear_outcomes_used_for_plan": False,
                   "true_target_position_used": False},
    }


def diagnose(case_dir):
    assignment = read_json(case_dir/"assignment.json")
    result = read_json(case_dir/"result.json")
    audit = read_json(case_dir/"post_exit_audit.json")
    if assignment["problem"] != 4 or assignment["arm"] != "candidate":
        raise ValueError("this diagnostic expects a P4 candidate official practice case")
    if assignment.get("environment") != "official_practice":
        raise ValueError("input is not tagged official_practice")
    if result["status"] != "complete" or audit["clear_fraction"] != 1.:
        raise ValueError("input must be an audited, complete historical case")

    decisions = list(rows(case_dir/"decisions.jsonl"))
    start_event = next(e for _, e in decisions if e.get("event") == "start")
    coverage_points = [point(p) for p in start_event["coverage_points"]]
    measure_events, clear_events = {}, {}
    for row_num, event in decisions:
        kind = event.get("event")
        if kind not in ("measure", "clear"):
            continue
        key = event_key(event["knowledge"]["channel"], event["position"], event["response"], kind)
        mapping = measure_events if kind == "measure" else clear_events
        if key in mapping:
            raise ValueError("ambiguous decision/action matching key")
        mapping[key] = row_num, event

    config = assignment["policy_spec"]["solver_config"]
    channels = {ch: ChannelKnowledge(ch, 4, config["epsilon_deg"]) for ch in range(1, 21)}
    position, current_channel, virtual_time = (0., 0.), 1, 0.
    seen, snapshots, counters, success_channels = {}, {}, Counter(), set()
    for request_row, entry in rows(case_dir/"requests.jsonl"):
        if entry.get("event") != "request":
            continue
        counters["request_rows"] += 1
        response = entry.get("response") or {}
        if response.get("accepted") is not True:
            counters["nonaccepted_rows"] += 1
            continue
        body, path = entry["request"], entry["path"]
        request_id = body["request_id"]
        signature = json.dumps({"path": path, "body": body}, sort_keys=True, separators=(",", ":"))
        if request_id in seen:
            if seen[request_id] != signature:
                raise ValueError("accepted request ID reused with a different path/body")
            counters["accepted_duplicate_rows"] += 1
            continue
        seen[request_id] = signature
        counters["unique_accepted_actions"] += 1
        state_check(entry, position, current_channel, virtual_time)
        if path == "/measure":
            channel, destination = int(body["channel"]), point(body["position"])
            key = event_key(channel, destination, response, "measure")
            _, event = measure_events[key]
            station = None
            if event.get("reason") == "global_coverage":
                station = next((i for i, p in enumerate(coverage_points)
                                if distance(p, destination) < 1e-8), None)
                if station is None:
                    raise ValueError("global measurement does not match its fixed public station plan")
            channels[channel].observe(destination, response, station)
            position, current_channel = destination, channel
            counters["measures"] += 1
        elif path == "/clear":
            channel, destination = int(body["channel"]), point(body["position"])
            key = event_key(channel, destination, response, "clear")
            decision_row, event = clear_events[key]
            if event["certificate"]["type"] == "optical_grid_attempt" and channel not in snapshots:
                snapshots[channel] = compare_plans(
                    channels[channel], position, current_channel, virtual_time, request_row,
                    request_id, decision_row, event, destination)
            # Crucially this feedback is applied only AFTER the offline plans.
            channels[channel].record_clear(destination, response)
            position = destination
            counters["clear_attempts"] += 1
            if response["clear_result"] == "success":
                if channel in success_channels:
                    raise ValueError("a channel was successfully cleared twice")
                success_channels.add(channel)
                counters["clear_successes"] += 1
        elif path not in ("/enter", "/exit"):
            raise ValueError(f"unsupported historical action {path}")
        virtual_time = float(response["virtual_time_s"])

    if counters["clear_successes"] != audit["source_total_post_exit"]:
        raise ValueError("public success count disagrees with post-exit source count")
    if counters["measures"] != result["measures"] or counters["clear_attempts"] != result["clear_attempts"]:
        raise ValueError("accepted action counts disagree with historical result")
    records = [snapshots.get(ch, {"channel": ch, "diagnostic_status": "no_optical_fallback_in_official_case",
                                 "comparison": None}) for ch in sorted(success_channels)]
    compared = [r for r in records if r["diagnostic_status"] == "same_prefix_optical_plan_comparison"]
    source_paths = [case_dir/name for name in ("requests.jsonl", "decisions.jsonl", "assignment.json",
                                              "result.json", "post_exit_audit.json")]
    implementation_paths = [Path(__file__).resolve()] + [PROJECT/"src/bsolver"/name for name in
        ("refined_local.py", "nosignal_sensing.py", "sensing.py", "knowledge.py", "geometry.py", "strategy.py")]
    return {
        "schema_version": 1, "generated_utc": datetime.now(timezone.utc).isoformat(),
        "case_id": assignment["case_id"], "case_code": audit["case_code"],
        "environment_of_source_logs": "official_practice", "problem": 4,
        "analysis_environment": "offline_public_observation_replay_no_actions",
        "interpretation": "Same observed state, alternative finite plans. Differences concern complete-plan upper bounds, not realized savings or new official measurements.",
        "limitations": ["No true target position or unseen response is used to compute either plan.",
                        "The actual success can occur before visiting all planned centers; subtracting their upper bounds is not an estimate of actual saved time.",
                        "Other targets, future coverage, and final no-missing-source certification are not replayed under the new policy.",
                        "The fixed historical case was selected for explanation, not as an independent performance test.",
                        "All successfully cleared channels are retained, including missing or adverse comparison cases."],
        "historical_post_exit_metadata_not_used_for_plan": {
            "source_total": audit["source_total_post_exit"],
            "directional_total": audit["directional_total_post_exit"],
            "clear_fraction": audit["clear_fraction"]},
        "source_files": [{"path": str(p.resolve()), "sha256": sha256(p)} for p in source_paths],
        "implementation_files": [{"path": str(p.resolve()), "sha256": sha256(p)} for p in implementation_paths],
        "reproduction": {"python": "D:/st_python/python.exe", "script": str(Path(__file__).resolve()),
                         "arguments": ["--case", str(case_dir.resolve()), "--output", "<new-output-json>"]},
        "replay_counts": dict(counters),
        "summary": {
            "public_success_channel_count": len(success_channels), "all_success_channels_retained": len(records) == len(success_channels),
            "compared_channel_count": len(compared), "channels_without_optical_fallback": len(records)-len(compared),
            "old_retained_center_count_sum": sum(r["old_frozen_plan"]["retained_center_count"] for r in compared),
            "new_retained_center_count_sum": sum(r["new_offline_plan"]["cell_count"] for r in compared),
            "old_complete_plan_upper_bound_sum_s": sum(r["old_frozen_plan"]["cost_upper_bound_s"] for r in compared),
            "new_complete_plan_upper_bound_sum_s": sum(r["new_offline_plan"]["cost_upper_bound_s"] for r in compared),
            "comparison_classifications": dict(Counter(r["new_minus_old_plan"]["classification"] for r in compared)),
            "issues": [],
        },
        "channels": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=PROJECT/"results/round2/cases/r2-p4-validation-052")
    parser.add_argument("--output", type=Path,
                        default=PROJECT/"results/p4_refinement_diagnostics/local_cover_examples.json")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Refusing to overwrite an existing diagnostic: choose a new output path")
    report = diagnose(args.case.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "sha256": sha256(args.output),
                      **report["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
