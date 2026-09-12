"""Read-only historical/public-log evidence for the next iteration; no policy calls."""
from __future__ import annotations
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def lines(path):
    return [json.loads(s) for s in Path(path).read_text(encoding="utf-8-sig").splitlines() if s.strip()]


def quantile(values, q):
    a = sorted(values)
    if not a:
        return None
    p = q*(len(a)-1)
    i, j = math.floor(p), math.ceil(p)
    return a[i] if i == j else a[i]*(j-p)+a[j]*(p-i)


def mean(values):
    return statistics.fmean(values) if values else None


def analyze_case(folder, group, index=None):
    folder = Path(folder)
    result, audit = read(folder/"result.json"), read(folder/"post_exit_audit.json")
    requests, decisions = lines(folder/"requests.jsonl"), lines(folder/"decisions.jsonl")
    accepted, seen = [], set()
    for r in requests:
        rid = r.get("request", {}).get("request_id")
        if r.get("outcome") == "accepted" and rid not in seen:
            seen.add(rid)
            accepted.append(r)
    successes = [r for r in accepted if r["path"] == "/clear" and r["response"]["clear_result"] == "success"]
    n, nd = audit["source_total_post_exit"], audit["directional_total_post_exit"]
    full = result["status"] == "complete" and len(successes) == n and not result.get("error")
    action_events = [e for e in decisions if e.get("event") in ("measure", "clear")]
    action_map = {(e["event"], e["virtual_time_s"]): e for e in action_events}
    phases = defaultdict(lambda: Counter())
    target = defaultdict(lambda: Counter())
    local_targets = defaultdict(lambda: Counter())
    local_outcomes = {}
    cover_plans = {}
    reasons = defaultdict(lambda: Counter())
    certificates = Counter()
    predicted, actual_no = [], []
    last_choices = {}
    for e in decisions:
        if e.get("event") == "refined_cover_plan":
            cover_plans[e["target_channel"]] = {k:e["plan"].get(k) for k in
                ("cell_count", "cost_upper_bound_s", "walk_distance_upper_bound_m", "route_kind")}
        if e.get("event") == "choose_measurement":
            last_choices[e["target_channel"]] = e.get("selection", {})
        if e.get("event") == "measure" and e.get("reason") == "local_active_sensing":
            c = e.get("knowledge", {}).get("channel", e["channel"])
            local_outcomes[c] = e["response"]["measure_result"]
            choice = last_choices.pop(c, {})
            p = choice.get("selected", {}).get("branch_probabilities", {}).get("no_signal")
            if p is not None:
                predicted.append(p)
                actual_no.append(int(e["response"]["measure_result"] == "no_signal"))
    last_clear_time = successes[-1]["response"]["virtual_time_s"] if successes and full else None
    tail = Counter()
    total = Counter()
    global_points = []
    for r in accepted:
        path, body, response = r["path"], r["request"], r["response"]
        if path not in ("/measure", "/clear"):
            continue
        before = r["state_before"]
        p = (before["position"]["x"], before["position"]["y"])
        q = (body["position"]["x"], body["position"]["y"])
        movement = math.dist(p, q)/5
        event = action_map.get((path[1:], response["virtual_time_s"]), {})
        cost = {"movement_s": movement, "measurement_s": 0., "switch_s": 0., "optical_s": 0., "laser_s": 0.}
        c = body["channel"]
        if path == "/measure":
            why = event.get("reason", "unmapped_measure")
            outcome = response["measure_result"]
            cost["measurement_s"] = 5.
            cost["switch_s"] = int(c != before["current_channel"])
            reasons[why][outcome] += 1
            phase = why
            if why == "global_coverage" and (not global_points or global_points[-1] != q):
                global_points.append(q)
            target[c]["measures"] += 1
            target[c][outcome] += 1
        else:
            kind = event.get("certificate", {}).get("type", "unmapped_clear")
            success = response["clear_result"] == "success"
            phase = "clear_"+kind
            certificates[kind] += 1
            cost["optical_s"] = 3.
            cost["laser_s"] = 2.*success
            target[c]["clear_attempts"] += 1
            target[c]["failed_clears"] += int(not success)
            target[c]["successful_clears"] += int(success)
        for key, value in cost.items():
            total[key] += value
            phases[phase][key] += value
            target[c][key] += value
            if path == "/clear" or phase == "local_active_sensing":
                local_targets[c][key] += value
        if path == "/clear":
            local_targets[c]["clear_attempts"] += 1
            local_targets[c]["failed_clears"] += int(response["clear_result"] != "success")
        if path == "/clear" or phase == "local_active_sensing":
            local_targets[c]["total_service_s"] += sum(cost.values())
        phases[phase]["actions"] += 1
        target[c]["total_attributed_s"] += sum(cost.values())
        if last_clear_time is not None and response["virtual_time_s"] > last_clear_time+1e-7:
            for key, value in cost.items():
                tail[key] += value
            tail["actions"] += 1
            tail[path[1:]+"s"] += 1
    planned_path = [(0., 0.)]+global_points
    route_distance = sum(math.dist(a,b) for a,b in zip(planned_path, planned_path[1:]))
    t = result["total_virtual_time_s"]
    selected_stats = {key: result.get(key, 0) for key in ("fallback_targets", "certified_clears", "nosignal_choices",
        "nosignal_changed_choices", "nosignal_baseline_fallbacks", "refined_cover_targets", "refined_cover_cells",
        "refined_extra_measures", "refined_failed_cells_skipped")}
    return {"group": group, "index": index, "folder": folder.relative_to(ROOT).as_posix(),
        "case_id": audit["case_id"], "case_code": audit["case_code"], "problem": result["problem"],
        "N": n, "Ndir": nd, "complete": full, "T_s": t, "T_per_source_s": t/n,
        "components_s": dict(total), "component_residual_s": t-sum(total.values()),
        "walk_m": result["walk_distance_m"], "measures": result["measures"], "switches": result["switches"],
        "clear_attempts": result["clear_attempts"], "failed_clears": result["clear_attempts"]-len(successes),
        "clear_successes": len(successes), "public_request_records": len(requests), "accepted_requests": len(accepted),
        "stop_type": (result.get("stop_evidence") or {}).get("type"),
        "post_last_clear_tail_s": t-last_clear_time if last_clear_time is not None else None,
        "tail_components_s": dict(tail), "tail_note": "Descriptive hindsight: last true source count is public only after exit; tail is a subset of component costs, not additive.",
        "visited_distinct_ordered_station_count": len(global_points), "visited_station_route_m": route_distance,
        "actual_minus_visited_station_route_m": result["walk_distance_m"]-route_distance,
        "movement_note": "Excess over the actual visited station sequence includes localization/clear detours; no counterfactual saving is asserted.",
        "measure_outcomes_by_reason": dict(reasons), "action_phase_costs_s": dict(phases),
        "clear_certificate_counts": dict(certificates), "stats": selected_stats,
        "heuristic_no_signal": {"predicted": predicted, "observed": actual_no,
            "note": "Chosen-point finite-grid heuristic; descriptive calibration check, not calibrated probabilities or randomized comparison."},
        "highest_cost_targets": sorted((dict(channel=c, **v) for c,v in target.items()), key=lambda v:v["total_attributed_s"], reverse=True)[:5],
        "target_attribution_note": "Global station-transfer cost is booked to the first measured channel; this accounting assignment does not mean that channel causes the shared route.",
        "local_service_targets": sorted((dict(channel=c, local_measure_outcome=local_outcomes.get(c),
            cover_plan=cover_plans.get(c), **v) for c,v in local_targets.items()), key=lambda v:v["total_service_s"], reverse=True),
        "source_sha256": {n:sha(folder/n) for n in ("result.json", "post_exit_audit.json", "requests.jsonl", "decisions.jsonl")}}


