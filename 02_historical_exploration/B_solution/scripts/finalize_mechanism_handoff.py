"""Freeze a separate post-screen handoff, preserving all original checks.

The screen combines public geometric contradictions and an explicitly
post-screen, repeated protocol discrepancy. No original model/assessment is
rewritten and no official endpoint is contacted.
"""
import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_decisions(candidates, radius, neighbor, contrasts):
    if radius.get("errors") or radius.get("audited_cases") != 160:
        raise ValueError("complete, error-free 160-case radius certificate required")
    if neighbor.get("errors") or neighbor.get("audited_cases") != 40:
        raise ValueError("complete, error-free 40-survey neighbor certificate required")
    exclusions = {}
    for hypothesis in ("all_R_1000", "all_R_1500"):
        evidence = [{"problem": group["problem"], "protocol": group.get("protocol"), "split": group["split"],
                     "witness_count": group["hypotheses"][hypothesis]["witness_count"],
                     "witness_cases": group["hypotheses"][hypothesis]["witness_cases"]}
                    for group in radius["groups"] if group["hypotheses"][hypothesis]["status"] == "logically_excluded_by_public_observation"]
        if not evidence:
            raise ValueError(f"required radius exclusion has no public witness: {hypothesis}")
        exclusions[hypothesis] = evidence
    hash_evidence = [r for r in contrasts if r["problem"] == 3 and r["protocol"] == "survey"
                     and r["metric"] == "neighbor_1m_absolute_report_delta_deg"
                     and "radius_mixed__noise_deterministic" in r["model_candidate"]]
    for composition in ("smoothed_joint", "broad_joint"):
        for split in ("fit", "development"):
            match = [r for r in hash_evidence if r["model_candidate"].startswith(composition + "__")
                     and r["official_reference_split"] == split]
            if len(match) != 1 or match[0]["difference_ci95"][0] <= .5:
                raise ValueError("hash screen requires the documented >.5deg discrepancy in both splits and both checked compositions")
    formula_status = {}
    for hypothesis in ("extreme_errors_exactly_minus1_or_plus1_deg", "FixedErrorField_correlated_scale150"):
        formula_status[hypothesis] = [group["hypotheses"][hypothesis]["status"] for group in neighbor["groups"]]
    decisions, retained = [], []
    for candidate in candidates:
        row = copy.deepcopy(candidate)
        radius_mode, error_mode = row["radius_mode"], row["error_mode"]
        decision = {"id": row["id"], "radius_mode": radius_mode, "error_mode": error_mode,
                    "correlation_length": row.get("correlation_length"), "original_statistical_status": row["status"],
                    "retained_for_broad_and_stress": True}
        if radius_mode in ("min", "max"):
            hypothesis = "all_R_1000" if radius_mode == "min" else "all_R_1500"
            decision.update(selection_status="screened_out_by_certified_witness", eligible_for_calibrated_pool=False,
                            hypothesis=hypothesis, witness_groups=exclusions[hypothesis],
                            claim="exact all-source point-mass radius model contradicted; no claim that any continuous radius distribution was recovered")
        elif error_mode == "deterministic":
            decision.update(selection_status="screened_out_by_replicated_protocol_contrast", eligible_for_calibrated_pool=False,
                            diagnostic_contrasts=hash_evidence,
                            claim="tested uniform independent-location hash implementation screened on strict1m joint observable feedback; not a theorem about every hash family or true sensor errors")
        elif radius_mode == "mixed" and error_mode in ("correlated", "extreme") and row.get("correlation_length") == 150.:
            hypothesis = "FixedErrorField_correlated_scale150" if error_mode == "correlated" else "extreme_errors_exactly_minus1_or_plus1_deg"
            if any(s == "logically_excluded_by_public_observation" for s in formula_status[hypothesis]):
                decision.update(selection_status="screened_out_by_certified_witness", eligible_for_calibrated_pool=False,
                                hypothesis=hypothesis, claim="exact error formula contradicted; no claim about all related stochastic families")
            else:
                decision.update(selection_status="retained_unresolved", eligible_for_calibrated_pool=True,
                                hypothesis=hypothesis, formula_audit_statuses=formula_status[hypothesis],
                                claim="no observed contradiction in this finite check; not a validated official generator")
                row.update(status="unresolved", selection_status="retained_after_named_screens",
                           retention_reason="remaining tested mixed-radius formula after endpoint witnesses and the hash protocol screen; still unidentified",
                           official_generator_validated=False,
                           exact_formula_audit="no_witness_not_validated",
                           directly_checked_composition_models=["smoothed_joint"],
                           proposed_composition_cross=["smoothed_joint", "broad_joint"],
                           cross_product_limit="broad+this error formula has no direct same-protocol replication; broad/hash reference only; the new cross remains compound uncertainty for local robustness tests")
                retained.append(row)
        else:
            raise ValueError("unexpected mechanism in the frozen five-candidate input")
        decisions.append(decision)
    return decisions, retained


