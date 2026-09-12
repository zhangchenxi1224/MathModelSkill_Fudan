"""Read-only target-state diagnostics from the 20 existing P4 public logs.

No policy, additional action, fitted threshold, hidden source or new simulation.
Recorded continuation costs are outcomes; proposed gates use only observable state.
"""
from __future__ import annotations
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
from bsolver.geometry import minimum_enclosing_circle, distance_to_polygon


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def lines(path):
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8-sig").splitlines() if x.strip()]


def qtile(a, q):
    a=sorted(a)
    if not a:
        return None
    p=q*(len(a)-1);lo,hi=math.floor(p),math.ceil(p)
    return a[lo] if lo==hi else a[lo]*(hi-p)+a[hi]*(p-lo)


def desc(a):
    a=[v for v in a if v is not None]
    return {"n":len(a),"mean":statistics.fmean(a) if a else None,"median":qtile(a,.5),
            "p90":qtile(a,.9),"min":min(a) if a else None,"max":max(a) if a else None}


def shape(hull, position):
    center,radius=minimum_enclosing_circle(hull)
    area=abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(hull,hull[1:]+hull[:1])))/2
    return {"vertices":len(hull),"area_m2":area,"mec_radius_m":radius,"mec_center":center,
            "min_range_to_hull_m":distance_to_polygon(hull,position),
            "mandatory_approach_lb_s":max(0.,distance_to_polygon(hull,position)-20.)/5.}


def analyze(folder,index):
    events=lines(folder/"decisions.jsonl")
    audit=read(folder/"post_exit_audit.json")
    last_knowledge,choices,targets={}, {}, {}
    for e in events:
        event=e["event"]
        if event=="choose_measurement":
            c=e["target_channel"]
            choices[c]=(e,last_knowledge[c])
        if event=="measure" and e.get("reason")=="local_active_sensing":
            c=e["knowledge"]["channel"]
            if c in targets:
                raise ValueError("This diagnostic expects the frozen single-local-measurement policy")
            choice,previous=choices[c]
            selected=choice["selection"]["selected"]
            prior=shape(previous["hull"], choice["position"])
            posterior=shape(e["knowledge"]["hull"], e["position"])
            targets[c]={"case_index":index,"case_id":audit["case_id"],"case_code":audit["case_code"],"channel":c,
                "local_outcome":e["response"]["measure_result"],
                "at_choice": {"time_s":choice["virtual_time_s"],"current_position":choice["position"],
                    "source_position_hull":prior,"negative_count":previous["negative_count"],
                    "positive_count":previous["positive_count"],
                    "selected_point":selected["point"],
                    "predicted_no_signal":selected["branch_probabilities"]["no_signal"],
                    "direction_radius_bound_m":selected["direction_posterior_radius_bound_m"],
                    "score_s":selected["score_s"],"cost_terms_s":selected["cost_terms_s"],
                    "visibility_certified":selected["positive_convex_visibility_certified"],
                    "selection_changed":choice["selection"]["selection_changed_from_baseline"]},
                "after_local_measurement":{"time_s":e["virtual_time_s"],"position":e["position"],
                    "source_position_hull":posterior,"negative_count":e["knowledge"]["negative_count"],
                    "positive_count":e["knowledge"]["positive_count"],
                    "failed_clear_count":len(e["knowledge"]["failed_clear_positions"])},
                "local_measure_step_s":e["virtual_time_s"]-choice["virtual_time_s"],
                "fallback":False,"plan":None,"clear_attempts":0,"failed_clears":0,
                "first_clear_time_s":None,"successful_clear_time_s":None}
        if event=="refined_cover_plan":
            c=e["target_channel"]
            target=targets[c]
            if abs(e["virtual_time_s"]-target["after_local_measurement"]["time_s"])>1e-6:
                raise ValueError("Unexpected action before recorded cover plan")
            target["fallback"]=True
            target["plan"]={k:e["plan"].get(k) for k in ("cell_count","generated_cells","excluded_cells",
                "walk_distance_upper_bound_m","cost_upper_bound_s","route_kind","basis")}
        if event=="clear":
            c=e["knowledge"]["channel"]
            if c in targets:
                target=targets[c]
                target["clear_attempts"]+=1
                target["failed_clears"]+=int(e["response"]["clear_result"]!="success")
                if target["first_clear_time_s"] is None:
                    target["first_clear_time_s"]=e["virtual_time_s"]
                    target["first_clear_point"]=e["position"]
                if e["response"]["clear_result"]=="success":
                    target["successful_clear_time_s"]=e["virtual_time_s"]
                    target["post_measure_clear_s"]=e["virtual_time_s"]-target["after_local_measurement"]["time_s"]
                    target["whole_local_service_s"]=e["virtual_time_s"]-target["at_choice"]["time_s"]
                    target["post_measure_clear_move_s"]=target["post_measure_clear_s"]-3*target["clear_attempts"]-2
                    target["clear_cost_above_mandatory_approach_s"]=target["post_measure_clear_s"]-target["after_local_measurement"]["source_position_hull"]["mandatory_approach_lb_s"]-5
                    if target["plan"]:
                        target["recorded_bound_slack_s"]=target["plan"]["cost_upper_bound_s"]-target["post_measure_clear_s"]
        if e.get("knowledge"):
            last_knowledge[e["knowledge"]["channel"]]=e["knowledge"]
    assert len(targets)==audit["source_total_post_exit"]
    for target in targets.values():
        assert target["successful_clear_time_s"] is not None
        target["source_files"]={name:sha(folder/name) for name in ("decisions.jsonl","requests.jsonl")}
        target["folder"]=folder.relative_to(ROOT).as_posix()
    return list(targets.values())