def summarize(rows):
    components = {k:mean([r["components_s"].get(k, 0) for r in rows]) for k in
        ("movement_s", "measurement_s", "switch_s", "optical_s", "laser_s")}
    phases, outcomes, cert, stats = defaultdict(Counter), defaultdict(Counter), Counter(), Counter()
    for r in rows:
        for phase, values in r["action_phase_costs_s"].items():
            phases[phase].update(values)
        for why, values in r["measure_outcomes_by_reason"].items():
            outcomes[why].update(values)
        cert.update(r["clear_certificate_counts"])
        stats.update(r["stats"])
    predicted = [p for r in rows for p in r["heuristic_no_signal"]["predicted"]]
    observed = [p for r in rows for p in r["heuristic_no_signal"]["observed"]]
    local_branches = {}
    for outcome in ("direction", "no_signal"):
        targets = [target for row in rows for target in row["local_service_targets"]
                   if target["local_measure_outcome"] == outcome]
        local_branches[outcome] = {"targets":len(targets),
            "clear_attempts":sum(t["clear_attempts"] for t in targets),
            "failed_clears":sum(t["failed_clears"] for t in targets),
            "mean_local_service_s":mean([t["total_service_s"] for t in targets]),
            "scope":"Observable-branch descriptive grouping, not randomized branch assignment; service includes local measurement/clear motion, excludes shared global scanning and return to a subsequent scan station."}
    t = [r["T_s"] for r in rows]
    return {"cases": len(rows), "full_clear": sum(r["complete"] for r in rows),
        "mean_T_s": mean(t), "median_T_s": statistics.median(t), "p90_T_s": quantile(t,.9), "max_T_s": max(t),
        "mean_T_per_source_s":mean([r["T_per_source_s"] for r in rows]), "mean_N":mean([r["N"] for r in rows]),
        "mean_Ndir":mean([r["Ndir"] for r in rows]), "component_mean_s":components,
        "component_shares":{k:v/mean(t) for k,v in components.items()},
        "means":{key:mean([r[key] for r in rows]) for key in ("walk_m", "measures", "switches", "clear_attempts",
            "failed_clears", "clear_successes", "post_last_clear_tail_s", "actual_minus_visited_station_route_m")},
        "stat_totals":dict(stats), "stat_means":{k:v/len(rows) for k,v in stats.items()},
        "phase_mean_cost_s":{p:{k:v/len(rows) for k,v in values.items()} for p,values in phases.items()},
        "measurement_outcomes_total":dict(outcomes), "clear_certificate_totals":dict(cert),
        "stop_types":dict(Counter(r["stop_type"] for r in rows)),
        "N16_summary":{str(flag):{"cases":sum((r["N"]==16)==flag for r in rows),
            "mean_T_s":mean([r["T_s"] for r in rows if (r["N"]==16)==flag]),
            "mean_tail_s":mean([r["post_last_clear_tail_s"] for r in rows if (r["N"]==16)==flag])} for flag in (True,False)},
        "heuristic_no_signal_check": {"n":len(predicted), "mean_predicted":mean(predicted), "observed_fraction":mean(observed)},
        "local_service_by_measure_outcome":local_branches,
        "tail_p90_s":quantile([r["post_last_clear_tail_s"] for r in rows],.9),
        "max_component_residual_s":max(abs(r["component_residual_s"]) for r in rows),
        "slowest_cases":sorted(rows,key=lambda r:r["T_s"],reverse=True)[:3]}


