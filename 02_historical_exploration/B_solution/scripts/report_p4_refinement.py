"""Summarize completed, audited local refinement; no policy selection or execution."""
from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path
import statistics
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bsolver.round_experiments import bootstrap_mean_ci
from bsolver.refined_coverage import refined_coverage_certificate

DATA = ROOT / "results/p4_refinement"
OUT = ROOT / "results/p4_refinement_diagnostics"
POOL = {"calibrated": "校准设计池", "broad": "广覆盖合法池", "stress": "边界压力池"}
ARM = {"current": "上一轮P4候选", "coverage": "仅25站覆盖", "local_cover": "仅当前区域光学覆盖", "combined_cover": "两项组合"}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def mean(rows, field):
    return statistics.fmean(r[field] for r in rows)


def main():
    audits = {name: read(OUT / (name + "_audit.json")) for name in ("smoke", "development", "confirmation")}
    assert all(a["status"] == "passed" and a["issue_count"] == 0 for a in audits.values())
    stages = {name: read(DATA / name / "results.json") for name in ("development", "confirmation")}
    meta = {name: read(DATA / name / "run_metadata.json") for name in stages}
    assert all(meta[s]["recorded_runs"] == meta[s]["expected_runs"] and meta[s]["error"] is None for s in stages)
    selection = read(DATA / "selection.json")
    regression_path = OUT / "development_regressions.json"
    regression = read(regression_path)
    regression_audit = regression["journal_audit_summary"]
    assert regression_audit["selected_runs_audited"] == 12
    assert regression_audit["all_source_and_journal_hash_checks_passed"]
    assert regression_audit["all_reconstructed_joint_deferrals_match_800m_rule"]
    assert not regression_audit["confirmation_results_opened"]
    primary_pairs = read(DATA / "confirmation/pool_paired_summary.json")
    assert selection["selected_variants"] == ["combined_cover", "coverage"]
    tests = ET.parse(OUT / "tests.xml").getroot()
    suites = list(tests.iter("testsuite"))
    tests_info = {k: sum(int(s.get(k, "0")) for s in suites) for k in ("tests", "failures", "errors", "skipped")}
    assert tests_info["failures"] == tests_info["errors"] == 0
    grouped = []
    for stage, rows in stages.items():
        for pool in POOL:
            for arm in ("current", "coverage", "local_cover", "combined_cover"):
                subset = [r for r in rows if r["pool"] == pool and r["variant"] == arm]
                if not subset:
                    continue
                assert all(r["evaluation_complete"] for r in subset)
                record = dict(partition=stage, pool=pool, variant=arm, n=len(subset), complete=len(subset))
                for field in ("total_virtual_time_s", "average_clear_time_s", "walk_distance_m", "measures",
                              "switches", "clear_attempts", "clear_successes", "failed_clear_attempts",
                              "post_clear_stop_tail_s", "fallback_targets", "program_real_time_s"):
                    record[field] = mean(subset, field)
                record["max_virtual_time_s"] = max(r["total_virtual_time_s"] for r in subset)
                record["max_program_real_time_s"] = max(r["program_real_time_s"] for r in subset)
                record["average_clear_ge900_count"] = sum(r["average_clear_time_s"] >= 900 for r in subset)
                record["average_clear_ge1000_count"] = sum(r["average_clear_time_s"] >= 1000 for r in subset)
                grouped.append(record)
    contrasts, worst = [], []
    for pool in POOL:
        rows = [r for r in stages["confirmation"] if r["pool"] == pool]
        index = {(r["case_id"], r["variant"]): r for r in rows}
        combined = [r for r in rows if r["variant"] == "combined_cover"]
        for comparator in ("current", "coverage"):
            deltas = [r["total_virtual_time_s"]-index[r["case_id"], comparator]["total_virtual_time_s"] for r in combined]
            if comparator == "current":
                primary = next(p for p in primary_pairs if p["pool"] == pool and p["variant"] == "combined_cover")
                assert abs(primary["completed_delta_mean_s"]-statistics.fmean(deltas)) < 1e-8
                interval = primary["penalized_delta_ci95_s"]
                interval_method = "Exact original preregistered runner interval; no replacement seed"
            else:
                interval = bootstrap_mean_ci(deltas, seed=91731+len(contrasts), draws=2000)
                interval_method = f"Explanatory component contrast; paired bootstrap 2000 draws, seed {91731+len(contrasts)}"
            contrasts.append(dict(pool=pool, comparator=comparator, n=len(deltas),
                mean_delta_s=statistics.fmean(deltas), ci95_s=interval, ci_method=interval_method,
                regression_count=sum(d > 0 for d in deltas), worst_regression_s=max(deltas),
                interpretation="Paired synthetic design contrast; no official-population or multiplicity-adjusted claim"))
        ranked = sorted(combined, key=lambda r: r["total_virtual_time_s"]-index[r["case_id"], "current"]["total_virtual_time_s"], reverse=True)
        for r in ranked[:3]:
            current = index[r["case_id"], "current"]
            worst.append(dict(pool=pool, case_id=r["case_id"], n=r["n"], n_directed=r["n_directed"],
                layout=r["layout"], current_time_s=current["total_virtual_time_s"], combined_time_s=r["total_virtual_time_s"],
                delta_s=r["total_virtual_time_s"]-current["total_virtual_time_s"],
                move_delta_m=r["walk_distance_m"]-current["walk_distance_m"],
                measure_delta=r["measures"]-current["measures"],
                failed_clear_delta=r["failed_clear_attempts"]-current["failed_clear_attempts"],
                log_directory=f"results/p4_refinement/confirmation/runs/{pool}/{r['case_id']}"))
    source_paths = [DATA / "manifest.json", DATA / "source_freeze.json", DATA / "selection.json"]
    source_paths += [DATA / stage / "results.json" for stage in stages]
    source_paths += [DATA / "confirmation/pool_paired_summary.json"]
    source_paths += [regression_path]
    payload = dict(environment="local_synthetic", official_runs_this_iteration=0, formal_runs_this_iteration=0,
        distinct_main_worlds=1000, main_runs=sum(len(v) for v in stages.values()), smoke_worlds=6, smoke_runs=24,
        all_complete=all(r["evaluation_complete"] for rows in stages.values() for r in rows),
        tests=tests_info, selected_variants=selection["selected_variants"], groups=grouped, contrasts=contrasts,
        worst_cases=worst, run_metadata=meta, coverage=refined_coverage_certificate(),
        development_regression_audit=regression_audit,
        audits={name: dict(status=a["status"], counts=a["counts"], issue_count=a["issue_count"]) for name, a in audits.items()},
        sources=[dict(path=p.relative_to(ROOT).as_posix(), sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in source_paths])
    (OUT / "iteration_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# P4第二轮迭代：覆盖路线与当前区域光学覆盖", "",
        "本轮全部为实际执行的本地模拟。官方新增演练0局、正式测试0局。比较基准是上一轮P4候选（NoSignal、L1、joint），不是更早L5基线。", "",
        "## 第一性原理与实际改动", "",
        "未发现频道需要覆盖位置与朝向的完整性证据；已发现目标需要从当前认证外包出发付出尽可能少的剩余清除成本。尾部时间和兜底次数均只作诊断，完整整局虚拟时间仍是优化指标。", "",
        "1. 覆盖：31站/29890.897米改为25站/24095.424米。995米近等边三角格、平移相位3/24与18/24；所有与外扩目标圆盘相交的闭三角形顶点全部保留，整数精确谓词及实际float坐标余量保证连续域可见。静态路线减少19.39%，不声称所有覆盖的全局最优。",
        "2. 局部：L1测量规则保持，用当前外包P重建边长28米光学网格，实际提交中心逐格验证半径小于20米；比较覆盖方向和蛇形/近邻路线。失败仅能剔除整块支持集被一个已失败清除圆覆盖的格。",
        "3. 可选adaptive实现有单元测试，但未纳入本轮批量与性能结论。对无信号仍保留P的候选测点，不能用近似路线界的松紧声称信息有价值。", "",
        "完整推导见[p4_refinement_principles.md](p4_refinement_principles.md)、[覆盖证明](refined_coverage.md)、[局部证明](refined_local.md)。", "",
        "## 预先固定的实验设计", "",
        "200开发世界×4臂=800次运行；开发后按预定规则选出组合与仅覆盖对照，800个独立确认世界×3臂=2400次运行。合计1000个主世界、3200次主运行；另有6世界24次冒烟。各臂共享隐藏环境与固定误差场，真值仅由环境和事后评估器读取。", "",
        "开发要求全清且外包违规为零，并要求每个池的平均惩罚时间不高于current，再按校准池平均时间选择主候选。确认不重新调参，不强行合并三个池为官方预期表现。", "",
        "## 开发消融：平均整局虚拟秒", "",
        "|场景池|每臂世界数|current|仅覆盖|仅光学覆盖|组合|", "|---|---:|---:|---:|---:|---:|"]
    lookup = {(g["partition"], g["pool"], g["variant"]): g for g in grouped}
    for pool in POOL:
        rs = [lookup["development", pool, a] for a in ("current", "coverage", "local_cover", "combined_cover")]
        lines.append(f"|{POOL[pool]}|{rs[0]['n']}|"+"|".join(f"{r['total_virtual_time_s']:.2f}" for r in rs)+"|")
    lines += ["", "## 独立确认：平均整局虚拟秒", "",
        "|场景池|每臂世界数|current|仅覆盖|组合|组合降幅|", "|---|---:|---:|---:|---:|---:|"]
    for pool in POOL:
        rs = [lookup["confirmation", pool, a] for a in ("current", "coverage", "combined_cover")]
        gain = 100*(1-rs[2]["total_virtual_time_s"]/rs[0]["total_virtual_time_s"])
        lines.append(f"|{POOL[pool]}|{rs[0]['n']}|"+"|".join(f"{r['total_virtual_time_s']:.2f}" for r in rs)+f"|{gain:.2f}%|")
    lines += ["", "|配对对比：组合减参照|场景池|平均差值秒|点态95%区间|退步例数|最坏退步秒|",
              "|---|---|---:|---|---:|---:|"]
    for r in contrasts:
        lines.append(f"|{ARM[r['comparator']]}|{POOL[r['pool']]}|{r['mean_delta_s']:.2f}|[{r['ci95_s'][0]:.2f}, {r['ci95_s'][1]:.2f}]|{r['regression_count']}/{r['n']}|{r['worst_regression_s']:.2f}|")
    lines += ["", "区间来自固定本地分层设计内的配对bootstrap；不是官方总体置信区间，未作多重比较校正。组合相对仅覆盖的差值用于解释局部组件的增益。", "",
              "## 确认成本与平均定位清除时间", "",
              "|池/策略|平均T/N秒|移动米|检测|换频|失败清除|尾部秒|程序现实秒|",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for pool in POOL:
        for arm in ("current", "coverage", "combined_cover"):
            r=lookup["confirmation",pool,arm]
            keys=("average_clear_time_s","walk_distance_m","measures","switches","failed_clear_attempts","post_clear_stop_tail_s","program_real_time_s")
            lines.append(f"|{POOL[pool]}/{ARM[arm]}|"+"|".join(f"{r[k]:.2f}" for k in keys)+"|")
    lines += ["", "|场景池|current每源≥900秒|组合每源≥900秒|current每源≥1000秒|组合每源≥1000秒|",
              "|---|---:|---:|---:|---:|"]
    for pool in POOL:
        a,b=lookup["confirmation",pool,"current"],lookup["confirmation",pool,"combined_cover"]
        lines.append(f"|{POOL[pool]}|{a['average_clear_ge900_count']}/{a['n']}|{b['average_clear_ge900_count']}/{b['n']}|{a['average_clear_ge1000_count']}/{a['n']}|{b['average_clear_ge1000_count']}/{b['n']}|")
    lines += ["", "## 退步案例保留", "",
              "每池列组合相对current最差的三例。正差值表示退步；不因平均改善而删除。", "",
              "|案例|N/定向|current秒|组合秒|差值秒|移动增量米|检测增量|失败清除增量|",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in worst:
        lines.append(f"|{r['case_id']}|{r['n']}/{r['n_directed']}|{r['current_time_s']:.2f}|{r['combined_time_s']:.2f}|{r['delta_s']:.2f}|{r['move_delta_m']:.2f}|{r['measure_delta']}|{r['failed_clear_delta']}|")
    lines += ["", "## 退步机制与下一轮因素", "",
        "另对开发集每池最差世界的四臂日志进行了逐动作重建，共12次运行、3147条已接受的检测/清除动作；时间残差不超过3.46e-11秒，文件散列、可见性和800米延期规则均吻合。它们是事后选择的极端案例，用于解释机制，不是新的验证样本。", "",
        "1. 完整路线短不意味着更早完成：开发合法池最差例旧策略访问16/31站便清除16源退出，新策略访问25/25站；压力池相应为15/31和25/25。路线同时改变了首次发现、局部测点与清除顺序。",
        "2. 处理顺序存在可改进之处：开发压力最差例，频道3在新路线第18站已被发现，随后成为唯一未清除目标；公开外包计算的顺路额外距离约1285.66—3098.58米，始终超过800米固定阈值，因而被推迟到全路线扫描结束。该行为符合现有程序，但未充分利用已发现16个不同频道的信息。",
        "3. 覆盖格数少不意味着首次命中早：同一新路线下，该压力例频道1的追加测量均为no_signal。旧110格方案第16次尝试命中，新80格方案第66次才命中。优化完整覆盖成本上界仍不能逐案例保证首次命中更快。", "",
        "下一轮应将处理顺序作为独立因素：已得到16个不同频道的阳性证据后，优先清理这16个目标，仍必须实际获得16次成功清除才能退出；其他状态比较立即服务与继续扫描的剩余时间，而不只用固定800米阈值。随后单独研究光学搜索的首次命中顺序。两项尚未实现或验证，不能算作本轮收益。任何经验优先级均不得删减最终覆盖与清除证据。", "",
        "细节与原始日志索引见[开发退步诊断JSON](../results/p4_refinement_diagnostics/development_regressions.json)，可运行 `scripts/diagnose_p4_regressions.py` 复现。", "",
        "![开发消融](../results/p4_refinement_diagnostics/development_ablation.png)", "",
        "![独立确认配对差值](../results/p4_refinement_diagnostics/confirmation_paired_effects.png)"]
    lines += ["", "## 验证、限制与复现", "",
        f"全部{payload['main_runs']}次主运行完整清除；另24次冒烟完整清除。{tests_info['tests']}项完整测试通过。三份独立审计均为0问题，按原始请求检查了计时、频道、位置、成功数、覆盖点与逐频道停止索引、外包真值包含及文件散列。", "",
        f"开发使用{meta['development']['workers']}个进程，墙钟{meta['development']['wall_time_s']:.2f}秒；确认使用{meta['confirmation']['workers']}个进程，墙钟{meta['confirmation']['wall_time_s']:.2f}秒。程序现实时间受并发与机器负载影响，不和官方程序时间直接比较。", "",
        "经验性收益目前限于这些本地相容环境与压力场景。不能把本轮降幅直接加到先前官方6.23%上，也不能据此填写新官方成绩。完整性证明与浮点余量针对指定物理/误差模型；现实时间和网络故障仍需要运行时预算管理。", "",
        "下一步应冻结入选方案，用新的官方独立案例随机交错比较current和组合；如收益不迁移，检查可见性、测向收缩、搜索轨迹和清除成本的具体失配，不直接扩大参数搜索。正式测试继续保留原授权边界。", "",
        "复现命令见[p4_refinement_reproduce.md](p4_refinement_reproduce.md)。全部主数据位于 `results/p4_refinement`，审计、图及综合JSON位于 `results/p4_refinement_diagnostics`。"]
    (ROOT / "docs/p4_refinement_report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(json.dumps(dict(main_runs=payload["main_runs"], tests=tests_info, group_count=len(grouped),
                         contrasts=contrasts, report="docs/p4_refinement_report.md"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
