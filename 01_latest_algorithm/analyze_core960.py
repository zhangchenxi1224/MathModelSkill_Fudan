"""Summarize paired experiment outputs without dropping failed runs."""
from pathlib import Path
import argparse, json, statistics, html, collections, sys
sys.stdout.reconfigure(encoding="utf-8")

def read(p): return json.loads(Path(p).read_text(encoding="utf-8-sig"))
def quantile(v,q):
    a=sorted(v); i=(len(a)-1)*q; lo=int(i)
    return a[lo]+(a[min(lo+1,len(a)-1)]-a[lo])*(i-lo)
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--runs",nargs="+",required=True)
    ap.add_argument("--baseline",required=True)
    ap.add_argument("--output",required=True)
    ap.add_argument("--label",default="核心960场景配对评测")
    ap.add_argument("--index",default="inputs/core960/index.json")
    ap.add_argument("--new240",action="store_true")
    args=ap.parse_args()
    rows=[]
    for root in args.runs:
        files=sorted((Path(root)/"cases").glob("*/*/result.json"))
        rows.extend(read(f) for f in files)
    base={r["world_key"]:r for r in (read(f) for f in (Path(args.baseline)/"cases").glob("*/baseline/result.json"))}
    index={r["case_id"]:r for r in read(args.index)}
    seen=set()
    for r in rows:
        key=(r["world_key"],r["arm"])
        if key in seen: raise ValueError("Duplicate run: "+str(key))
        seen.add(key)
        r["source_group"]=index[r["case_id"]]["source_group"]
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    groups=[]
    for problem,arm in sorted({(r["problem"],r["arm"]) for r in rows}):
        for stratum,key in [("all",None)]+[("error_mode",x) for x in ("correlated","extreme")]+[("source_group",x) for x in sorted({r["source_group"] for r in rows})]:
            g=[r for r in rows if r["problem"]==problem and r["arm"]==arm and (key is None or r[stratum]==key)]
            if not g:continue
            paired=[(r,base[r["world_key"]]) for r in g if r["world_key"] in base]
            ok=sum(r["complete"] for r in g)
            diffs=[r["total_virtual_time_s"]-b["total_virtual_time_s"] for r,b in paired]
            valid=ok==len(g) and len(paired)==len(g) and all(b["complete"] for r,b in paired)
            mean=lambda field:statistics.mean(r[field] for r in g)
            groups.append(dict(problem=problem,arm=arm,stratum=stratum,stratum_value=key or "all",
                runs=len(g),complete=ok,source_clear_fraction=sum(r["clear_successes"] for r in g)/sum(r["source_total"] for r in g),
                mean_T_s=mean("total_virtual_time_s"),mean_T_per_source_s=mean("T_per_source_s"),
                mean_loss_s=mean("loss_s"),paired=len(paired),
                gain_pct=-100*statistics.mean(diffs)/statistics.mean(b["total_virtual_time_s"] for r,b in paired) if valid else None,
                mean_paired_difference_s=statistics.mean(diffs) if valid else None,
                faster=sum(d<-.01 for d in diffs),same=sum(abs(d)<=.01 for d in diffs),slower=sum(d>.01 for d in diffs),
                p90_T_s=quantile([r["total_virtual_time_s"] for r in g],.9),
                max_regression_s=max(diffs) if valid else None,
                mean_walk_m=mean("walk_distance_m"),mean_measures=mean("measures"),mean_switches=mean("switches"),
                mean_clear_attempts=mean("clear_attempts"),mean_failed_clears=statistics.mean(r["clear_attempts"]-r["clear_successes"] for r in g),
                mean_wall_s=mean("wall_s"),region_violations=sum(len(r["region_violations"]) for r in g)))
    (out/"summary.json").write_text(json.dumps(groups,ensure_ascii=False,indent=2),encoding="utf-8")
    (out/"results.json").write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8")
    title=args.label
    note="本数据集全部为历史仿真场景，用于开发与回归比较；不是新的独立确认集。时间为仿真任务时间，程序时间为本机本轮并行运行耗时。未全清的策略不以提前停止时间计算提速。"
    if args.new240: note="新240由官方369数量统计构造，H1是主要参考（每题60场），H2是敏感性对照。位置、半径、方向及误差场仍包含仿真假设；此轮用于迭代，不作为反复调参后的独立确认。候选与基线使用相同世界和固定误差场。"
    labels={"three_feedback_fast":"快速三反馈","dp_geometric":"几何短期规划","baseline":"现有基线","geometry":"全反馈几何","three_feedback":"三反馈选点","probability":"概率排序","lookahead":"短期动态规划","risk_cut":"低概率截断"}
    lines=["# "+title,"",note,"","|题目|策略|全清|平均任务秒|相对基线提速|平均秒/源|程序秒/局|","|---|---|---:|---:|---:|---:|---:|"]
    for g in groups:
        if g["stratum"]!="all":continue
        gain="不计提速" if g["gain_pct"] is None else f'{g["gain_pct"]:+.2f}%'
        lines.append(f'|P{g["problem"]}|{labels.get(g["arm"],g["arm"])}|{g["complete"]}/{g["runs"]}|{g["mean_T_s"]:.2f}|{gain}|{g["mean_T_per_source_s"]:.2f}|{g["mean_wall_s"]:.2f}|')
    lines+=["","|题目|分层|策略|全清|平均任务秒|相对同层基线提速|平均秒/源|","|---|---|---|---:|---:|---:|---:|"]
    for g in groups:
        if g["stratum"]!="error_mode":continue
        gain="不计提速" if g["gain_pct"] is None else f'{g["gain_pct"]:+.2f}%'
        h="H1主要参考" if g["stratum_value"]=="correlated" else "H2敏感性"
        lines.append(f'|P{g["problem"]}|{h}|{labels.get(g["arm"],g["arm"])}|{g["complete"]}/{g["runs"]}|{g["mean_T_s"]:.2f}|{gain}|{g["mean_T_per_source_s"]:.2f}|')
    (out/"RESULTS.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    headers=["序号","场景编码","题目","H","来源","策略","N/Nd","清除","总虚拟时间","平均时间/源","移动米","检测","换频","清除尝试","失败","程序秒","状态"]
    trs=[]
    for i,r in enumerate(sorted(rows,key=lambda r:(r["problem"],r["case_id"],r["arm"])),1):
        values=[i,r["case_id"],"P"+str(r["problem"]),"H1" if r["error_mode"]=="correlated" else "H2",r["source_group"],labels.get(r["arm"],r["arm"]),f'{r["source_total"]}/{r["directional_total"]}',f'{r["clear_successes"]}/{r["source_total"]}',f'{r["total_virtual_time_s"]:.2f}',f'{r["T_per_source_s"]:.2f}',f'{r["walk_distance_m"]:.2f}',r["measures"],r["switches"],r["clear_attempts"],r["clear_attempts"]-r["clear_successes"],f'{r["wall_s"]:.3f}',r["status"] if not r["region_violations"] else "region_violation"]
        trs.append("<tr>"+"".join("<td>"+html.escape(str(v))+"</td>" for v in values)+"</tr>")
    text='<!doctype html><meta charset="utf-8"><title>'+html.escape(title)+'</title><style>body{font:14px system-ui;margin:24px;color:#172333}table{border-collapse:collapse;white-space:nowrap}th,td{padding:10px;border-bottom:1px solid #ddd;text-align:right}th{position:sticky;top:0;background:#fff}td:nth-child(2){text-align:left}input{padding:8px;width:500px}p{max-width:1000px;line-height:1.7}</style><h1>'+html.escape(title)+'</h1><p>'+html.escape(note)+'</p><input placeholder="筛选场景、策略、P3/P4、H1/H2..." oninput="for(const r of document.querySelectorAll(\'tbody tr\')) r.hidden=!r.textContent.toLowerCase().includes(this.value.toLowerCase())"><table><thead><tr>'+''.join("<th>"+h+"</th>" for h in headers)+'</tr></thead><tbody>'+''.join(trs)+'</tbody></table>'
    (out/"results.html").write_text(text,encoding="utf-8")
    print("\n".join(lines))
if __name__=="__main__":main()