def summary(rows):
    return {"targets":len(rows),"cases":len({r['case_id'] for r in rows}),
        "fallback_targets":sum(r["fallback"] for r in rows),
        "no_signal_targets":sum(r["local_outcome"]=="no_signal" for r in rows),
        "clear_attempts":desc([r["clear_attempts"] for r in rows]),
        "failed_clears":desc([r["failed_clears"] for r in rows]),
        "post_measure_clear_s":desc([r["post_measure_clear_s"] for r in rows]),
        "whole_local_service_s":desc([r["whole_local_service_s"] for r in rows]),
        "post_measure_clear_move_s":desc([r["post_measure_clear_move_s"] for r in rows]),
        "mec_radius_m":desc([r["after_local_measurement"]["source_position_hull"]["mec_radius_m"] for r in rows]),
        "hull_area_m2":desc([r["after_local_measurement"]["source_position_hull"]["area_m2"] for r in rows]),
        "mandatory_approach_lb_s":desc([r["after_local_measurement"]["source_position_hull"]["mandatory_approach_lb_s"] for r in rows]),
        "recorded_fallback_cells":desc([r["plan"]["cell_count"] for r in rows if r["plan"]]),
        "recorded_fallback_bound_s":desc([r["plan"]["cost_upper_bound_s"] for r in rows if r["plan"]]),
        "fallback_bound_slack_s":desc([r["recorded_bound_slack_s"] for r in rows if r["plan"]]),
        "observed_clear_ge200_s":sum(r["post_measure_clear_s"]>=200 for r in rows),
        "observed_clear_ge400_s":sum(r["post_measure_clear_s"]>=400 for r in rows),
        "observed_clear_attempts_ge10":sum(r["clear_attempts"]>=10 for r in rows)}


def buckets(rows,feature,edges):
    out=[]
    for low,high in zip(edges,edges[1:]):
        subset=[r for r in rows if (value:=feature(r)) is not None and low<=value<high]
        out.append({"lower_inclusive":low,"upper_exclusive":high if math.isfinite(high) else None,**summary(subset)})
    return out