def finalize(checked, diagnostics, radius_dir, neighbor_dir, official_model, output, *, authoritative=None):
    checked, diagnostics, radius_dir, neighbor_dir, official_model, output = map(Path,
        (checked, diagnostics, radius_dir, neighbor_dir, official_model, output))
    sources = {"original_five_mechanisms": checked / "mechanism_candidates.json",
               "original_six_composition_mechanism_assessments": checked / "mechanism_assessments.json",
               "original_24_statistical_groups": checked / "model_checks.json",
               "post_screen_diagnostic": diagnostics / "diagnostic_contrasts.json",
               "diagnostic_design": diagnostics / "diagnostic_design.json",
               "radius_certificate": radius_dir / "summary.json", "neighbor_formula_certificate": neighbor_dir / "summary.json",
               "frozen_official_composition": official_model}
    if authoritative is not None:
        sources["stage1_authoritative_handoff"] = Path(authoritative)
    before = {name: sha(path) for name, path in sources.items()}
    candidates, radius, neighbor, contrasts = (load(sources[name]) for name in
        ("original_five_mechanisms", "radius_certificate", "neighbor_formula_certificate", "post_screen_diagnostic"))
    checks = load(sources["original_24_statistical_groups"])
    if len(candidates) != 5 or len(checks["candidate_checks"]) != 24:
        raise ValueError("expected frozen five physical mechanisms and 24 original statistical groups")
    decisions, retained = make_decisions(candidates, radius, neighbor, contrasts)
    if {(r["radius_mode"], r["error_mode"], r["correlation_length"]) for r in retained} != {
            ("mixed", "correlated", 150.), ("mixed", "extreme", 150.)}:
        raise ValueError("evidence does not support the explicitly requested two-mechanism handoff")
    output.mkdir(parents=True, exist_ok=True)
    if authoritative is not None:
        candidate_path = Path(authoritative)
        selected = load(candidate_path)
        if {(r["radius_mode"], r["error_mode"], r["correlation_length"]) for r in selected} != {
                (r["radius_mode"], r["error_mode"], r["correlation_length"]) for r in retained}:
            raise ValueError("authoritative dispatch choice differs from the independently screened physical mechanisms")
        retained = selected
    else:
        for row in retained:
            row["screen_evidence_manifest"] = str((output / "mechanism_screening.json").resolve())
        candidate_path = output / "mechanism_candidates_post_screen.json"
        candidate_bytes = json.dumps(retained, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        if candidate_path.exists() and candidate_path.read_bytes() != candidate_bytes:
            raise ValueError("post-screen handoff already frozen with different contents")
        candidate_path.write_bytes(candidate_bytes)
    report = {"version": "post-screen-mechanism-handoff-v1", "frozen_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "source_paths": {name: str(path.resolve()) for name, path in sources.items()}, "source_sha256": before,
              "official_model_id": load(official_model)["model_id"], "original_statistical_groups_unchanged": True,
              "original_physical_assessments_unchanged": True, "retained_count": len(retained),
              "retained_sha256": sha(candidate_path), "decisions": decisions,
              "authoritative_stage1_mechanism_file": str(candidate_path.resolve()),
              "selection_data_usage": "development consumed; post-screen fit feedback replication and public logical witnesses; future independent official round2 is the validation",
              "limits": ["remaining formulas are unresolved, not recovered or validated official mechanisms",
                         "broad composition has direct protocol evidence only with hash; no broad x retained-formula Cartesian validation",
                         "all excluded legal mechanisms remain available to broad/stress robustness pools",
                         "no geometry tolerance or sensing epsilon changed; no composition alpha or cost distribution retuned"]}
    (output / "mechanism_screening.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (output / "stage1_mechanism_reference.json").write_text(json.dumps({"path": str(candidate_path.resolve()),
        "sha256": sha(candidate_path), "role": "authoritative pre-dispatch mechanism selection"}, indent=2), encoding="utf-8")
    if before != {name: sha(path) for name, path in sources.items()}:
        raise RuntimeError("an input changed while freezing the handoff")
    lines = ["# 机制证据筛查与冻结交接", "", "原24组统计状态、6个完整组合评估和5个物理参数评估均保持原样。"
             "新增证据层排除具体参考模型，剩余2组仍标 unresolved，并非官方生成器验证通过。", "",
             "|物理机制|证据层结论|进入筛查后的候选池|", "|---|---|---|"]
    for row in decisions:
        lines.append(f"|{row['id']}|{row['selection_status']}|{row['eligible_for_calibrated_pool']}|")
    lines += ["", "半径端点由公开正观测/无信号与最终保守位置外包的逻辑矛盾排除。独立位置hash实现由严格1m邻点报告变化在fit和development、smoothed和broad两个已检验组合中的重复大偏差筛出；该代理仍含几何变化，不能改称真实误差。", "",
              "40局邻点公式审计没有给出corr150或exact±1的确定性反例；无反例不等于已验证。只有smoothed组成下对这两种机制做过同协议重复；broad×它们是随后本地鲁棒性测试中的复合不确定性，不能声称全部跨积已通过复现。", "",
              "全部筛出但合法的机制仍留在broad/stress压力范围。下一轮独立官方案例承担验收，已经用于筛查的development不再包装为未见测试。", "",
              f"唯一stage1交接文件：{candidate_path.resolve()}；SHA256：{sha(candidate_path)}。"]
    (output / "post_screen_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"path": str(candidate_path.resolve()), "sha256": sha(candidate_path), "retained_count": len(retained),
            "retained_ids": [row["id"] for row in retained], "official_model_sha256": before["frozen_official_composition"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checked", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--radius-witnesses", type=Path, required=True)
    parser.add_argument("--neighbor-witnesses", type=Path, required=True)
    parser.add_argument("--official-model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--authoritative-mechanisms", type=Path, help="Verify and reference the already frozen dispatch choice; do not create a parallel handoff")
    args = parser.parse_args()
    print(json.dumps(finalize(args.checked, args.diagnostics, args.radius_witnesses, args.neighbor_witnesses,
                              args.official_model, args.output, authoritative=args.authoritative_mechanisms), ensure_ascii=False))


if __name__ == "__main__":
    main()
