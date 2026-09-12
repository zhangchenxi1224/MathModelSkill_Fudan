# 首轮采集后的本地配对迭代协议

本流程只执行自建模拟器。首轮160例官方采集由独立采集器处理；校准文件就绪之前，不用早先少量练习结果冒充该轮分布。下一轮官方演练验证已在用户本轮计划范围内，依赖候选与样本量预冻结，实际执行状态由主执行器记录。正式测试仍未授权。`round_experiments.py`不包含HTTP地址、官方token或官方调度能力。

## 冻结基线与固定环境

基线是既有`joint_triangular`：active测点、joint调度、三角950覆盖、局部检测上限5、绕行阈值800 m、角容差1.0051°、4°分箱、radius_weight=2、最近安全清除点。全部配置逐字段显式写出，不依赖将来的默认值。10个原核心文件的散列固定为`1b673484453104a5d87d4605d30b20826a3b7ec64769fd6ce8d3c8f7a1ad24f6`；增加采集/实验模块不改变这个定义。任一核心文件变化就停止该轮，必须另立版本。

一个场景在manifest中完整保存源位置、频道、接收半径、发射方向和误差场定义。`scenario_sha256`覆盖全部场景字段；固定误差场是`FixedErrorField-v1(seed,mode,correlation_length)`，在同一坐标/频道上与访问顺序无关。各策略实例拥有全新模拟器状态，却使用完全相同的函数式误差场。不能按每次请求独立抽噪声，也不能为某个策略更换场景种子。

隐藏真值只进入`LocalSimulator`和运行结束后的外部核验。策略仍只接收`RobotClient`、题号及公开配置；真实N、Ndir、位置、方向、半径和误差种子都不传入策略。每次运行保存独立结果和gzip请求/决策日志，外部检查每一次后验是否保留真值。

## 1000个不同场景：三个独立池

|池|场景数|P3/P4|生成原则|可支持的结论|
|---|---:|---:|---|---|
|calibrated|400|200/200|冻结的Dirichlet平滑联合smoothed_joint与全支持先验broad_joint各半|对两个预先指定组成模型的局部稳健性；不代表官方总体期望|
|broad|400|200/200|N=10…16；P4所有98种合法(N,Ndir)组合至少各2例|对广泛合法组成的比较|
|stress|200|100/100|圆周向外、切向、最后一个向外隐藏、中心/边界、聚簇、格点及5 m邻域退化|对特定困难构造的压力核验|

校准池在机制检查后只使用manifest生成前冻结的保留/未决机制列表，按机制分层；正式状态被标为biased/screened的机制不再进入该池。点态比较的screen_flag不等于正式排除；证据不足可保留为unresolved，不能改称已识别。若未给列表，则半径min/max/mixed、噪声hash/correlated/extreme和相关尺度75/150/300 m仅为组合假设，明确标`composition_only_assumptions`。broad/stress仍保留完整合法机制网格，不随机制检查结果删去困难场景。方向模型包含全向与定向组合，P3固定Ndir=0。不同池分别抽独立种子，不复用几何场景。

组成模型选择记录见`results/round1/composition_screen_decision.json`：Q4官方20个development案例中，经验联合对9个观测组成赋零概率；平滑模型NLL=5.5733，全支持先验NLL=4.6003。因此保留原已指定的smoothed/broad两模型，未改fit参数或平滑强度，经验模型原件保留。官方development已用于此模型选择，最终接受须用新的官方案例；不能再称这批development为未触碰检验。独立broad池仍用于穷举合法支持，与calibrated池的随机broad_joint层分别报告。

校准只消费组成分布。官方数据无法直接识别的半径、位置、方向及噪声机制仍是明确的合成假设；绝不反调这些量让模拟器平均耗时贴近官方均值。calibrated的输出也不能称“复现了官方隐藏生成器”。

每池/题/组成模型按独立哈希排名预先分为80% development和20% confirmation，总800/200；小规模组按20%向下取整。分配与机制轮换周期独立，避免5种机制恰好对应每5例一次确认而使某机制全进入确认。所有策略均配对执行；筛选函数只读取development。确认集结果用于一次冻结候选的检验，不能看完后反复换参数。CSV同时提供每机制/噪声细分，小样本层明确提示精度不足。

## 校准JSON接口

消费与校准代理约定的schema：

