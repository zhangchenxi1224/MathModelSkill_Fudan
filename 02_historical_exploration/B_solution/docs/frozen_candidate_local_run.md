# 冻结候选的本地复现

在项目根目录运行，例如：

```powershell
& 'D:\st_python\python.exe' scripts/run_frozen_candidate.py --candidate results/round2/candidate.json --problem 4 --seed 202609119901 --count 3 --output results/local_reproduction/p4_seed202609119901
```

`--count` 是本地局数，不是源数量。问题3或4分别运行；同种子、题号、局索引和冻结代码决定同一隐藏场景与固定误差场。实际墙钟时间、日志时间戳不要求逐字节相同。已有输出目录会被拒绝，重跑请选新目录。

入口直接复用 `run_official_validation.normalize_spec`，每局核验候选依赖文件及原10个核心文件的冻结散列。候选旁如有 `plan.json` 或 `validation_freeze.json`，必须与其中 `candidate_sha256` 一致；没有旁证时记录输入原始 SHA，并标注仅核验指定文件。默认基线证书为候选旁 `baseline_freeze.json`，不存在时使用 `results/round1/baseline_freeze.json`。不会生成或修改官方计划。

这个入口只有本地模式，始终构造 `LocalSimulator` 传输器，没有官方地址或 GUI 开关。求解器只收到公开 `RobotClient` 和策略配置；源真值只由环境创建和执行后的审计使用。记录为 `environment=local_synthetic`，绝不汇入官方成绩或既有调参、确认样本。

示例环境是明确的合成参考：源数在10—16均匀取值，位置按圆盘面积均匀，接收半径在1000—1500米均匀，问题3全向、问题4各源独立以0.5概率定向，朝向均匀；误差场固定为150米平滑公式。它不是官方分布的唯一估计，也不是完整校准池的替代。

每局保存 `scenario.json`、原始 `requests.jsonl`、`decisions.jsonl`、`result.json`；顶层保存候选原始副本、manifest、实际结果和完成/失败汇总。`scenario.json` 是本地环境重放输入，求解器不会读取它。失败与异常保留实际已耗时间，并在独立字段给出360000秒失败惩罚；不把较短失败时间当作成功时间。日志还含执行后外包真值检查。

该工具的工程测试使用临时 L1/NoSignal 规格，包含真实两题成功运行、预算不足失败、依赖或候选哈希改变、禁止 HTTP 和拒绝覆盖等构造检查。这些 smoke 仅验证入口可用，不形成新的策略效果证据。
