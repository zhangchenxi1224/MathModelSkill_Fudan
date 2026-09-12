"""Paired cost decomposition and regressions for selected experiments."""
from pathlib import Path
import argparse,json,random,statistics,math,sys
sys.stdout.reconfigure(encoding="utf-8")
def read(p):return json.loads(Path(p).read_text(encoding="utf-8-sig"))
def main():
 p=argparse.ArgumentParser();p.add_argument("--runs",nargs="+",required=True);p.add_argument("--baseline",required=True);p.add_argument("--output",required=True)
 a=p.parse_args()
 base_rows=read(a.baseline) if Path(a.baseline).is_file() else [read(f) for f in Path(a.baseline).glob("cases/*/baseline/result.json")]
 base={r["world_key"]:r for r in base_rows}
 rows=[read(f) for root in a.runs for f in Path(root).glob("cases/*/*/result.json")]
 pairs=[]
 for r in rows:
  b=base[r["world_key"]]
  pairs.append(dict(case_id=r["case_id"],world_key=r["world_key"],problem=r["problem"],arm=r["arm"],error_mode=r["error_mode"],N=r["source_total"],Nd=r["directional_total"],
    complete=r["complete"] and b["complete"],baseline_T_s=b["total_virtual_time_s"],candidate_T_s=r["total_virtual_time_s"],
    delta_T_s=r["total_virtual_time_s"]-b["total_virtual_time_s"],
    delta_T_per_source_s=r["T_per_source_s"]-b["T_per_source_s"],baseline_T_per_source_s=b["T_per_source_s"],
    movement_delta_s=(r["walk_distance_m"]-b["walk_distance_m"])/5,
    measure_delta_s=5*(r["measures"]-b["measures"]),switch_delta_s=r["switches"]-b["switches"],
    clear_delta_s=3*(r["clear_attempts"]-b["clear_attempts"])+2*(r["clear_successes"]-b["clear_successes"])))
 out=Path(a.output);out.mkdir(exist_ok=True,parents=True)
 summaries=[];rng=random.Random(20260912)
 lines=["# 配对收益与变慢案例","","时间分解：移动距离/5 + 检测次数×5 + 换频次数 + 清除尝试×3 + 成功清除数×2。所有差值为候选减基线，负值表示省时。","",
        "区间是按场景配对重采样得到的描述性95%区间；数据已用于候选筛选，不能当作官方泛化或重新独立确认的保证。",""]
 for key in sorted({(r["problem"],r["arm"],r["error_mode"]) for r in pairs}):
  g=[r for r in pairs if (r["problem"],r["arm"],r["error_mode"])==key]
  values=[r["delta_T_s"] for r in g];n=len(g)
  draws=sorted(statistics.mean(rng.choices(values,k=n)) for _ in range(2000))
  avg=lambda name:statistics.mean(r[name] for r in g)
  record=dict(problem=key[0],arm=key[1],error_mode=key[2],runs=n,complete=sum(r["complete"] for r in g),
    baseline_T_s=avg("baseline_T_s"),candidate_T_s=avg("candidate_T_s"),delta_T_s=avg("delta_T_s"),
    gain_pct=-100*avg("delta_T_s")/avg("baseline_T_s"),per_source_gain_pct=-100*avg("delta_T_per_source_s")/avg("baseline_T_per_source_s"),
    mean_delta_bootstrap_interval_s=[draws[49],draws[1949]],faster=sum(v<-.01 for v in values),same=sum(abs(v)<=.01 for v in values),slower=sum(v>.01 for v in values),
    max_regression_s=max(values),mean_movement_delta_s=avg("movement_delta_s"),mean_measure_delta_s=avg("measure_delta_s"),
    mean_switch_delta_s=avg("switch_delta_s"),mean_clear_delta_s=avg("clear_delta_s"))
  summaries.append(record)
  h="H1主要参考" if key[2]=="correlated" else "H2敏感性"
  lines.extend([f'## P{key[0]} · {key[1]} · {h}',"",
    f'全清 {record["complete"]}/{n}；任务时间提速 {record["gain_pct"]:+.2f}%；每源时间提速 {record["per_source_gain_pct"]:+.2f}%。',
    f'省时/持平/变慢：{record["faster"]}/{record["same"]}/{record["slower"]}。平均时间差 {record["delta_T_s"]:.2f} 秒，描述性区间 [{draws[49]:.2f}, {draws[1949]:.2f}] 秒。',
    f'平均差值分解：移动 {record["mean_movement_delta_s"]:+.2f}，检测 {record["mean_measure_delta_s"]:+.2f}，换频 {record["mean_switch_delta_s"]:+.2f}，清除 {record["mean_clear_delta_s"]:+.2f} 秒。',"",
    "|场景|N/Nd|总时间差/s|移动差/s|检测差/s|换频差/s|清除差/s|","|---|---:|---:|---:|---:|---:|---:|"])
  for r in sorted(g,key=lambda r:r["delta_T_s"],reverse=True)[:8]:
   lines.append(f'|{r["case_id"]}|{r["N"]}/{r["Nd"]}|{r["delta_T_s"]:+.2f}|{r["movement_delta_s"]:+.2f}|{r["measure_delta_s"]:+.2f}|{r["switch_delta_s"]:+.2f}|{r["clear_delta_s"]:+.2f}|')
  lines.append("")
 for name,value in [("paired_cases.json",pairs),("paired_summary.json",summaries)]:
  (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
 (out/"DIFFERENCES.md").write_text("\n".join(lines),encoding="utf-8")
 print(json.dumps(summaries,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