```json
{
  "schema_version": 1,
  "model_id": "round1-composition-fit",
  "model_status": "fitted_composition_only",
  "provenance": {"n_official_cases": 160, "n_fit": 120},
  "problems": {
    "3": {"n_fit_known_joint": 60, "composition_candidates": {
      "empirical_joint": {"status": "fit", "count_distribution": []},
      "smoothed_joint": {"status": "fit", "count_distribution": []}
    }},
    "4": {"n_fit_known_joint": 60, "composition_candidates": {
      "empirical_joint": {"status": "fit", "count_distribution": []},
      "smoothed_joint": {"status": "fit", "count_distribution": []}
    }}
  }
}
```

上例数字仅展示字段，不是采集结果；实际`count_distribution`必须是`{"n":10,"n_directed":7,"probability":0.2}`等合法联合项且概率和为1。未知Q4 Ndir不能填0；没有已知联合组成的题目或`status=unavailable`的模型拒绝生成calibrated池。`model_status=exploratory`的早期练习模型同样拒绝启动计划轮次。分布文件本身另存SHA256与来源，模型变化后必须产生新manifest和输出目录。

`--mechanisms`另读JSON数组，例如`[{"id":"smoothed_joint__radius_mixed__noise_deterministic","radius_mode":"mixed","error_mode":"deterministic","correlation_length":150,"status":"unresolved"}]`。可保留状态为retained/matched/unresolved/insufficient_evidence/not_rejected，必须带明确状态。参数和机制id写进每个场景及结果，不根据后续测试收益重调分层。汇总同时提供每机制层与`__design_mixture__`设计组合；后者只是给定实验设计的混合，不是官方总体均值，不能单独支持“已校准”的表述。

## 阶段1：只比较局部检测上限

进入策略筛选前，先用`scripts/run_protocol_checks.py`进行同协议机制验证。它直接实例化冻结`Solver`或`SurveySolver`，保持标准survey的完整固定站点、清除前观测、目标随机采样及附加探针不变。每个候选机制的标准设计是每问baseline60+survey20，共160局；可根据实际预算先做160或320，不必一次跑完全部9种机制。

候选机制网格为接收半径min/uniform/max × 误差hash/correlated/extreme，主800机制检查的组成使用`smoothed_joint`；另预先登记160局`broad_joint × mixed:deterministic`同协议对照，不扩展为全机制笛卡尔积。`empirical_joint`保留描述与诊断用途。共同case_id/种子保存相同的基本g/u与混合半径随机变量，只切换R定义或固定误差场机制；`scenario_id`含模型前缀，防止校准分析误去重。所有本地assignment显式`environment=local, split=development`，绝不加入官方组成fit。真实N/Ndir只在退出后写`post_exit_audit.json`。

适配器保存普通`requests.jsonl/decisions.jsonl`供校准器直接读取，额外记录完整率、每条外包集合真值保持和全部失败。必须等校准器将官方和本地相同protocol、phase、station_id、清除前可观测量逐项比较后，才可把某个机制称为“未发现偏差/匹配”；组成分布拟合成功本身不等于整个模拟机制通过。观察量不足时必须标`insufficient_evidence`，不把少样本的“不显著”当校准完成。

```powershell
D:\st_python\python.exe scripts\run_protocol_checks.py --fixture-broad --models mixed:hash --cases-per-protocol 2,1 --workers 2 --output results\round1\protocol_bench_fixture
D:\st_python\python.exe scripts\run_protocol_checks.py --calibration results\calibration\round1\model.json --models mixed:hash,min:hash --cases-per-protocol 60,20 --workers 8 --output results\round1\protocol_checks
```

主机制检查已实际完成5候选：mixed:deterministic、mixed:correlated、mixed:extreme、min:deterministic、max:deterministic；每个为每问60 baseline+20 survey，共800次本地运行、8进程，墙钟309.6305秒。800/800全清、0外包约束违规，800份退出后审计齐全，活动依赖无漂移。证据在`results/round1/protocol_checks_primary`；源快照在`source_freeze_protocol800`。这只验证算法在这些本地机制中的运行与约束，匹配官方可观测量的结果由校准分析另行给出；18次早期fixture不计入800次。

另行预登记的broad_joint × mixed:deterministic参考已完成160/160，墙钟64.0062秒、0外包约束违规，目录`results/round1/protocol_checks_broad_reference`，预派发决策与源散列保留。它只为组成不确定性提供一个同协议参考，不代表broad组成下全部机制均已验证。

首条只是显式广覆盖假设下的代码/吞吐检查，不能称基于官方完成校准。若用旧少量官方`exploratory`模型做机制fixture，必须显式加`--allow-exploratory`，标签原样保留。该入口不运行官方采集器或GUI。

