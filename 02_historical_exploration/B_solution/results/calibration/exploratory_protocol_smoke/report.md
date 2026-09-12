# 整局日志校准与可观测检验

模型状态：exploratory。读取 23 个案例目录，其中官方 5 个实际尝试、4 个已确认进入案例；另有 0 个仅预分配而未启动的目录，不进入尝试和成本分母。

这里只估计源总数与定向数的联合分布 P(N,Ndir)，不把两张独立直方图相乘。无 N 或 Ndir 的案例保留在审计/成本表，但不进入该联合拟合；问题3的 Ndir=0 来自题设。

## 组成与划分

|问题|fit已知联合组成局数|全部已知(N,Ndir)|development已知局数|
|---|---:|---|---:|
|3|1|{(15, 0): 1, (11, 0): 1}|1|
|4|2|{(16, 14): 1, (14, 2): 1}|0|

缺少预分配记录而使用追溯整局哈希的尝试数：5。旧演练分析只能用于探索，不能包装为前瞻验证。新实验须在进入前持久化 assignment.json；development 不进入任何 fit 计数，也不自动用于选择候选模型。

联合候选包含经验分布、总先验权重为 2.0 局的 Dirichlet 平滑分布和宽联合先验。先验按 N 均匀、Ndir|N 均匀构造；这是明确的假设，经验项仍保留 N 与 Ndir 的依赖。空经验分布标 unavailable，不冒充已校准。

## 反馈与矩阵

提取 6151 次去重后 accepted 测量，205 条同点/邻点报告关系，6151 条算法外包面积记录。

反馈按问题、采集协议、阶段、站点、测前存在认知、测前已清除状态与事后存在证据条件化。事后存在标签仅用于离线分层，不反馈在线策略。未测频道 mask=false、值为 null；no_signal 只表示一次真实无信号反馈。全20频道且清除前测量的固定站点矩阵才适合作设计一致的比较。

channel_visibility_patterns.* 保留同一频道跨全部计划测点的联合模式与掩码。joint_visibility_case_summaries.jsonl 给出每局已确认存在频道的可见站点数分布；joint_visibility_pairs.csv 给出六个几何最近站点对的 both-visible 与 discordant 概率。站点对按距离、路线索引确定，不按反馈选取；这是当前分析冻结规则，不追称旧数据采集前预注册。概率先在整局内聚合，再按局计算不确定性；同局的频道与测量不是独立样本。

方向变化和同点重复值不是相对未知真实方向的误差；邻点还包含几何变化。外包面积是算法代理，不是真定位误差。clear success 只给出以提交点为圆心的20m约束，提交点不是真值。

## 成本与不确定性

|来源/问题/协议/划分/候选|尝试|完成|全部尝试观测耗时均值/s|完整局耗时均值/s|
|---|---:|---:|---:|---:|
|local/3/baseline/development/smoothed_joint__radius_max__noise_deterministic|2|2|4614.9061535|4614.9061535|
|local/3/baseline/development/smoothed_joint__radius_min__noise_deterministic|2|2|4954.009357|4954.009357|
|local/3/baseline/development/smoothed_joint__radius_mixed__noise_deterministic|2|2|4691.362454|4691.362454|
|local/3/survey/development/smoothed_joint__radius_max__noise_deterministic|1|1|13380.610648|13380.610648|
|local/3/survey/development/smoothed_joint__radius_min__noise_deterministic|1|1|14135.741312|14135.741312|
|local/3/survey/development/smoothed_joint__radius_mixed__noise_deterministic|1|1|13762.207982|13762.207982|
|local/4/baseline/development/smoothed_joint__radius_max__noise_deterministic|2|2|11447.527088499999|11447.527088499999|
|local/4/baseline/development/smoothed_joint__radius_min__noise_deterministic|2|2|10367.2812085|10367.2812085|
|local/4/baseline/development/smoothed_joint__radius_mixed__noise_deterministic|2|2|11326.9453|11326.9453|
|local/4/survey/development/smoothed_joint__radius_max__noise_deterministic|1|1|17649.720079|17649.720079|
|local/4/survey/development/smoothed_joint__radius_min__noise_deterministic|1|1|18202.74761|18202.74761|
|local/4/survey/development/smoothed_joint__radius_mixed__noise_deterministic|1|1|17624.422437|17624.422437|
|official/3/legacy_adaptive_06cc6dc553/development/unspecified|1|1|3858.840284|3858.840284|
|official/3/legacy_adaptive_06cc6dc553/fit/unspecified|2|1|2616.153053|5232.306106|
|official/4/legacy_adaptive_801190bf73/fit/unspecified|2|2|11310.361692|11310.361692|