def main():
    groups = defaultdict(list)
    for rnd in ("round1", "round2"):
        plan = read(ROOT/"results"/rnd/"plan.json")
        for c in plan["cases"]:
            group=f"{rnd}_p{c['problem']}_{c.get('arm', c['protocol'])}"
            groups[group].append(analyze_case(ROOT/"results"/rnd/"cases"/c["case_id"],group))
    latest_path = ROOT/"results/latest_practice_report/latest_results.json"
    latest = read(latest_path)
    for section in latest["sections"]:
        for key in ("primary_cases", "extra_cases"):
            for case in section.get(key,[]):
                group=f"latest_p{section['problem']}_all"
                groups[group].append(analyze_case(ROOT/case["raw_folder"], group, case["index"]))
    groups["latest_p4_first15"] = [r for r in groups["latest_p4_all"] if r["index"] <= 15]
    evidence = {"scope":"Read-only public logs; no new experiment, training, parameter change, official call or causal cross-round claim.",
        "batch_scope":"315 completed main-batch official cases =160 round1+120 round2+15 latest P3+20 latest P4. Four earlier integration cases and separate neighboring-research cases are not pooled here.",
        "unique_main_cases":len({r["folder"] for rows in groups.values() for r in rows}),
        "group_overlap":"latest_p4_first15 is a view into latest_p4_all, not 15 additional executions.",
        "generator_sha256":sha(Path(__file__)),
        "latest_report_sha256":sha(latest_path),
        "groups":{key:summarize(rows) for key,rows in groups.items()},
        "comparability": [
            "round2: randomized interleaved independent arms (30/problem/arm), independent bootstrap; no same-case pairing.",
            "round1: 60 baseline +20 survey per problem; survey includes diagnostic overhead, not a strategy efficacy comparison. 120 fit/40 development split.",
            "latest: P3 15 normal L1; P4 actually20 combined_cover, first15 and additional5 all retained. Original two-arm62 plan cancelled; no new randomized current comparator executed.",
            "Latest-versus-old differences are descriptive across independent cohorts and mixtures; cannot identify causal gain or separate NoSignal/local-cover/route effects.",
            "Local refinement:200 development worlds*4arms +800 frozen confirmation worlds*3arms=3200 runs; same hidden world and fixed error field across arms within each case. Three synthetic design pools separate; not official expected population.",
            "Round1 local960 mechanism checks +1800 distinct worlds/5800 staged policy runs =6760 main local runs; experiments differ in purpose and cannot be pooled as one efficacy sample. Do not inspect unused L2/L3 confirmation arms."],
        "local_refinement_confirmation_pool_pairs":read(ROOT/"results/p4_refinement/confirmation/pool_paired_summary.json"),
        "rows":{key:rows for key,rows in groups.items()}}
    out = ROOT/"results/next_iteration_review/main_evidence.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"output":str(out),"groups":{k:{x:v[x] for x in ("cases","mean_T_s","component_mean_s","means","stat_means","measurement_outcomes_total","stop_types","heuristic_no_signal_check")} for k,v in evidence["groups"].items() if k.startswith("latest")}},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
