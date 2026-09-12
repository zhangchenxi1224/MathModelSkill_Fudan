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
## 后续官方演练更新

P3、P4各10局，20/20全清。P3平均总虚拟时间3363.63秒、平均每源250.87秒；P4为5882.37秒、437.68秒。

[查看官方逐局成绩](../runs/official_coverage_tail_10_each_20260912/report/results.md)。本批为新官方场景，没有同场景旧算法配对，不能由跨批均值推断提速幅度。

## 后续正式测试更新

P3、P4各3次正式测试均正常结束，6份官方日志均显示已上传。

|题目|次数|成功清除数C|总虚拟时间（秒）|秒/成功清除源T/C|
|---|---:|---:|---:|---:|
|P3|1|10|2822.06|282.21|
|P3|2|16|3671.02|229.44|
|P3|3|14|3685.38|263.24|
|P4|1|10|6196.37|619.64|
|P4|2|11|6389.90|580.90|
|P4|3|14|6475.28|462.52|

三局T/C的算术平均：**P3 258.30秒/成功清除源，P4 554.35秒/成功清除源**。正式界面不公开真实源总数N及定向源数Nd，因此以成功清除数C为分母，不能将六局正常结束写作官方公开的全清率。

[正式逐局成绩与日志](../runs/formal_selected_3_each_20260912/report/results.md)。