失败与未知状态不删除；未完成耗时是截至停止的投入，不能作为较快完成。先报告完成率，再比较完整局成本。置信区间按整局重采样，动作行从不独立抽样；不足2局的区间记 null，零观察事件不证明真实概率为零。

## 留出检验和输出

predictive_checks.json 报告联合组成 NLL/Brier、零预测概率次数，以及同协议条件可匹配的 development/本地反馈 NLL。无法匹配的站点或协议不强行合并；覆盖率与未匹配数需一同查看。这些检验只反映记录中的采样设计，不能识别未知 g、R、u 或完整误差场。

model.json 为组成模型；features/cases.* 保留失败和缺失；measurements.* 为公开反馈；visibility_matrices.jsonl 带观测掩码；observable_pairs.csv、contractions.csv 为代理；joint_constraints.jsonl 保留相同源共享参数的相容约束摘要。源文件 SHA256 进入案例审计。

半径、噪声、位置与发射朝向没有充分可辨识证据，继续保留多种宽分布与极端压力机制；禁止根据清除耗时反向调成某个假想官方场景。

## 相同协议的本地候选机制检验

model_checks.json 只将真正复用 baseline/survey 的本地记录与同问题、同协议的官方 development 案例比较。至少每侧5局才尝试区间判别；差异95%整局自助区间落入预设实质等价范围才记单项 matched，整段落在范围外记 screen_flag，其余及缺字段均记 insufficient_evidence。matched 仅代表所测可观察指标兼容。

检查联合(N,Ndir)的两样本总变差/置换检验，完整清除前20频道标准站点可见率，survey追加测量数与无信号率、相对冻结估计点的名义距离/方位分层、同点/邻点报告关系、外包面积收缩、完成率、失败清除数与路程。不同协议、不同实际站点不能互补成匹配。候选 matched 还要求完整跨站联合分布、固定站点对指标及联合组成均达到实质兼容，仅站点边缘可见率接近不够。单项偏差不能直接拒绝整个机制；只有核心联合可见性偏差在预定整局哈希两半均以同方向超出容限、且各半每侧至少5局，才记探索性 biased。

|本地候选|问题|协议|官方development局|本地局|证据状态|
|---|---:|---|---:|---:|---|
|smoothed_joint__radius_max__noise_deterministic|3|baseline|0|2|insufficient_evidence|
|smoothed_joint__radius_max__noise_deterministic|3|survey|0|1|insufficient_evidence|
|smoothed_joint__radius_max__noise_deterministic|4|baseline|0|2|insufficient_evidence|
|smoothed_joint__radius_max__noise_deterministic|4|survey|0|1|insufficient_evidence|
|smoothed_joint__radius_min__noise_deterministic|3|baseline|0|2|insufficient_evidence|
|smoothed_joint__radius_min__noise_deterministic|3|survey|0|1|insufficient_evidence|
|smoothed_joint__radius_min__noise_deterministic|4|baseline|0|2|insufficient_evidence|
|smoothed_joint__radius_min__noise_deterministic|4|survey|0|1|insufficient_evidence|
|smoothed_joint__radius_mixed__noise_deterministic|3|baseline|0|2|insufficient_evidence|
|smoothed_joint__radius_mixed__noise_deterministic|3|survey|0|1|insufficient_evidence|
|smoothed_joint__radius_mixed__noise_deterministic|4|baseline|0|2|insufficient_evidence|
|smoothed_joint__radius_mixed__noise_deterministic|4|survey|0|1|insufficient_evidence|

这些多指标检查是探索性筛查，未做整个指标族的同时显著性控制；biased 提醒检查或降低候选优先级，不能单凭一次筛查宣布真实机制已被证明。首轮每问20 survey的3:1划分只留下5局development，两半稳定检查通常证据不足；增加本地局数不能代替新的官方留出证据。development若据此参与模型保留，下一轮或最终验收必须另用未消费案例。

**当前样本只支持探索性描述，不支持宣称强校准、官方生成律恢复或精确泛化保证。**
