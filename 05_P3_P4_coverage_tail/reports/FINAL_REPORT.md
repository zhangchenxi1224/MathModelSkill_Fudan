# 本轮结果

基线为最近官方20局部署版本；新240为选型后独立确认。

|题目|H|冻结候选|全清|各N等权提速|N10—15|N16|变慢数|采用|
|---|---|---|---:|---:|---:|---:|---:|---|
|P3|H1|substitute|60/60|+0.22%|+0.21%|+0.31%|2|False|
|P3|H2|substitute|60/60|+0.05%|+0.10%|-0.31%|2|False|
|P4|H1|ring_search|60/60|+17.33%|+17.56%|+15.15%|2|True|
|P4|H2|ring_search|60/60|+15.17%|+16.26%|+3.50%|4|True|

P4覆盖路线由约24.10km缩短到17.73km；这是固定搜索路线，不是整局距离。

{
  "stored_runs": 1036,
  "actual_runs": 994,
  "reused_runs": 42,
  "complete": 1036,
  "fresh_worlds": 240,
  "fresh_paired_runs": 480,
  "selected_policies": {
    "3": {
      "candidate": "current",
      "tested_candidate": "substitute",
      "status": "retain_deployed_baseline",
      "reason": "Predeclared confirmation criteria not met; no alternative selected on confirmation.",
      "baseline": "opportunity",
      "results": "runs/confirmation_p3"
    },
    "4": {
      "candidate": "ring_search",
      "tested_candidate": "ring_search",
      "status": "confirmed_on_fresh_local",
      "reason": "Independent H1 paired interval supports improvement; H2 point estimate non-regressing.",
      "baseline": "count_transit",
      "results": "runs/confirmation_p4"
    }
  }
}

全部逐局数据见 reports/results.html。未宣称全局最优或每个新官方场景必然改善。