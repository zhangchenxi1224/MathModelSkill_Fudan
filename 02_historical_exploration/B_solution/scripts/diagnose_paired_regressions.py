"""Post-selection, read-only DEVELOPMENT regression explanations from real logs.

Never opens the stage total results or confirmation directory. Takes a frozen
per-problem choice; preserves each pool's worst selected development pair and
all actual development failures. Optional trajectory plots are post hoc only.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
from bsolver.calibration import unique_accepted_actions  # noqa: E402

BASELINE = "joint_triangular_l5"
VERSION = "development-worst-pairs-actual-journal-v1"
COMPONENTS = ("move_s", "switch_s", "measure_s", "optical_s", "laser_s")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rows_gzip(path):
    with gzip.open(path, "rt", encoding="utf-8-sig") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def position(value):
    if isinstance(value, dict):
        value = (value.get("x"), value.get("y"))
    if not isinstance(value, (tuple, list)) or len(value) != 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in value):
        raise ValueError("Invalid recorded coordinate")
    return tuple(float(v) for v in value)


def normalized_selection(obj):
    if obj.get("selection_basis") != "development_only":
        raise ValueError("Selection must be explicitly frozen from development_only evidence")
    if not obj.get("manifest_sha256"):
        raise ValueError("Frozen selection must identify its scenario manifest SHA256")
    variants = obj.get("selected_variants")
    if variants is None:
        limits = obj.get("selected_local_limits")
        if not isinstance(limits, dict):
            raise ValueError("Expected selected_variants or selected_local_limits")
        variants = {str(k): f"joint_triangular_l{v}" for k, v in limits.items()}
    result = {str(k): v for k, v in variants.items()}
    allowed = {f"joint_triangular_l{n}" for n in (1, 2, 3, 5)}
    if set(result) != {"3", "4"} or any(v not in allowed for v in result.values()):
        raise ValueError("Require one existing stage1 variant per problem")
    return result


def paired_rows(development_rows):
    """Validate pairing before any ranking; no filtering of failed arms."""
    if any(row.get("partition") != "development" for row in development_rows):
        raise ValueError("This helper accepts DEVELOPMENT records only")
    index = {}
    for row in development_rows:
        key = row["case_id"], row["variant"]
        if key in index:
            raise ValueError("Duplicate case/arm")
        index[key] = row
    pairs = []
    for row in development_rows:
        if row["variant"] == BASELINE:
            continue
        baseline = index.get((row["case_id"], BASELINE))
        if baseline is None:
            raise ValueError("Missing actual paired baseline")
        for key in ("scenario_sha256", "error_field_sha256", "policy_core_sha256", "problem", "pool", "partition"):
            if row.get(key) != baseline.get(key):
                raise ValueError(f"Pair differs in {key}")
        pairs.append({"case_id": row["case_id"], "problem": row["problem"], "pool": row["pool"],
                      "variant": row["variant"], "baseline": baseline, "candidate": row,
                      "delta_penalized_s": row["failure_penalized_time_s"]-baseline["failure_penalized_time_s"],
                      "both_complete": bool(row["evaluation_complete"] and baseline["evaluation_complete"])})
    return pairs, index


def select_pairs(development_rows, variants):
    pairs, index = paired_rows(development_rows)
    for (case_id, variant), row in index.items():
        if variant == BASELINE and (case_id, variants[str(row["problem"])]) not in index:
            raise ValueError("The selected arm is missing from the development table")
    groups = defaultdict(list)
    for pair in pairs:
        if pair["variant"] == variants[str(pair["problem"])]:
            groups[pair["pool"], pair["problem"]].append(pair)
    selected, reasons = {}, defaultdict(list)
    for key, group in sorted(groups.items()):
        # Largest signed cost delta, even if every actual pair improved.
        worst = sorted(group, key=lambda p: (-p["delta_penalized_s"], p["case_id"], p["variant"]))[0]
        identity = worst["case_id"], worst["variant"]
        selected[identity] = worst
        reasons[identity].append("largest_selected_development_delta_in_pool_problem")
    for pair in pairs:
        if not pair["both_complete"]:
            identity = pair["case_id"], pair["variant"]
            selected[identity] = pair
            reasons[identity].append("actual_failure_in_candidate_or_paired_baseline")
    # If baseline itself is chosen, there is no nontrivial paired regression.
    # Still keep all actual failures above, including unselected candidate arms.
    return [{**pair, "selection_reasons": reasons[identity]}
            for identity, pair in sorted(selected.items(), key=lambda x: (x[1]["pool"], x[1]["problem"], x[0]))]


def replay_costs(request_rows, decisions):
    """Replay accepted unique actions using the simulator's microsecond tariff."""
    issues = []
    accepted = list(unique_accepted_actions(request_rows, issues))
    if issues:
        raise ValueError("Request audit issues: " + "; ".join(issues))
    current, channel, last_time = (0., 0.), 1, 0.
    totals = {name: 0. for name in COMPONENTS}
    counts = Counter()
    actions = []
    decision_lookup = {}
    for d in decisions:
        if d.get("event") not in ("measure", "clear"):
            continue
        response = d.get("response", {})
        target_channel = d.get("target_channel", (d.get("knowledge") or {}).get("channel", d.get("channel")))
        key = d["event"], target_channel, position(d["position"]), response.get("virtual_time_s")
        if key in decision_lookup:
            raise ValueError("Duplicate accepted decision match key")
        decision_lookup[key] = d
    for row_index, row in accepted:
        if row.get("outcome") not in (None, "accepted"):
            raise ValueError("Client did not confirm an accepted request")
        path, request, response = row.get("path"), row.get("request", {}), row.get("response", {})
        if path not in ("/measure", "/clear"):
            if path not in ("/enter", "/exit"):
                raise ValueError("Unknown accepted action path")
            continue
        target, ch = position(request["position"]), request["channel"]
        movement = math.dist(current, target)
        cost = {name: 0. for name in COMPONENTS}
        cost["move_s"] = round(movement/5*1_000_000)/1_000_000
        counts["walk_distance_m"] += movement
        if path == "/measure":
            cost["switch_s"] = float(ch != channel)
            cost["measure_s"] = 5.
            channel = ch
            counts["measures"] += 1
            counts["switches"] += int(cost["switch_s"])
            outcome = response["measure_result"]
        else:
            cost["optical_s"] = 3.
            cost["laser_s"] = 2.*(response["clear_result"] == "success")
            counts["clear_attempts"] += 1
            counts["clear_successes"] += int(cost["laser_s"] > 0)
            outcome = response["clear_result"]
        decision = decision_lookup.get((path[1:], ch, target, response.get("virtual_time_s")))
        if decision is None:
            raise ValueError("Accepted action has no actual decision-log match")
        reason = decision.get("reason") if path == "/measure" else (decision.get("certificate") or {}).get("type")
        for name in COMPONENTS:
            totals[name] += cost[name]
        virtual = response["virtual_time_s"]
        observed_increment = virtual-last_time
        if abs(observed_increment-sum(cost.values())) > 3e-5:
            raise ValueError("Accepted action time does not reconcile with the fixed local tariff")
        actions.append({"row_index": row_index, "request_id": request.get("request_id"),
                        "event": path[1:], "channel": ch, "from_position": current, "position": target,
                        "outcome": outcome, "reason_or_certificate": reason,
                        "decision_sequence": decision["sequence"], "virtual_time_s": virtual,
                        "observed_increment_s": observed_increment, "component_s": cost})
        current, last_time = target, virtual
    no_signal = Counter(a["reason_or_certificate"] for a in actions if a["event"] == "measure" and a["outcome"] == "no_signal")
    fallback = [a for a in actions if a["event"] == "clear" and a["reason_or_certificate"] == "optical_grid_attempt"]
    stats = {"no_signal_by_reason": dict(no_signal), "fallback_attempts": len(fallback),
             "fallback_channels": sorted({a["channel"] for a in fallback}),
             "failed_fallback_attempts": sum(a["outcome"] != "success" for a in fallback),
             "local_measure_choice_events": sum(d.get("event") == "choose_measurement" for d in decisions),
             "failure_events": [{"sequence": d["sequence"], "error": d.get("error")} for d in decisions if d.get("event") == "failure"]}
    return {"components_s": totals, "counts": dict(counts), "total_replayed_s": sum(totals.values()),
            "last_observed_virtual_s": last_time, "diagnostics": stats, "actions": actions}