def main():
    source=read(ROOT/"results/latest_practice_report/latest_results.json")
    latest=next(s for s in source["sections"] if s["problem"]==4)
    rows=[]
    for case in latest["primary_cases"]+latest.get("extra_cases",[]):
        rows.extend(analyze(ROOT/case["raw_folder"],case["index"]))
    direction=[r for r in rows if r["local_outcome"]=="direction"]
    no_signal=[r for r in rows if r["local_outcome"]=="no_signal"]
    # Broad descriptive cutoffs are illustrative, not selected to optimize a score.
    gates={"no_signal_only":lambda r:r["local_outcome"]=="no_signal",
        "fallback_cells_ge20":lambda r:bool(r["plan"]) and r["plan"]["cell_count"]>=20,
        "post_mec_ge100m":lambda r:r["after_local_measurement"]["source_position_hull"]["mec_radius_m"]>=100,
        "fallback_bound_ge400s":lambda r:bool(r["plan"]) and r["plan"]["cost_upper_bound_s"]>=400,
        "fallback_bound_minus_approach_ge200s":lambda r:bool(r["plan"]) and r["plan"]["cost_upper_bound_s"]
            -r["after_local_measurement"]["source_position_hull"]["mandatory_approach_lb_s"]-5>=200}
    report={"scope":"20 existing official P4 cases,261 target states,public histories only. No new strategy/action/simulation/threshold optimization.",
        "unit":"Targets are nested in20 cases; bucket sample counts are not261 independent official replications.",
        "cost_scope":"post_measure_clear_s starts immediately after the first local measurement following initial discovery and ends at the successful clear. It excludes the measurement move and subsequent return/global travel. whole_local_service_s includes the local measurement move/action. Neither equals whole-case improvement.",
        "features":"choice features are available before local measurement; posterior geometry and recorded cover plan are available after it. Observed continuation cost/clear count are outcomes, never proposed decision inputs. Post-exit true positions/orientations/counts are not used as gate features.",
        "bound_warning":"A recorded feasible continuation upper bound can be loose; its slack and mandatory-approach subtraction do not establish value of another measurement. Expected gains require an explicitly justified feedback model; robust guarantees require all possible responses including no_signal.",
        "script_sha256":sha(Path(__file__)),"overall":summary(rows),
        "by_outcome":{"direction":summary(direction),"no_signal":summary(no_signal)},
        "choice_predicted_no_signal_buckets":buckets(rows,lambda r:r["at_choice"]["predicted_no_signal"],[0,.05,.1,.2,.4,1.000000001]),
        "choice_direction_radius_bound_buckets":buckets(rows,lambda r:r["at_choice"]["direction_radius_bound_m"],[0,40,100,200,400,math.inf]),
        "choice_direction_radius_bound_given_direction_buckets":buckets(direction,lambda r:r["at_choice"]["direction_radius_bound_m"],[0,40,100,200,400,math.inf]),
        "observed_direction_post_mec_buckets":buckets(direction,lambda r:r["after_local_measurement"]["source_position_hull"]["mec_radius_m"],[0,20,50,100,200,math.inf]),
        "recorded_cover_cell_buckets":buckets(rows,lambda r:r["plan"]["cell_count"] if r["plan"] else 0,[0,1,10,20,40,math.inf]),
        "exploratory_gate_coverage":{name:{"triggered":summary([r for r in rows if gate(r)]),
            "untriggered":summary([r for r in rows if not gate(r)])} for name,gate in gates.items()},
        "expensive_direction_examples":sorted(direction,key=lambda r:r["post_measure_clear_s"],reverse=True)[:12],
        "most_expensive_whole_service_direction":max(direction,key=lambda r:r["whole_local_service_s"]),
        "no_signal_unchanged_outer_hulls":sum(abs(r["at_choice"]["source_position_hull"]["area_m2"]
            -r["after_local_measurement"]["source_position_hull"]["area_m2"])<1e-6 for r in no_signal),
        "direction_bound_exceedances":sum(r["after_local_measurement"]["source_position_hull"]["mec_radius_m"]
            >r["at_choice"]["direction_radius_bound_m"]+1e-4 for r in direction),
        "interpretation_clues":[
            "A no_signal-only trigger misses expensive direction outcomes; inspect remaining geometry/cover cost after either response.",
            "Illustrative observable gates are cell_count>=20, MEC radius>=100m, or feasible cover bound minus mandatory approach cost>=200s. These describe workload subsets, are not validated performance policies, and do not imply another measurement pays for itself.",
            "A radius<=19.999m already admits certified one-clear continuation; small2..9-cell regions often have costs dominated by approaching them, not uncertainty.",
            "Current large no_signal bounds exceed actual mean cost by~308s. Do not treat an upper-bound difference between approximate routes as certified positive information value.",
            "After no_signal there is only one positive station here. Its positive-station convex hull is a singleton, so guaranteed-visible new informative sensing is not supplied by that convexity certificate. A speculative second measurement needs explicit no_signal continuation and budget/fallback limits.",
            "Pre-measurement predicted no_signal is a finite-grid heuristic, not calibrated probability. Small predicted no_signal can coexist with a large conditional-direction radius bound and an expensive wide posterior."],
        "rows":rows}
    out=ROOT/"results/next_iteration_review/p4_state_diagnostics.json"
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    print(json.dumps({"output":str(out),"by_outcome":report["by_outcome"],
        "expensive_direction_examples":[{k:r[k] for k in ("case_index","channel","post_measure_clear_s","clear_attempts","plan","after_local_measurement","at_choice")} for r in report["expensive_direction_examples"][:3]]},ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