四臂为冻结L5基线和L1、L2、L3，其余配置、代码、场景完全一致，共4000次策略运行。先跑小benchmark核验耗时，再根据实际CPU吞吐选worker数；不需要GPU。每场景的四臂执行顺序由独立固定种子预随机化，减少本地缓存或机器负载顺序影响，runtime不作为策略得分。

主结果依次报告：完整率/失败数、完整率配对差、预注册失败惩罚时间差、双成功子集的实际虚拟时间差、退步比例、最坏退步。失败惩罚定义为成功时取实际时间，失败时记360000秒。这是一个事先规定的评价损失，不能解释成失败场景的真实完成时间；失败日志和实际已耗时间原样保留。双成功子集存在选择效应，不能替代全案例分析。

区间在同一场景内做配对bootstrap，按池/题/组成模型分别计算；不把四个策略当独立样本，不把不同假设池混成一个“官方平均”。CSV中的95%区间是点态探索性区间，未做多重比较校正，不能仅凭三个候选中的最优均值声称显著胜出。只有1例的层不输出伪精确区间。

初筛仅用development，要求候选在每个池/组成模型的预注册设计组合中平均惩罚差为负，且任一机制层都不增加失败，最多每题保留1–2个。用最差池/组成组的改善排序，不把三个池合成一个均值；每机制的退步与区间保留供判断。若无候选满足条件，保留L5。该shortlist只是探索候选，不自动部署，不以成功次数有限就声称连续域保证。

## 阶段2、3的顺序和禁止越界

阶段2新增`NoSignalSolver`及阶段2/3配对入口`scripts/run_candidate_stages.py`已实现。正式执行已完成阶段1的4000次运行，阶段2的640 development与120冻结确认运行，以及阶段3的960 development与80 joint单臂可靠性确认运行；这些与早期工程fixture分开保存。完整选择链与精确物理运行数见`docs/local_iteration_results.md`及`results/round1/local_iteration_summary.json`。

阶段2固定P3/P4各自选定L，normal与独立`NoSignalSolver`只在测点排序上不同；原候选集合、集合更新、Qsafe/联合半径认证、清除认证和有限光学兜底保持不变。新增选择器枚举少量由观测历史相容的固定g/R/u状态，组分先验质量归一，再估计响应分支权重。它不得访问真实Ndir/方向/误差字段。有限网格权重不是经过校准的概率后验，也不是连续域证明；零相容样本等情况回退原选择器，认证仍在保守外包集合上完成。

评分统一为秒，例如`移动距离/5 + 检测5 + 必要切换1 + p(no_signal)*后续无信号分支成本 + p(direction)*方向分支成本 + p(near)*近距分支成本`。概率与后续成本必须注明估计来源和误差；现有`direction_outcome_bound`只界定direction分支后验半径，不能冒充所有响应的剩余时间上界。先用少量场景检查新启发是否真的减少无效观测，再扩展实验；不提前投入昂贵模型或大规模搜索。

阶段3固定前两步选定的各题L与normal/nosignal模式，再比较joint基线与immediate/scan_then_clear，只有scheduling字段变化。两个阶段均生成新场景，验证与所有已提供旧manifest无seed或完整世界重用，继承最新前一阶段的冻结机制列表及calibrated_models组成列表（按阶段号选择，不能静默回到旧默认经验/平滑组合）；不复用已读过的阶段1confirmation。最多推进1–2个候选，进入下一次官方演练前冻结为单一候选。

新入口默认只运行development。`freeze-selection`要求完整的预注册development分配已运行，并持久化每题一个选择及development结果散列。只有携带该selection才能运行confirmation，且只运行基线与选定候选；若选择基线自身，则确认区只有基线，不能补造候选改进。确认结果按目录分离，不在同一汇总中供继续筛选。

```powershell
# L值仅为接口例子，实际必须使用阶段1冻结的P3/P4选择。
D:\st_python\python.exe scripts\run_candidate_stages.py prepare --stage 2 --local-limits 3,2 --previous-manifest results\round1\local\manifest.json --calibration results\calibration\round1\model.json --output results\round1\stage2
D:\st_python\python.exe scripts\run_candidate_stages.py run --manifest results\round1\stage2\manifest.json --output results\round1\stage2 --workers 8
# 选择值同样是接口示例，不是当前实验结论。
D:\st_python\python.exe scripts\run_candidate_stages.py freeze-selection --manifest results\round1\stage2\manifest.json --development-results results\round1\stage2\development\results.json --choices normal,nosignal --output results\round1\stage2\selection.json
D:\st_python\python.exe scripts\run_candidate_stages.py run --manifest results\round1\stage2\manifest.json --output results\round1\stage2 --partition confirmation --selection results\round1\stage2\selection.json --workers 8
D:\st_python\python.exe scripts\run_candidate_stages.py prepare --stage 3 --local-limits 3,2 --modes normal,nosignal --previous-manifest results\round1\local\manifest.json --previous-manifest results\round1\stage2\manifest.json --calibration results\calibration\round1\model.json --output results\round1\stage3
```