def diagnose_pair(pair, local_root):
    result = {k: pair[k] for k in ("case_id", "problem", "pool", "variant", "delta_penalized_s", "both_complete", "selection_reasons")}
    result.update(scenario_sha256=pair["candidate"]["scenario_sha256"], error_field_sha256=pair["candidate"]["error_field_sha256"])
    arms = {}
    for arm in ("baseline", "candidate"):
        expected = pair[arm]
        folder = Path(local_root)/"runs"/pair["pool"]/pair["case_id"]/expected["variant"]
        paths = {"result": folder/"result.json", "requests": folder/"requests.jsonl.gz", "decisions": folder/"decisions.jsonl.gz"}
        actual = load(paths["result"])
        for key in ("case_id", "variant", "partition", "scenario_sha256", "error_field_sha256", "evaluation_complete", "total_virtual_time_s", "failure_penalized_time_s"):
            if actual.get(key) != expected.get(key):
                raise ValueError("Stored run differs from development result table")
        replay = replay_costs(rows_gzip(paths["requests"]), rows_gzip(paths["decisions"]))
        residual = actual["total_virtual_time_s"]-replay["total_replayed_s"]
        if abs(residual) > max(3e-5, len(replay["actions"])*1e-9):
            raise ValueError("Total time does not reconcile with full request journal")
        for field in ("measures", "switches", "clear_attempts", "clear_successes"):
            if replay["counts"].get(field, 0) != actual[field]:
                raise ValueError("Result action count differs from accepted journal")
        arms[arm] = {"variant": expected["variant"], "evaluation_complete": actual["evaluation_complete"],
                     "status": actual["status"], "error": actual.get("error"),
                     "total_virtual_time_s": actual["total_virtual_time_s"],
                     "failure_penalized_time_s": actual["failure_penalized_time_s"],
                     "source_sha256": {k: sha(p) for k, p in paths.items()}, **replay,
                     "rounding_residual_s": residual,
                     "failure_penalty_adjustment_s": actual["failure_penalized_time_s"]-actual["total_virtual_time_s"]}
    result["arms"] = arms
    a, b = arms["baseline"], arms["candidate"]
    result["component_deltas_s"] = {k: b["components_s"][k]-a["components_s"][k] for k in COMPONENTS}
    result["rounding_residual_delta_s"] = b["rounding_residual_s"]-a["rounding_residual_s"]
    result["failure_penalty_adjustment_delta_s"] = b["failure_penalty_adjustment_s"]-a["failure_penalty_adjustment_s"]
    result["delta_observed_virtual_s"] = b["total_virtual_time_s"]-a["total_virtual_time_s"]
    decomposition = sum(result["component_deltas_s"].values())+result["rounding_residual_delta_s"]+result["failure_penalty_adjustment_delta_s"]
    if abs(decomposition-result["delta_penalized_s"]) > 1e-5:
        raise ValueError("Paired penalty delta does not reconcile with components")
    signature = lambda action: (action["event"], action["channel"], action["position"], action["outcome"])
    result["first_action_divergence"] = None
    for index in range(max(len(a["actions"]), len(b["actions"]))):
        old = a["actions"][index] if index < len(a["actions"]) else None
        new = b["actions"][index] if index < len(b["actions"]) else None
        if old is None or new is None or signature(old) != signature(new):
            result["first_action_divergence"] = {"accepted_action_index": index, "baseline": old, "candidate": new}
            break
    per_channel = []
    for channel in sorted({x["channel"] for arm in arms.values() for x in arm["actions"]}):
        row = {"channel": channel}
        for label, arm in arms.items():
            actions = [x for x in arm["actions"] if x["channel"] == channel]
            row[label] = {"action_attached_time_s": sum(sum(x["component_s"].values()) for x in actions),
                          "measures": sum(x["event"] == "measure" for x in actions),
                          "no_signal": sum(x["outcome"] == "no_signal" for x in actions),
                          "fallback_attempts": sum(x["reason_or_certificate"] == "optical_grid_attempt" for x in actions),
                          "failed_clears": sum(x["event"] == "clear" and x["outcome"] != "success" for x in actions)}
        row["attached_time_delta_s"] = row["candidate"]["action_attached_time_s"]-row["baseline"]["action_attached_time_s"]
        per_channel.append(row)
    result["per_channel_action_accounting"] = sorted(per_channel, key=lambda r: (-r["attached_time_delta_s"], r["channel"]))
    result["interpretation"] = "Post hoc selected worst case, not new inference; movement is charged to the destination action, not a causal channel effect"
    return result


