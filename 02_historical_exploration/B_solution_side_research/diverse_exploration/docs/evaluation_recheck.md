# 实验入口修复复核

复核时间：2026-09-11 08:29 UTC（北京时间 16:29）。对象为 `diverse/experiment.py`，复核前后 SHA256 均为 `928eb84adc718b610645bbad6acb7236904e76eb1010d025a94e4b92b833d0e6`。只读复核并运行现有实验入口测试，没有修改 runtime 文件或正在执行的工程烟测。

当前未发现会阻止固定源码下这轮 48 次工程烟测的缺陷。以下区分已经落实的修复、实际测试覆盖与剩余运行边界；本报告不赋予任何候选性能胜出结论。

## 修复落实情况

|原审阅项目|当前实现|复核结论|
|---|---|---|
|烟测/研究世界重复|新增 `world_digest`，字段为 problem、seed、sources、error_mode、correlation_length，忽略 case_id 与 partition；`--exclude-manifest` 递归提取既有世界，拒绝相同 seed 或 world hash；本轮内也排重|原先改标签绕过检测的问题已修复；排除哪些外部计划仍取决于实际传入的清单|
|stress 半径与几何混杂|stress 的 layout 每 6 条轮换；radius 每 6 条切换，18 条构成 6 layout×3 radius；noise 采用与 layout 周期互素的 5 周期|确定性的一对一混杂已解除；小设计仍不能覆盖所有高阶组合|
|普通 harness 异常|`evaluate_job` 包住 `_evaluate_job`，无既有结果时写 `harness_failed`、traceback、360000 预注册损失；未知实际时间/路程保持 null|普通 Python 异常不再使案例悄悄消失；汇总支持 null 成本|
|半成品恢复|`--resume` 将无结果的半成品目录移动到 `interrupted_attempts`，保留证据后重新执行相同分配；移动前检查源/目标均在实验分区内|不再因 `exist_ok=False` 永久卡住，且没有删除原日志|
|源码来源|worker 开始前核对 source hash，结束时写 `source_valid`；不匹配则标 `invalid_source_drift`，计失败损失；resume 拒绝 `source_valid` 缺失/false 的结果|观察到的单局源码漂移不能作为成功结果复用|
|整批源码失效|末尾验证源码与每个 worker 的 `source_valid`；失配写 `INVALID_SOURCE_DRIFT.json`，下次启动明确拒绝该批次|原先恢复源码后直接接受已标失效批次的缺口已修复|
|选择文件门禁|确认只接受与 manifest 同目录的 canonical `selection.json`；核对 selection 自身 hash、manifest hash、development 结果 hash 与候选是否注册|普通改名、编辑候选或改变开发结果不能静默通过；已存在 selection 不可被 freeze 覆盖|
|freeze provenance|完整检查分配数量与身份集合，随后核对 source_valid、源码 hash、scenario hash、config、options 与 manifest|主要配置/来源漂移均被拒绝；平局也不再仅因路线名称顺序选择新候选|
|机制汇总|新增 problem×pool×route×radius_mode×error_mode 的 `mechanism_groups`，含 n、完整数与惩罚均值；n<10 标小样本描述|已补上半径与误差机制诊断；选择主规则仍为各池平均损失无退步且无新增失败|

## 实际测试

使用项目独立 vendor 路径、`D:/st_python/python.exe -B`，运行 `pytest.main(['tests/test_experiment.py','-q','-p','no:cacheprovider'])`，结果 **6 passed in 0.32s**。

现有六项测试实际覆盖：

1. Q4 98 种合法组成及定向数量的一致性，相同固定误差函数与访问顺序无关。
2. research 计划内部 seed 唯一，确认分配只含 robust 与选择路线，改 manifest 被拒绝，重复 prepare 被拒绝，显式旧 manifest 的同 seed 计划被拒绝。
3. exploratory 或非法联合组成概率不能当作已拟合组成输入。
4. 候选提前失败的 1 秒观测时间不能战胜完整基线；损失使用 360000 秒，双成功数为 0，单样本区间为空。
5. world hash 忽略标签；每种 stress layout 在 18 条设计内覆盖全部三种半径。
6. 无效策略构造触发 harness_failed，实际时间保持 null，失败与异常原因确实落盘。

canonical selection、自身 hash、完整 freeze provenance、批次源码失效标记与半成品归档在本次通过静态控制流复核；没有为这些分支新增文件或宣称已有六项测试逐一覆盖它们。

## 保留边界与后续解释

- **源码检查是起止采样，不是不可变执行快照。** 若外部进程在两次检查之间修改又恢复文件，或动态更换已导入模块，单靠文件 hash 无法追踪所有瞬时变化。当前明确冻结源码、运行中不编辑的流程适用。若要支持并发热更新，需要从每个实验的独立源码快照启动 worker；这不是当前工程烟测的阻塞项。
- **硬终止不等于普通异常。** 进程被强杀、系统中断或持久化磁盘不可写时，worker 不能保证生成 harness_failed。应保留中断档案，查明 stale `RUNNING.lock` 的进程已经结束后恢复；汇总反映最终已完成的预分配，同时在论文/报告列明中断次数。不能将中断档案视为不存在。
- **排重范围是显式输入。** `--exclude-manifest` 能验证列出的计划，不能自动知道未提供的历史实验。新 360 世界研究计划应提供本次 smoke 与相关历史计划，并记录实际排除路径及 hash；这一步的参数证据需要在 prepare 产物中确认。
- **机制表不是完整笛卡尔积证明。** 当前分层没有进一步按 layout、Ndir、correlation_length 拆表，也没有按这些层做配对区间；相关长度可从 manifest 追回。360 世界设计支持探索，不能声称所有机制组合已充分验证。P4 broad development 40 个案例也不等于覆盖全部 98 个组成。
- **选择 hash 保护意外变动，不是权限系统。** 有权限主动改选择并重算所有 hash 的操作者仍能重写实验历史；当前研究流程无需将操作者视作对抗方，应保留审计产物与版本记录。
- **freeze 使用来源标识而非重新核验所有统计。** 它检查主要 provenance，但不会从日志重新计算每条结果的 pool/problem 等复制字段、完整率和时间。正常入口自产结果一致；若人工加工过结果，应回到原始单局产物或另做审计，不能假定 hash 可替代数据真实性检查。

工程烟测结果应继续只用于运行、耗时与不变量检查。研究候选的选择/确认需要新计划、预先冻结的分配和完整开发分母；本复核没有读取尚未完成的路线优劣来调整选择。
