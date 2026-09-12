"""Read-only logical witnesses against all-R=1000/1500 point-mass models.

Uses final conservative public location outer polygons, never true sources.
Only accepted observations before that channel's first successful clearance
are eligible. The R=1500 negative-feedback implication is applied only to P3.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal, localcontext
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
from bsolver.calibration import unique_accepted_actions  # noqa: E402

VERSION = "public-outer-hull-radius-point-mass-witness-v1"
GUARD_M = Decimal("0.00001")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def point(value):
    if isinstance(value, dict):
        value = [value.get("x"), value.get("y")]
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("Expected a two-dimensional public point")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or abs(x) > 1e5 for x in value):
        raise ValueError("Invalid or out-of-scope public coordinate")
    return tuple(Decimal.from_float(x) if isinstance(x, float) else Decimal(x) for x in value)


def dot(a, b):
    return a[0]*b[0]+a[1]*b[1]


def sub(a, b):
    return a[0]-b[0], a[1]-b[1]


def cross(a, b):
    return a[0]*b[1]-a[1]*b[0]


def squared_segment_distance(q, a, b):
    edge, delta = sub(b, a), sub(q, a)
    length2 = dot(edge, edge)
    if not length2:
        return dot(delta, delta)
    t = max(Decimal(0), min(Decimal(1), dot(delta, edge)/length2))
    residual = delta[0]-t*edge[0], delta[1]-t*edge[1]
    return dot(residual, residual)


def conservative_distance_bounds(public_point, outer_polygon):
    """Return outward guarded [min distance to P, max distance on P] in metres.

    Decimal precision 70 evaluates the exact stored binary coordinates. The
    final 1e-5 m outward allowance dominates arithmetic roundoff in the bounded
    coordinate scope; the input must already be a conservative convex polygon.
    A lower-dimensional polygon is treated as points/segments, not its line.
    """
    if not outer_polygon:
        raise ValueError("Empty outer polygon cannot support a witness")
    with localcontext() as ctx:
        ctx.prec = 70
        q, vertices = point(public_point), [point(v) for v in outer_polygon]
        maximum2 = max(dot(sub(q, v), sub(q, v)) for v in vertices)
        if len(vertices) == 1:
            minimum2 = maximum2
        else:
            edges = list(zip(vertices, vertices[1:]+vertices[:1]))
            area2 = sum(cross(a, b) for a, b in edges)
            signs = [cross(sub(b, a), sub(q, a)) for a, b in edges]
            inside = bool(area2) and (all(s >= 0 for s in signs) or all(s <= 0 for s in signs))
            minimum2 = Decimal(0) if inside else min(squared_segment_distance(q, a, b) for a, b in edges)
        lower = max(Decimal(0), minimum2.sqrt()-GUARD_M)
        upper = maximum2.sqrt()+GUARD_M
        return lower, upper


def active_observations(request_rows):
    """Replay removal independently; a pre-discovery negative may be eligible."""
    issues, removed, present = [], set(), set()
    observations, clearances = defaultdict(list), defaultdict(list)
    after_clear = 0
    unique = list(unique_accepted_actions(request_rows, issues))
    if issues:
        raise ValueError("Accepted-action audit issues: " + "; ".join(issues))
    for index, row in unique:
        if row.get("outcome") not in (None, "accepted"):
            raise ValueError("Unconfirmed row in accepted journal")
        path, request, response = row.get("path"), row.get("request", {}), row.get("response", {})
        if path not in ("/measure", "/clear"):
            continue
        channel = request.get("channel")
        if isinstance(channel, bool) or not isinstance(channel, int) or not 1 <= channel <= 20:
            raise ValueError("Invalid channel in accepted request")
        position = request.get("position")
        point(position)
        if channel in removed:
            after_clear += 1
            continue
        if path == "/clear":
            if response.get("clear_result") == "success":
                present.add(channel)
                removed.add(channel)
                clearances[channel].append({"request_id": request.get("request_id"), "row_index": index,
                                            "position": position, "radius_m": 20,
                                            "clear_point_is_true_g": False})
            continue
        outcome = response.get("measure_result")
        if outcome not in ("direction", "near", "no_signal"):
            raise ValueError("Invalid accepted measurement result")
        if outcome in ("direction", "near"):
            present.add(channel)
        observations[channel].append({"request_id": request.get("request_id"), "row_index": index,
                                      "position": position, "outcome": outcome,
                                      "reported_angle_deg": response.get("svd_deg") if outcome == "direction" else None,
                                      "before_first_successful_clear": True})
    return observations, present, clearances, after_clear


def audit_case(archive, request_rows, assignment):
    problem = assignment["problem"]
    if problem not in (3, 4) or archive["problem"] != problem:
        raise ValueError("Problem metadata mismatch")
    if archive.get("status") != "archived" or archive.get("issues"):
        raise ValueError("Archive has unresolved audit issues")
    if abs(archive.get("epsilon_deg", 0)-1.0051) > 1e-12:
        raise ValueError("This audit is fixed to the archived conservative epsilon 1.0051 degrees")
    if archive.get("case_id") != assignment.get("case_id"):
        raise ValueError("Case metadata mismatch")
    observations, present, clearances, ignored = active_observations(request_rows)
    channels, witnesses = [], []
    for channel in sorted(present):
        state = archive["channels"][str(channel)]
        if state.get("existence") != "confirmed_present" or state.get("position_outer_empty"):
            raise ValueError("Confirmed source lacks a nonempty archived outer polygon")
        polygon = state["position_outer_polygon"]
        bounds = []
        for obs in observations[channel]:
            lower, upper = conservative_distance_bounds(obs["position"], polygon)
            certificate = {**obs, "distance_lower_bound_m": str(lower), "distance_upper_bound_m": str(upper),
                           "arithmetic_precision_decimal_digits": 70, "outward_guard_m": str(GUARD_M)}
            excluded = None
            if obs["outcome"] in ("direction", "near") and lower > Decimal(1000):
                excluded = "all_R_1000"
                explanation = "positive => R>=||g-s||>=min_{x in P}||x-s||>1000"
                boundary_gap = lower-Decimal(1000)
            elif problem == 3 and obs["outcome"] == "no_signal" and upper < Decimal(1500):
                excluded = "all_R_1500"
                explanation = "P3 present and uncleared no_signal => R<||g-q||<=max_{v in vertices(P)}||v-q||<1500"
                boundary_gap = Decimal(1500)-upper
            certificate["excludes_point_mass"] = excluded
            bounds.append(certificate)
            if excluded:
                witnesses.append({"case_id": assignment["case_id"], "problem": problem,
                                  "protocol": assignment.get("protocol", "unknown"), "split": assignment.get("split", "unknown"),
                                  "channel": channel, **certificate, "logical_implication": explanation,
                                  "guarded_boundary_gap_m": str(boundary_gap),
                                  "assumes_true_source_in_public_outer_polygon": True,
                                  "clearance_points_are_not_true_sources": True})
        channels.append({"channel": channel, "position_outer_polygon": polygon,
                         "successful_clearance_disks": clearances[channel], "observation_bounds": bounds,
                         "joint_feasibility_certified": False})
    return {"version": VERSION, "case_id": assignment["case_id"], "problem": problem,
            "protocol": assignment.get("protocol", "unknown"), "split": assignment.get("split", "unknown"),
            "status": "audited", "confirmed_channels": len(present), "ignored_post_clear_actions": ignored,
            "channels": channels, "witnesses": witnesses,
            "limits": "No witness is not compatibility; R=1500 negatives assessed only in P3; source coordinates are not identified"}


def summary_groups(cases):
    groups = defaultdict(list)
    for case in cases:
        groups[case["problem"], case["protocol"], case["split"]].append(case)
    result = []
    for (problem, protocol, split), rows in sorted(groups.items()):
        hypotheses = {}
        for hypothesis in ("all_R_1000", "all_R_1500"):
            witnesses = [w for row in rows for w in row["witnesses"] if w["excludes_point_mass"] == hypothesis]
            hypotheses[hypothesis] = {"status": "logically_excluded_by_public_observation" if witnesses else
                                     "not_assessed_for_directional_problem" if problem == 4 and hypothesis == "all_R_1500" else
                                     "no_witness_not_validated",
                                     "witness_count": len(witnesses),
                                     "witness_cases": sorted({w["case_id"] for w in witnesses}),
                                     "witness_channel_count": len({(w["case_id"], w["channel"]) for w in witnesses}),
                                     "first_witness": witnesses[0] if witnesses else None}
        result.append({"problem": problem, "protocol": protocol, "split": split,
                       "cases": len(rows), "hypotheses": hypotheses})
    return result


def run(round_dir, output):
    round_dir, output = Path(round_dir), Path(output)
    plan_path = round_dir/"plan.json"
    plan = load(plan_path)
    cases, errors, witnesses = [], [], []
    for planned in plan["cases"]:
        case_id = planned["case_id"]
        try:
            folder = round_dir/"cases"/case_id
            assignment_path, requests_path = folder/"assignment.json", folder/"requests.jsonl"
            archive_path = round_dir/"posterior_archives"/case_id/"posterior_archive.json"
            assignment, archive = load(assignment_path), load(archive_path)
            for key in ("case_id", "problem", "protocol", "split"):
                if assignment.get(key) != planned.get(key):
                    raise ValueError(f"Assignment differs from frozen plan: {key}")
            if archive["requests_sha256"] != sha(requests_path):
                raise ValueError("Request journal differs from archive source SHA256")
            request_rows = [json.loads(line) for line in requests_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
            result = audit_case(archive, request_rows, assignment)
            result["source_sha256"] = {"assignment": sha(assignment_path), "requests": sha(requests_path), "archive": sha(archive_path)}
            save(output/"cases"/(case_id+".json"), result)
            cases.append(result)
            witnesses.extend(result["witnesses"])
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append({"case_id": case_id, "error": f"{type(exc).__name__}: {exc}"})
    summary = {"version": VERSION, "plan_sha256": sha(plan_path), "script_sha256": sha(__file__),
               "planned_cases": len(plan["cases"]), "audited_cases": len(cases), "errors": errors,
               "epsilon_deg": 1.0051, "outward_guard_m": str(GUARD_M),
               "source": "public accepted requests and conservative archived outer polygons, no hidden source state",
               "inference": "deterministic contradiction of exact point-mass radii; not a statistical rejection test or fitted radius distribution",
               "groups": summary_groups(cases), "witness_count": len(witnesses),
               "ignored_post_clear_actions": sum(case["ignored_post_clear_actions"] for case in cases)}
    save(output/"summary.json", summary)
    output.mkdir(parents=True, exist_ok=True)
    (output/"witnesses.jsonl").write_text("".join(json.dumps(w, ensure_ascii=False, allow_nan=False)+"\n" for w in witnesses), encoding="utf-8")
    notes = ["# 接收半径端点模型的公开几何证据", "", f"完整核查 {len(cases)}/{len(plan['cases'])} 局，错误 {len(errors)}。角度外包参数保持 1.0051°，70 位 Decimal 计算，最终距离界再向外留 0.00001 m 护栏。", "",
             "令最终保守外包为 P，真源满足 g∈P。每个阳性测点 s 必有 R≥||g-s||≥dist(s,P)；若保守下界仍大于1000，则该频道不可能恰为R=1000。问题3的未清除源若在q返回无信号，则 R<||g-q||≤max_{v∈vertices(P)}||v-q||；保守上界仍小于1500时，R=1500不可能。", "",
             "这只排除“所有源半径恰为该端点”的点质量模型。无证据不等于该模型可行，也没有识别连续半径分布。问题4的无信号可能来自辐射半平面，因此不套用第二条。存在性可由同局后续阳性/成功清除确认，但任何首次成功清除之后的反馈均排除。清除成功只提供20 m圆盘，提交点从未当成真坐标。", "",
             "这些是条件于题设可见性规则、保守外包和日志真实性的逻辑见证，不需要多重统计检验，也不修改原机制检查中的小样本状态。fit与development逐层分开列出；不能把两者再次包装成全新独立验证。", "",
             "|题目|协议|划分|局数|R=1000见证局/条|R=1500见证局/条|", "|---|---|---|---:|---:|---:|"]
    for group in summary["groups"]:
        a, b = (group["hypotheses"][h] for h in ("all_R_1000", "all_R_1500"))
        notes.append(f"|P{group['problem']}|{group['protocol']}|{group['split']}|{group['cases']}|{len(a['witness_cases'])}/{a['witness_count']}|{len(b['witness_cases'])}/{b['witness_count']}|")
    notes += ["", "summary.json 给每层首个原序见证；witnesses.jsonl 保存全部频道/请求见证。cases/*.json 同时保留所有合格观测界、最终外包、成功清除圆及来源SHA256，便于独立复算。"]
    (output/"report.md").write_text("\n".join(notes)+"\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.round, args.output)
    print(json.dumps({"audited_cases": result["audited_cases"], "errors": len(result["errors"]),
                      "witness_count": result["witness_count"], "output": str(args.output)}, ensure_ascii=False))
    return int(bool(result["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
