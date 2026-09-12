"""Public fixed-neighbor witnesses against two exact synthetic error formulas.

Only preregistered survey neighbor probes and their first observed anchors are
used. This excludes neither arbitrary spatial correlation nor general bounded
errors: it tests exact +/-1 errors and FixedErrorField correlated scale=150.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal
import hashlib
import importlib.util
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("radius_witness", ROOT/"scripts/audit_radius_extremes.py")
radius = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(radius)
VERSION = "fixed-survey-neighbors-exact-error-formulas-v1"
QUANTIZATION_PLUS_GUARD_DEG = .0102
CORRELATION_LENGTH_M = 150.


def angle_wrap(angle):
    return (angle+180.) % 360. - 180.


def assess_pair(polygon, first, second):
    """Bound bearing change using the public outer set, then test exact errors."""
    if first.get("outcome") != "direction" or second.get("outcome") != "direction":
        return {"status": "not_two_direction_readouts", "witnesses": []}
    a, b = first["position"], second["position"]
    lower_a, _ = radius.conservative_distance_bounds(a, polygon)
    lower_b, _ = radius.conservative_distance_bounds(b, polygon)
    # Treat the other measurement point as a singleton for the same outward
    # numerical enclosure. r is a lower bound; separation_upper is an upper.
    _, separation_upper = radius.conservative_distance_bounds(a, [list(map(float, radius.point(b)))])
    actual_separation = math.dist(list(map(float, radius.point(a))), list(map(float, radius.point(b))))
    result = {"status": "evaluated", "first": first, "second": second,
              "actual_separation_m": actual_separation, "separation_upper_bound_m": str(separation_upper),
              "source_distance_lower_bound_m": str(min(lower_a, lower_b)),
              "quantization_plus_numerical_guard_deg": QUANTIZATION_PLUS_GUARD_DEG,
              "witnesses": []}
    if actual_separation > 2.:
        result["status"] = "outside_predeclared_2m_neighborhood"
        return result
    lower_r = math.nextafter(float(min(lower_a, lower_b)), -math.inf)
    upper_b = math.nextafter(float(separation_upper), math.inf)
    if lower_r <= upper_b/2:
        result["status"] = "outer_hull_too_close_for_informative_angle_bound"
        return result
    bearing_bound = 2*math.degrees(math.asin(upper_b/(2*lower_r)))
    report_delta = angle_wrap(second["reported_angle_deg"]-first["reported_angle_deg"])
    distance_to_extreme_set = min(abs(angle_wrap(report_delta-v)) for v in (0., 2., -2.))
    lipschitz = math.nextafter(math.hypot(.55, .45)/CORRELATION_LENGTH_M, math.inf)
    extreme_bound = bearing_bound+QUANTIZATION_PLUS_GUARD_DEG
    correlated_bound = bearing_bound+lipschitz*upper_b+QUANTIZATION_PLUS_GUARD_DEG
    result.update(true_bearing_change_upper_deg=bearing_bound, reported_circular_delta_deg=report_delta,
                  distance_to_extreme_difference_set_deg=distance_to_extreme_set,
                  extreme_difference_set_deg=[0, 2, -2], extreme_allowed_distance_deg=extreme_bound,
                  correlated150_gradient_bound_deg_per_m=lipschitz,
                  correlated150_allowed_absolute_delta_deg=correlated_bound)
    if distance_to_extreme_set > extreme_bound:
        result["witnesses"].append({"excludes_exact_model": "extreme_errors_exactly_minus1_or_plus1_deg",
                                     "guarded_excess_deg": distance_to_extreme_set-extreme_bound})
    if abs(report_delta) > correlated_bound:
        result["witnesses"].append({"excludes_exact_model": "FixedErrorField_correlated_scale150",
                                     "guarded_excess_deg": abs(report_delta)-correlated_bound})
    return result


def audit_case(archive, request_rows, decisions, assignment):
    if assignment.get("protocol") != "survey":
        raise ValueError("Neighbor audit is restricted to the preregistered survey design")
    # Reuse the already-tested public archive integrity and removal logic.
    if archive.get("status") != "archived" or archive.get("issues") or abs(archive.get("epsilon_deg", 0)-1.0051) > 1e-12:
        raise ValueError("Unusable archive or changed angular outer bound")
    observations, present, _, after_clear = radius.active_observations(request_rows)
    plans = [d for d in decisions if d.get("event") == "survey_plan"]
    if len(plans) != 1:
        raise ValueError("Expected exactly one frozen survey plan")
    plan = plans[0]
    pairs, witnesses = [], []
    for target in plan["plan"]:
        channel = target["channel"]
        if channel not in present:
            raise ValueError("Selected survey channel was never confirmed present")
        state = archive["channels"][str(channel)]
        polygon = state["position_outer_polygon"]
        if not polygon or state.get("position_outer_empty"):
            raise ValueError("No conservative outer set for the selected channel")
        anchor = target["reference"]["anchor_position"]
        for probe in target["probes"]:
            if probe["kind"] != "neighbor":
                continue
            logged = [d for d in decisions if d.get("event") == "measure" and d.get("phase") == "survey"
                      and d.get("station_id") == probe["station_id"] and d.get("target_channel") == channel]
            if len(logged) != 1 or not logged[0].get("request_id"):
                raise ValueError("Neighbor does not have a unique logged confirmed request ID")
            second = next((o for o in observations[channel] if o["request_id"] == logged[0]["request_id"]), None)
            if second is None:
                raise ValueError("Neighbor request is not an active accepted measurement")
            if radius.point(second["position"]) != radius.point(probe["position"]):
                raise ValueError("Actual neighbor point differs from the frozen plan")
            anchors = [o for o in observations[channel] if o["row_index"] < second["row_index"]
                       and radius.point(o["position"]) == radius.point(anchor)]
            if not anchors:
                raise ValueError("No prior accepted anchor observation")
            first = anchors[0]
            pair = assess_pair(polygon, first, second)
            pair.update(channel=channel, station_id=probe["station_id"], plan_index=probe["plan_index"],
                        position_outer_polygon=polygon, pair_selection="each frozen neighbor probe versus earliest prior accepted anchor")
            pairs.append(pair)
            for witness in pair["witnesses"]:
                witnesses.append({"case_id": assignment["case_id"], "problem": assignment["problem"],
                                  "protocol": assignment["protocol"], "split": assignment["split"],
                                  "channel": channel, "station_id": probe["station_id"], **witness,
                                  "first_request_id": first["request_id"], "second_request_id": second["request_id"],
                                  "pair_certificate": pair})
    return {"case_id": assignment["case_id"], "problem": assignment["problem"], "protocol": assignment["protocol"],
            "split": assignment["split"], "pairs": pairs, "witnesses": witnesses,
            "ignored_post_clear_actions": after_clear, "plan_sha256_from_log": plan["plan_sha256"]}


def run(round_dir, output):
    round_dir, output = Path(round_dir), Path(output)
    plan_path = round_dir/"plan.json"
    plan = radius.load(plan_path)
    cases, errors, witnesses = [], [], []
    for assignment in plan["cases"]:
        if assignment["protocol"] != "survey":
            continue
        case_id = assignment["case_id"]
        try:
            folder = round_dir/"cases"/case_id
            paths = {"assignment": folder/"assignment.json", "requests": folder/"requests.jsonl",
                     "decisions": folder/"decisions.jsonl", "archive": round_dir/"posterior_archives"/case_id/"posterior_archive.json"}
            actual, archive = radius.load(paths["assignment"]), radius.load(paths["archive"])
            for key in ("case_id", "problem", "protocol", "split"):
                if actual.get(key) != assignment.get(key):
                    raise ValueError("Assignment mismatch")
            if archive.get("case_id") != case_id or archive.get("problem") != assignment["problem"]:
                raise ValueError("Archive metadata mismatch")
            if archive["requests_sha256"] != radius.sha(paths["requests"]):
                raise ValueError("Archive input SHA256 mismatch")
            read_rows = lambda p: [json.loads(line) for line in p.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
            result = audit_case(archive, read_rows(paths["requests"]), read_rows(paths["decisions"]), assignment)
            result["source_sha256"] = {key: radius.sha(value) for key, value in paths.items()}
            radius.save(output/"cases"/(case_id+".json"), result)
            cases.append(result)
            witnesses.extend(result["witnesses"])
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append({"case_id": case_id, "error": f"{type(exc).__name__}: {exc}"})
    groups = []
    for problem in (3, 4):
        for split in ("fit", "development"):
            chosen = [r for r in cases if r["problem"] == problem and r["split"] == split]
            entries = [w for r in chosen for w in r["witnesses"]]
            hypotheses = {}
            for model in ("extreme_errors_exactly_minus1_or_plus1_deg", "FixedErrorField_correlated_scale150"):
                selected = [w for w in entries if w["excludes_exact_model"] == model]
                hypotheses[model] = {"status": "logically_excluded_by_public_observation" if selected else "no_witness_not_validated",
                                     "witness_count": len(selected), "witness_cases": sorted({w["case_id"] for w in selected}),
                                     "first_witness": selected[0] if selected else None}
            groups.append({"problem": problem, "protocol": "survey", "split": split, "cases": len(chosen),
                           "planned_pairs": sum(len(r["pairs"]) for r in chosen),
                           "evaluated_pairs": sum(p["status"] == "evaluated" for r in chosen for p in r["pairs"]),
                           "hypotheses": hypotheses})
    result = {"version": VERSION, "audited_cases": len(cases), "errors": errors,
              "pair_selection": "all preregistered survey neighbor probes, earliest prior accepted anchor; b<=2m; no angular-value selection",
              "plan_sha256": radius.sha(plan_path), "script_sha256": radius.sha(__file__),
              "distance_helper_sha256": radius.sha(ROOT/"scripts/audit_radius_extremes.py"),
              "tested_formula_source_sha256": radius.sha(ROOT/"src/bsolver/simulator.py"),
              "epsilon_deg": 1.0051, "quantization_plus_numerical_guard_deg": QUANTIZATION_PLUS_GUARD_DEG,
              "groups": groups, "witness_count": len(witnesses),
              "limits": "Only exact +/-1 or the documented scale150 smooth formula excluded; arbitrary spatial correlation and continuous bounded errors remain unidentified"}
    radius.save(output/"summary.json", result)
    (output/"witnesses.jsonl").write_text("".join(json.dumps(w, ensure_ascii=False, allow_nan=False)+"\n" for w in witnesses), encoding="utf-8")
    text = ["# 预注册邻点的精确误差模型见证", "", f"核查 {len(cases)} 局标准调查；错误 {len(errors)}。只使用固定 neighbor 探针及其先前最早测量的锚点，b≤2 m；缺少双示向的探针对保守结论不贡献见证。", "",
            "|题目|划分|局数|可评估/计划点对|精确±1见证局/条|smooth150见证局/条|", "|---|---|---:|---:|---:|---:|"]
    for g in groups:
        a, b = (g["hypotheses"][m] for m in ("extreme_errors_exactly_minus1_or_plus1_deg", "FixedErrorField_correlated_scale150"))
        text.append(f"|P{g['problem']}|{g['split']}|{g['cases']}|{g['evaluated_pairs']}/{g['planned_pairs']}|{len(a['witness_cases'])}/{a['witness_count']}|{len(b['witness_cases'])}/{b['witness_count']}|")
    text += ["", "推导、量化护栏与结论适用范围见 docs/model_witnesses.md。每个证书保留两次accepted请求ID、真实测点、报告角差、外包、距离下界、几何角度上界与剩余超界幅度。提交清除点没有被当作源坐标。", "",
             "只有实际出现见证时，才说明相应精确公式无法解释公开局内观测；0见证保持未验证、未排除。本核查不排除任意空间相关性，也不估计真实传感器误差。原统计检查的 insufficient_evidence 状态保持原义。"]
    (output/"report.md").write_text("\n".join(text)+"\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.round, args.output)
    print(json.dumps({"audited_cases": result["audited_cases"], "errors": len(result["errors"]), "witness_count": result["witness_count"]}))
    return int(bool(result["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