接口默认master_seed=2026091202/2026091303曾用于工程fixture。正式阶段2/3已在结果出现前另行冻结为2026091207/2026091307，准备manifest时显式传`--seed`，决策见`results/round1/later_stage_seed_decision.json`。正式源快照/元数据应保留该决策及原因；不得复用工程fixture或此前正式阶段的世界。正式两个阶段各400新场景（160/160/80）均已完成；阶段2确认仅执行冻结的P3单臂/P4双臂，阶段3确认仅执行各题joint单臂。NoSignalConfig全部字段与代码散列写进manifest，不能执行中更换。

## 本地命令与恢复

```powershell
D:\st_python\python.exe scripts\run_calibrated_experiments.py bench --output results\round1\benchmark --cases-per-problem 2 --workers 2
D:\st_python\python.exe scripts\run_calibrated_experiments.py generate --calibration results\calibration\round1\model_frozen.json --models smoothed_joint,broad_joint --mechanisms results\round1\mechanisms_selected.json --output results\round1\local --sizes 400,400,200
D:\st_python\python.exe scripts\run_calibrated_experiments.py run --manifest results\round1\local\manifest.json --output results\round1\local --workers 8
```

中断后加`--resume`，仅复用场景散列、核心散列、实验模块散列和完整配置均一致的已存结果。策略失败案例同样复用，不通过重跑删除失败。每臂结束即原子写`result.json`，日志gzip保存；跨案例并行，单案例内串行。`--max-cases`只用于有明确partial标记的小规模检查，不将其冒充全manifest完成。`--no-logs`是显式容量选项，正式本地轮默认保留完整日志。

真实阶段1列表已由主流程冻结于`results/round1/mechanisms_selected.json`：mixed/correlated150与mixed/extreme150均为unresolved。组成使用smoothed_joint/broad_joint，manifest=`dee3221c6c16b153ce7deb7b423ab76759c0634eb4006196343dce7ded3626b2`，1000唯一场景、800development/200confirmation；源与输入冻结在`results/round1/local/source_snapshot.zip`。这不等于识别了官方隐藏生成机制。实际运行使用该目录`run_frozen_stage1.py`调用同一run_manifest，避免通用CLI提前生成confirmation性能汇总。完整4000运行可以保存确认结果，但在主流程冻结L选择前，只读取和报告development对比及全局可靠性，不读取确认性能。场景manifest不因已看到的候选效果更改。

### 已完成的小规模工程验证

以下均为本地CPU运行，不是首轮160局官方数据，也不证明某个模拟机制匹配官方。

|检查|运行数|并行进程|墙钟秒|完整数|真值外包违规|
|---|---:|---:|---:|---:|---:|
|4个broad场景×4个L上限|16|2|11.69|16|0|
|显式广覆盖fixture的baseline/survey|6|2|5.05|6|0|
|旧4局官方探索组成模型×3种半径机制|18|4|10.00|18|0|

对应证据目录为`results/round1/benchmark`、`results/round1/protocol_bench_fixture`、`results/round1/protocol_bench_exploratory`。最后一组实际采用旧资料生成的`model_status=exploratory`，标签保留，不能升级为首轮校准结论。

计时仅支持粗略预算：按8进程估计320次同协议机制检查约1–3分钟，4000次阶段1约12–20分钟；应给官方采集器留CPU，并观察真实数据中的困难案例与日志开销。小bench不能保证该吞吐或完成率。核心版本保持`1b673484…`，标准survey版本为`standard-survey-v1`，完整散列记在每组`run_metadata.json`。

## 下一轮官方验证只生成设计

```powershell
D:\st_python\python.exe scripts\run_calibrated_experiments.py validation-design --output results\round1\validation_design --cases-per-problem 60
```

预随机化每问40–60个独立新案例，基线/单一冻结候选各半；这些案例无法假定同场景重放，必须用独立组设计。脚本输出随机分派表、正态近似功效计算、零失败仍不确定的上界；任何官方动作都不在本脚本中触发。40–60是总数，不是每臂数。若目标差异太小导致样本量不足，必须如实说明精度不足。新增官方演练验证已在授权计划范围内，冻结后由主执行器运行；正式测试仍未授权。
