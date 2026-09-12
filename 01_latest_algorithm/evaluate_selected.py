"""Run the selected P3/P4 policies, optionally paired with the frozen baseline."""
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import argparse,json,statistics,sys
from run import load_cases,run_one,report,write
from selected_policy import selection  # also installs equivalent faster cover calculation

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--workers",type=int,default=6)
    p.add_argument("--no-baseline",action="store_true")
    a=p.parse_args();out=Path(a.output).resolve()
    if out.exists():p.error("请使用新的输出目录，避免覆盖已有实验。")
    config=selection();cases=load_cases(a.dataset);jobs=[]
    for c in cases:
        choice=config["policies"][str(c["problem"])]
        jobs.append((c,choice["arm"],choice["options"]))
        if not a.no_baseline:jobs.append((c,"baseline",{}))
    out.mkdir(parents=True)
    write(out/"plan.json",dict(selection=config,dataset=str(Path(a.dataset).resolve()),cases=cases,
                              paired_baseline=not a.no_baseline,entrypoint="evaluate_selected.py"))
    rows=[]
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        futures=[pool.submit(run_one,c,arm,str(out),None,options) for c,arm,options in jobs]
        for f in as_completed(futures):
            rows.append(f.result())
            if len(rows)%20==0 or len(rows)==len(jobs):
                print(f"完成 {len(rows)}/{len(jobs)}；完整清除 {sum(r['complete'] for r in rows)}",flush=True)
    report(rows,out)
    b={r["world_key"]:r for r in rows if r["arm"]=="baseline"}
    groups=[]
    lines=["# 冻结方案配对结果","","H1、H2分别列出；数据集的物理假设与代表性应按其来源说明解释。","",
           "|题目|误差场|策略|全清|平均任务秒|平均秒/源|相对冻结基线提速|","|---|---|---|---:|---:|---:|---:|"]
    for key in sorted({(r["problem"],r["error_mode"],r["arm"]) for r in rows}):
        g=[r for r in rows if (r["problem"],r["error_mode"],r["arm"])==key]
        pairs=[(r,b[r["world_key"]]) for r in g if r["world_key"] in b]
        valid=len(pairs)==len(g) and all(r["complete"] and base["complete"] for r,base in pairs)
        gain=-100*sum(r["total_virtual_time_s"]-base["total_virtual_time_s"] for r,base in pairs)/sum(base["total_virtual_time_s"] for r,base in pairs) if valid else None
        item=dict(problem=key[0],error_mode=key[1],arm=key[2],runs=len(g),complete=sum(r["complete"] for r in g),
                  mean_T_s=statistics.mean(r["total_virtual_time_s"] for r in g),
                  mean_T_per_source_s=statistics.mean(r["T_per_source_s"] for r in g),gain_pct=gain)
        groups.append(item);h="H1" if key[1]=="correlated" else "H2" if key[1]=="extreme" else key[1]
        display=f"{gain:+.2f}%" if gain is not None else "未计算"
        lines.append(f"|P{key[0]}|{h}|{key[2]}|{item['complete']}/{len(g)}|{item['mean_T_s']:.2f}|{item['mean_T_per_source_s']:.2f}|{display}|")
    write(out/"summary_h1_h2.json",groups)
    (out/"RESULTS.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print("\n".join(lines))
    print("结果目录："+str(out))
if __name__=="__main__":main()
