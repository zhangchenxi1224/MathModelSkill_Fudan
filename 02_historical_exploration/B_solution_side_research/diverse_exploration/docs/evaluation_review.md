# 多元实验入口独立研究设计审阅

审阅日期：2026-09-11。审阅对象为 `diverse/experiment.py`、`run.py`，并只读核对 `vendor/bsolver/strategy.py`、`vendor/bsolver/simulator.py` 和 `diverse/policies.py` 的接口边界。本审阅未修改代码、未执行官方动作、未开展候选性能筛选。

主实现正在同步修复下述问题。本文件记录所见问题及验收条件，不把开发者告知的修复状态当成已经独立通过测试；最终应以修复后的文件和测试记录为准。

## 1. 已成立的设计边界

- 每个策略运行获得新的 `LocalSimulator`，相同世界的源字段、seed、误差模式、相关长度相同，误差由位置与频道决定。没有发现策略直接读取 `case`、真实源坐标、Ndir 或误差 seed；这些字段由外部 harness 消费，truth summary 在策略结束后读取。
- 原始动作日志、后验知识日志、外部全清检查和凸外包真值检查保留。原 Solver 能将策略运行期间的大部分异常记录为失败，并尝试退出；正常策略失败结果不会因为不成功而被 resume 删除。
- 统计配对以同一个 case_id 下的 robust 为参照，失败损失预先固定为 360000 秒，成功子集指标明确为条件统计，各池不合并成官方均值。
- 工程 smoke 的分区为 engineering，`freeze-selection` 要求 research；正常 CLI 路径可防止用工程烟测选优。源码文件清单、配置、模型快照、场景与 manifest 均有散列，正常固定源码执行有可追溯性。
- composition_fit 仅从明确的组成模型采样，其他隐藏机制标成合成假设，没有把它们称作已识别的官方生成器。

这些观察支持继续本地工程验证，并不构成对整个算法保证层或所有恢复路径的形式化证明。

## 2. 发现的问题及验收条件

|优先级|位置/原问题|影响|应验证的修复行为|
|---|---|---|---|
|P1|`prepare` / `make_case` 原本没有旧世界排除；scenario hash 包含 case_id、partition|相同默认 seed 的 smoke 与 research 会产生相同隐藏世界，但标签变化使 hash 不同；工程数据可进入“新”开发/确认世界|单独计算仅含实际隐藏环境的 world hash；准备新计划时检查本轮及所有显式排除 manifest 的 seed 与 world hash；同世界改名仍拒绝；研究与烟测不同 seed 且互相排除|
|P1|`make_case` 的 stress layout=`index%6`，radius=`(index//2)%3`|boundary/tangent 恒为 min；hidden_outward_last/clustered 恒为 mixed；center_and_boundary/near_grid 恒为 max，几何与半径完全混杂|独立分派或交叉设计；输出 layout×radius×noise 的覆盖表。低数量工程检查可不覆盖全组合，但研究计划须明确哪些机制未被覆盖|
|P1|`evaluate_job` 先 `mkdir(exist_ok=False)`，外层无异常记录；`future.result()` 直接上抛|constructor、外部审计或写文件等异常可无结果退出；半成品目录导致 resume 再次 FileExistsError；失败不进入分母|worker 外层捕获并保存 harness_failed；无法确认实际耗时则保持 null，不填完成时间；原证据目录保留；resume 可识别/保留 interrupted 状态；完成分母与预分配一致，失败不得悄悄重跑成成功|
|P1|run 开头/结尾才验源码，单局提前写 expected code hash|批次末尾发现源码漂移后，已落盘结果没有失效标志；恢复旧源码再 resume 可能接受漂移期间产物|由固定源快照执行，或增加不可被恢复源码抹去的批次 invalid 标记；不允许来源未封存结果混入新有效汇总。对运行中改源码触发失效后恢复源码的路径做明确检验|
|P2|confirmation 原本只校验 manifest 与 development hash；选择内容无自校验/规范位置限制|改名或改动 choices 的 selection 文件可以运行其他候选，削弱“冻结后一次确认”门禁|核对 selection 自身 hash、canonical selection 路径、合法候选与 manifest route；更强可从固定 development 重算确定性 choices。修改 choices 而不重冻须拒绝；已冻结选择不能覆盖|
|P2|`freeze` 对每条结果原本只核对 manifest hash|损坏或错误聚合的结果可能携带错误配置、世界、源码或 options 而仍进入选择|复用 resume 的完整 provenance 校验：case/scenario/world、route、source、config、options、manifest；检查无重复、无缺失、无额外结果|
|P2|汇总原本只有 problem×pool×route|池平均收益可能掩盖部分半径/噪声/几何退步|补充机制分层结果与样本数量；明确小层区间精度不足；最坏层诊断不得被总体均值遮蔽，正式选择规则先写进 manifest|

主实现者已告知正在处理 world hash/exclude、混杂、harness failed；已加 selection hash 与中断目录保留，计划补充机制分层。本审阅结束时没有再扩大改动范围，也没有宣称上述修复已逐项复验。

## 3. 针对 360 世界研究计划的准备检查

1. 只 prepare 世界，不因 smoke 改名而声称新样本；保留开发/确认世界分配，确认性能在单一候选冻结前不可进入筛选。
2. `per_cell=40`、`confirm_per_cell=20`、两题、三池会产生 360 个不同世界，其中 development 240、confirmation 120。若六路线均跑开发，开发为 1440 次运行；确认只跑 robust 与每题已选路线，最多 240 次，若选择 robust 本身则更少。不要把 360×6 当作必须提前读完的确认计划。
3. 40 个 Q4 broad development 世界不可能覆盖全部 98 个合法组成单元。可作为有限宽支持敏感性样本，但不可标成已穷举 98 单元；输出计划实际覆盖数。
4. composition_fit 的 smoothed 组成只消费 fit 证据；radius/position/direction/error 均仍是合成假设。现有官方 development 已消费于模型选择，不可再次称为最终留出。
5. 使用新的目标输出目录；源码变化需要新 manifest。验证冻结时应保证 workers 实际导入的是独立 vendor 与 diverse 路径，而不是主项目模块或其他 Python 环境中的同名安装。

## 4. 建议的最小验收集

- 同一个隐藏世界在 smoke/research 改 case_id、partition 后仍被 world hash 排重；不同配置策略仍能共享同世界配对。
- 给 worker 一个构造器异常与一次运行中异常，完整留下失败/中断证据并能执行文档中的恢复命令；总分母不变。
- 改动已冻 choices、开发结果 provenance 或源码后，相关 gate 确实拒绝执行/复用；恢复源码不能使 invalid 批次重新有效。
- 无新改动的 smoke resume 不重跑任何已完成或已失败的分配，输出与首次一致；只变程序 runtime 不作为策略评分变化。
- 以计划表检查 stress 的 layout/radius 不再确定性一一绑定，机制分层样本数与全部世界数量一致。
- 研究计划可冻结单一候选后才运行 confirmation；engineering 计划、缺失开发分配或覆盖原选择均被拒绝。

无需为实现细节逐句写镜像测试；以上检查针对实验完整性承诺及真实失效边界。工程验证通过只说明入口可用，不给任何新路线赋予性能胜出结论。