def plot_trajectories(entries, output, *, selected_scenes=None):
    """At most two worst selected-development trajectories; no solver invoked."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    # Restrict selection to the deliberately retained worst selected variants.
    ranked = sorted([r for r in entries if "largest_selected_development_delta_in_pool_problem" in r["selection_reasons"]],
                    key=lambda r: (-r["delta_penalized_s"], r["problem"], r["pool"], r["case_id"]))[:2]
    plots = []
    for index, row in enumerate(ranked, 1):
        fig, axes = plt.subplots(1, 2, figsize=(13, 6))
        scene = (selected_scenes or {}).get(row["case_id"])
        if scene is not None and (scene.get("partition") != "development" or scene.get("case_id") != row["case_id"]
                                  or scene.get("scenario_sha256") != row["scenario_sha256"]):
            raise ValueError("Truth overlay must be the explicitly selected DEVELOPMENT case")
        for label, ax in zip(("baseline", "candidate"), axes):
            arm = row["arms"][label]
            points = [(0., 0.)]+[a["position"] for a in arm["actions"]]
            ax.plot([p[0] for p in points], [p[1] for p in points], color="#668ca5", linewidth=.6, alpha=.7)
            for outcome, marker, color in (("no_signal", "x", "#aaa"), ("success", "o", "#158066")):
                chosen = [a["position"] for a in arm["actions"] if a["outcome"] == outcome]
                if chosen:
                    ax.scatter(*zip(*chosen), s=12, marker=marker, c=color, label=outcome)
            if scene is not None:
                sources = [s["position"] for s in scene["sources"]]
                ax.scatter(*zip(*sources), marker="*", c="#c17717", s=50, label="Synthetic source; post-evaluation only")
            ax.set_title(f"{label}: {arm['variant']}\nT={arm['total_virtual_time_s']:.1f}s; completed={arm['evaluation_complete']}")
            ax.set_aspect("equal")
            ax.set_xlabel("East x (m)")
            ax.set_ylabel("North y (m)")
            ax.grid(alpha=.2)
            ax.legend(fontsize=7)
        fig.suptitle(f"Post hoc worst DEVELOPMENT pair | {row['pool']} P{row['problem']} | {row['case_id']}\nCandidate minus L5: {row['delta_penalized_s']:+.1f}s; selection is not fresh statistical evidence", fontsize=11)
        fig.tight_layout(rect=(0, 0, 1, .92))
        path = output/f"trajectory_{index:02d}_{row['case_id']}.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        plots.append(path.name)
    return plots


def run(local_root, selection_path, output, *, plots=False, selected_scenes_path=None):
    local_root, output = Path(local_root), Path(output)
    selection = load(selection_path)
    variants = normalized_selection(selection)
    # Intentionally no generic --results argument: prevent accidentally opening
    # the aggregate file that also contains confirmation outcomes.
    result_path = local_root/"development/results.json"
    development = load(result_path)
    selected = select_pairs(development, variants)
    entries, errors = [], []
    for pair in selected:
        try:
            entries.append(diagnose_pair(pair, local_root))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append({"case_id": pair["case_id"], "variant": pair["variant"], "error": f"{type(exc).__name__}: {exc}"})
    manifest = {"version": VERSION, "selection": selection, "selected_variants": variants,
                "per_problem_choice_status": {problem: "no_changed_variant" if variant == BASELINE else "changed_variant" for problem, variant in variants.items()},
                "input_development_sha256": sha(result_path), "selection_sha256": sha(selection_path), "script_sha256": sha(__file__),
                "development_rows": len(development), "selected_pairs": len(selected), "diagnosed_pairs": len(entries),
                "errors": errors, "plots": [], "confirmation_read": False,
                "selection_rule": "largest signed penalized delta per selected variant/pool/problem; also every actual development failure, including unselected variants",
                "inference": "post hoc explanatory cases, not a fresh sample, CI, or selection criterion"}
    output.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        save(output/"cases"/(entry["case_id"]+"__"+entry["variant"]+".json"), entry)
    if plots:
        scenes = load(selected_scenes_path) if selected_scenes_path else None
        manifest["plots"] = plot_trajectories(entries, output, selected_scenes=scenes)
        if selected_scenes_path:
            manifest["explicit_post_evaluation_scenes_sha256"] = sha(selected_scenes_path)
    save(output/"summary.json", manifest)
    text = ["# 已选方案的局部最差回归与全部实际失败", "",
            "仅打开development总表以及实际入选解释案例的压缩日志。每个pool×题保留已选方案相对L5最大的有符号惩罚成本差；若全组都改进，仍保留改进最小的那局。另保留所有development实际失败，包括未选方案。并未读取confirmation。", "",
            "案例是事后极值，用于解释已发生动作，不构成新的泛化证据。Δ始终是候选减基线。耗时严格拆为移动/频道切换/测量/光学/激光；失败惩罚调整另列，失败短投入不当作快速完成。", "",
            "|pool|题|case/variant|完成 B/C|Δ惩罚秒|Δ移动|Δ切换|Δ测量|Δ光学|Δ激光|", "|---|---|---|---|---:|---:|---:|---:|---:|---:|"]
    details = []
    for row in entries:
        a, b = row["arms"]["baseline"], row["arms"]["candidate"]
        delta = row["component_deltas_s"]
        text.append(f"|{row['pool']}|P{row['problem']}|{row['case_id']} / {row['variant']}|{a['evaluation_complete']}/{b['evaluation_complete']}|{row['delta_penalized_s']:+.3f}|"+"|".join(f"{delta[k]:+.3f}" for k in COMPONENTS)+"|")
        details += ["", f"{row['case_id']}：局部无信号 B/C={a['diagnostics']['no_signal_by_reason'].get('local_active_sensing',0)}/{b['diagnostics']['no_signal_by_reason'].get('local_active_sensing',0)}；光学兜底尝试 B/C={a['diagnostics']['fallback_attempts']}/{b['diagnostics']['fallback_attempts']}，其中失败 {a['diagnostics']['failed_fallback_attempts']}/{b['diagnostics']['failed_fallback_attempts']}。惩罚调整差 {row['failure_penalty_adjustment_delta_s']:+.3f} s。", ""]
    text += [""]+details
    text += [f"解释成功 {len(entries)}/{len(selected)} 对，日志/计时错误 {len(errors)}；错误原样保留在summary.json。", "",
             "逐局JSON保存首次动作分歧、所有accepted动作及各成本、按频道归账的动作差、no_signal理由、兜底与失败事件以及输入SHA。移动归于其目的动作，只是会计归类，不等于该频道造成移动的因果估计。最多两幅轨迹图也按最大有符号差选择，明确为事后development案例；可选真源星号只来自显式提供的已评估本地场景，绝不进入策略。"]
    (output/"report.md").write_text("\n".join(text)+"\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plots", action="store_true", help="At most two post hoc trajectory PNGs")
    parser.add_argument("--selected-scenes", type=Path, help="Optional case_id->explicit selected development scene JSON; post-evaluation only")
    args = parser.parse_args()
    result = run(args.local, args.selection, args.output, plots=args.plots, selected_scenes_path=args.selected_scenes)
    print(json.dumps({k: result[k] for k in ("development_rows", "selected_pairs", "diagnosed_pairs", "errors", "confirmation_read")}))
    return int(bool(result["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
