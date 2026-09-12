"""Create the local evaluation delivery from completed paired results."""
from pathlib import Path
from datetime import datetime
import json,zipfile
from run import read,write
ROOT=Path(__file__).resolve().parent
def link(path,label):return f"[{label}]({(ROOT/path).resolve().as_posix()})"
def main():
    data={d:read("runs/final_"+d+"/results.json") for d in ["new240","core960"]}
    assert len(data["new240"])==240 and len(data["core960"])==960
    assert all(r["complete"] for rows in data.values() for r in rows)
    assert not ({r["world_key"] for r in data["new240"]}&{r["world_key"] for r in data["core960"]})
    selection=read("selection.json")
    selection.update(status="validated_local_candidates_on_new240_and_old960",evaluated_worlds=1200,all_cleared_worlds=1200,
      evidence={"new240":"reports/final_new240","core960":"reports/final_core960","latest_mainline_comparison":["reports/final_vs_mainline_new240","reports/final_vs_mainline_core960"]},
      limitation="Finite-candidate local lookahead; empirical local simulation results, not global optimality or new official evaluation.")
    write("selection.json",selection)
    stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
    delivery=ROOT/"delivery";delivery.mkdir(exist_ok=True)
    archive=delivery/("all_feedback_ready_"+stamp+".zip")
    stats={d:read("reports/final_"+d+"/paired_summary.json") for d in data}
    mainline={d:read("reports/final_vs_mainline_"+d+"/paired_summary.json") for d in data}
    pretty={"new240":"新240","core960":"旧960"}
    lines=["# 全反馈实验：新240迭代与旧960回归","",
      "本轮推荐 P3 使用局部两步几何前瞻 dp_geometric，P4 使用局部一步三反馈成本评估 three_feedback_fast（位置细分深度3）。新旧数据无重叠，保留候选在全部 **1200/1200 场景完整清除**，最终记录没有几何区域误排。","",
      "按用户指定用途，新240用于迭代，H1为主要参考、H2为敏感性对照；旧960扩大回归范围。数量组成参考官方记录，位置、半径、朝向与误差机制仍保留仿真假设。本报告是本地仿真结果，不作为本轮反复选参后的独立确认，也不宣称全局最优。","",
      "## 与本轮冻结侧线基线比较","",
      "冻结基线为 p3_r1150_w1 / p4_L2_d1200_w1。提速按同场景平均虚拟任务时间计算；每源时间和完整逐局指标见明细。","",
      "|数据|题目|完整清除|H1提速|H2提速|全组平均任务秒：基线→候选|","|---|---|---:|---:|---:|---:|"]
    for d,rows in data.items():
      for p in (3,4):
        items=[r for r in stats[d] if r["problem"]==p]
        a=next(r for r in items if r["error_mode"]=="correlated")
        b=next(r for r in items if r["error_mode"]=="extreme")
        n=sum(r["runs"] for r in items)
        old=sum(r["baseline_T_s"]*r["runs"] for r in items)/n
        new=sum(r["candidate_T_s"]*r["runs"] for r in items)/n
        lines.append(f'|{pretty[d]}|P{p}|{n}/{n}|{a["gain_pct"]:.2f}%|{b["gain_pct"]:.2f}%|{old:.2f} → {new:.2f}|')
    lines+=["","## 与数据来源目录最新主线发布版本比较","",
      "比较对象为 P3 compact1150、P4 merged25_l1。复用了来源目录已完成的同场景结果，逐世界散列已对应，仿真器与协议文件一致；没有重复运行或改写来源项目。此表与上一表的基线不同。","",
      "|数据|题目|H1提速|H2提速|","|---|---|---:|---:|"]
    for d in data:
      for p in (3,4):
        a=next(r for r in mainline[d] if r["problem"]==p and r["error_mode"]=="correlated")
        b=next(r for r in mainline[d] if r["problem"]==p and r["error_mode"]=="extreme")
        lines.append(f'|{pretty[d]}|P{p}|{a["gain_pct"]:.2f}%|{b["gain_pct"]:.2f}%|')
    lines+=["","## 收益与变慢的来源","",
      "P3 在新240中有117/120场更快。平均增加约19秒检测成本，减少约216秒移动成本，得到约205秒净收益。P4 在新240中有88/120场更快；H1平均少花约271秒移动、39秒检测，多花约14秒清除尝试。两题都通过调整后续行动兑现反馈价值。","",
      "几何信息单独增加时，新240平均收益仅P3约0.07%、P4约0.09%。先期筛选中的概率贪心排序出现明显退步，低概率截断出现漏清，因此没有加入推荐组合。","",
      "|数据|题目|变慢场景数|最大单局变慢/s|","|---|---|---:|---:|"]
    for d in data:
      for p in (3,4):
        items=[r for r in stats[d] if r["problem"]==p]
        lines.append(f'|{pretty[d]}|P{p}|{sum(r["slower"] for r in items)}/{sum(r["runs"] for r in items)}|{max(r["max_regression_s"] for r in items):.2f}|')
    lines+=["","以下为每组最明显的退步案例。时间差分解为移动、检测、换频和清除尝试，均为候选减基线，负值表示省时。","",
      "|数据/题目|案例|总差/s|移动差/s|检测差/s|换频差/s|清除差/s|","|---|---|---:|---:|---:|---:|---:|"]
    for d in data:
      pairs=read("reports/final_"+d+"/paired_cases.json")
      for p in (3,4):
        r=max((r for r in pairs if r["problem"]==p),key=lambda r:r["delta_T_s"])
        lines.append(f'|{pretty[d]}/P{p}|{r["case_id"]}|{r["delta_T_s"]:+.2f}|{r["movement_delta_s"]:+.2f}|{r["measure_delta_s"]:+.2f}|{r["switch_delta_s"]:+.2f}|{r["clear_delta_s"]:+.2f}|')
    lines+=["","P4仍存在个别较大的退步，主要落在额外移动和清除尝试上。工作概率分布与有限视界是近似决策工具，完整清除由保留的几何覆盖和实际清除反馈保障；平均改善并不表示每局都更快。","",
      "## 计算与复现","",
      "推荐组合沿用既有全局搜索站点与动态目标服务顺序。P3前瞻发生在当前目标的局部处理内，P4按三种可能反馈逐步重算成本；没有宣称实现无限制的跨频道全局反馈树。","",
      "单次规划保留有限候选/节点数，紧急计算上限统一放宽到60秒。共34场触及原5秒保护，均按日志标记补跑；未触及原保护的完整搜索直接复用。最终记录注明原始结果、实际采用结果和日志地址。","",
      "本机本轮并行运行中，候选程序平均每局耗时约P3 23秒、P4 43—45秒，高于冻结基线约1—3秒。这里的程序时间是本机观测值；任务提速指虚拟移动/检测/清除时间。","",
      "## 运行与文件","",
      "统一入口自动按题号选择策略，默认同时运行冻结基线。使用新的输出目录：","",chr(96)*3+"powershell",
      "Set-Location -LiteralPath '"+str(ROOT)+"'",
      "D:\\st_python\\python.exe -X utf8 -u evaluate_selected.py --dataset inputs/new240/cases.json --output runs/retest_new240 --workers 6",
      chr(96)*3,"",
      "数据可换成其他JSON、包含场景JSON的ZIP或目录。只跑候选可加 --no-baseline。也可从 selected_policy 导入 make_solver(client, problem) 接入其他评测框架。","",
      "- "+link("README.md","运行说明与组件解释"),
      "- "+link("selection.json","冻结方案参数"),
      "- "+link("reports/final_new240/results.html","新240逐局表")+" / "+link("reports/final_new240/DIFFERENCES.md","新240配对差异"),
      "- "+link("reports/final_core960/results.html","旧960逐局表")+" / "+link("reports/final_core960/DIFFERENCES.md","旧960配对差异"),
      "- "+link("reports/final_vs_mainline_new240/DIFFERENCES.md","新240相对最新主线发布版")+" / "+link("reports/final_vs_mainline_core960/DIFFERENCES.md","旧960相对最新主线发布版"),
      "- "+link("runs/final_new240/provenance.json","新240采用记录")+" / "+link("runs/final_core960/provenance.json","旧960采用记录"),
      "- "+link("delivery/"+archive.name,"脚本、输入数据与结果表轻量包"),"",
      "原始请求/决策日志保存在本项目 runs/ 各实验子目录。轻量包提供完整脚本、输入快照、结果表与历史实验摘要。"]
    report=ROOT/"reports/FINAL_REPORT.md";report.write_text("\n".join(lines)+"\n",encoding="utf-8")
    p=ROOT/"README.md";s=p.read_text(encoding="utf-8")
    s=s.replace("reports/new240_complete/","reports/final_new240/").replace("新 240 原始完整比较","新 240 最终完整比较")
    s=s.replace("reports/new240_vs_mainline_release/","reports/final_vs_mainline_new240/")
    p.write_text(s,encoding="utf-8")
    p=ROOT/"VALIDATION.md";s=p.read_text(encoding="utf-8")
    prefix="> 本文件记录数据集评测之前的初期集成检查。当前完整结论见 reports/FINAL_REPORT.md。\n\n"
    if not s.startswith("> 本文件记录"):p.write_text(prefix+s,encoding="utf-8")
    files=set(ROOT.glob("*.py"))|set(ROOT.glob("*.md"))|{ROOT/"selection.json"}
    for folder in ["feedback","methods","vendor","runtime","configs","inputs","reports","tests"]:
      files.update(p for p in (ROOT/folder).rglob("*") if p.is_file() and not any(x in p.parts for x in ["__pycache__",".pytest_cache"]) and p.suffix not in [".pyc",".prof"])
    with zipfile.ZipFile(archive,"x",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
      for p in sorted(files):z.write(p,p.relative_to(ROOT))
    manifest=dict(worlds=1200,all_complete=True,selection=str(ROOT/"selection.json"),report=str(report),
      archive=str(archive),archive_bytes=archive.stat().st_size,raw_log_root=str(ROOT/"runs"),
      final_results={d:str(ROOT/"reports"/("final_"+d)) for d in data})
    write("delivery/latest.json",manifest)
    print(json.dumps(manifest,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
