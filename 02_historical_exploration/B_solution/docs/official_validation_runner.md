# 新官方演练：冻结分配后执行，独立两臂分析

`scripts/run_official_validation.py` 仅消费 `prepare_official_validation.py` 已产生的 round 2 计划，不生成场景、不重新随机、不依据已出现的效果提前结束样本收集。每题 40..60 局，分为基线/候选两臂，每个预定区组各 2 局；问题 3、4 分开分析。用户本轮已授权这些演练；正式六次测试不在此工具范围内，含正式授权标记或非演练 assignment 的计划会被拒绝。

候选 JSON 要有 `problems['3']` 与 `problems['4']`。每题支持配置字段简写，或规范结构：

```json
{
  "solver_class": "nosignal",
  "solver_config": {"local_measure_limit": 2, "scheduling": "joint"},
  "nosignal_config": {"directional_prior": 0.5}
}
```

以上只示范格式，L/先验必须来自先前冻结的本地选择。顶层 `dependency_hashes` 是“项目相对文件路径 → SHA256”映射；使用 NoSignal 时必须包含 `src/bsolver/nosignal_sensing.py`。原十个核心文件按原 round 1 `baseline_freeze.json` 逐文件及聚合散列核验。基线固定重建原 `joint_triangular`、active、L5 的全部配置，拒绝悄悄跟随候选改参数。

准备好的材料可离线核验：

```powershell
& 'D:\st_python\python.exe' scripts/run_official_validation.py preflight --round results/round2 --baseline-freeze results/round1/baseline_freeze.json
```

预检验证候选文件 SHA、策略依赖、所有 assignment 与计划逐项一致、分臂/区组数量和样本量表。设计阶段 `dispatch_ready=false` 可以预检，但不能运行。准备完成后由主流程将计划设为 `dispatch_ready=true`；此时第一次预检冻结 `validation_freeze.json`，包括计划、候选、原基线、运行脚本/UIA桥依赖的散列和预设失败惩罚。之后若变化便停止，不自动覆盖冻结文件。设计阶段的预检不会提前锁死 readiness 标记。

在已授权演练范围内执行：

```powershell
& 'D:\st_python\python.exe' scripts/run_official_validation.py run --round results/round2 --robot-id <队伍机器狗编号> --max-new 120
```

每局复核冻结材料后调用原 `collect_official_round.collect`。原采集器仅新增可选 `policy_runner` 注入点，UIA生命周期、案例编码核对、原始行为日志复制、停止标志和未完成会话恢复逻辑保持原样。runner 同时持有 round 2 与原 round 1 的操作系统锁，防止另一轮恢复任务同时驱动模拟器；`STOP_AFTER_CASE` 阻止新局启动。已有 result 只恢复事后审计，不重跑策略；已有未确认请求/会话按原采集器暂停，不偷偷换案例。

任一新局未完成或事后 N/清除数不能确认全清，会保留结果并暂停后续分发。恢复执行是沿原计划继续，失败局不删除、不替换。策略仅接收 RobotClient 与配置，不接收事后 N/Ndir 或任何源真值。工具没有正式测试分支。

离线汇总：

```powershell
& 'D:\st_python\python.exe' scripts/run_official_validation.py summarize --round results/round2 --bootstrap 5000 --seed 92117
```

输出 `validation_summary.json` 和 Markdown。每题分别报告两臂已启动/未启动、确认全清、失败或未验证数量、失败率及 Wilson 区间；成功案例完成时间的条件均值/中位数；全尝试预设惩罚损失（失败/未验证固定 360000 s）；移动、切换、检测、光学、激光时间分解及公开 N/Ndir 组成。尚未启动的分配不计失败，已启动但没有完整结果的尝试保留为失败/未验证。

候选减基线的均值差使用**分别在两臂内独立重采样**的 bootstrap 95% CI，不把第 i 个官方案例硬配成一对。成功者时间是条件分析，必须同时看失败率及惩罚损失。两臂零失败时 bootstrap 风险差可能退化为 [0,0]，另报告 Wilson-score 风险差区间，避免将其解释为真实风险已证明为零。不用每臂 20..30 局声称尾部最差 5% 已可靠估计。

测试只用 mock UI/锁和 LocalSimulator：覆盖冻结/assignment/依赖篡改、正式标记拒绝、设计态不启动、原基线配置、NoSignal实例、停止与恢复、失败保留、独立而非配对 CI、分量汇总和 `--help`。还实际调用原准备脚本生成临时计划，再运行离线预检，核对生产 schema 一致。没有运行官方 GUI 或 HTTP。
