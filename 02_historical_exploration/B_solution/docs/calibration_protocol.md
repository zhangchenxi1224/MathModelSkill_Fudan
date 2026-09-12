# 公开日志校准协议与证据边界

这条流水线读取已落盘的公开请求反馈和事后数量审计，估计源总数、定向数的联合组成，并检查本地候选机制能否复现相同采集协议下的可观察现象。它不连接官方服务、不操作 GUI、不读取包含源坐标的 `case.json`，也不把清除点当成真实源位置。正文求解算法与采集器均不由本模块修改。

## 输入、划分与案例身份

每个案例目录可以包含以下文件。缺失字段保持 `null` 或明确状态，不删除失败和部分完成的目录。

|文件|读取用途|缺失处理|
|---|---|---|
|`assignment.json`|进入前保存的案例编号、问题、协议、survey_seed、fit/development、环境、候选机制|旧日志使用追溯整局哈希并标为探索；不能追称前瞻留出|
|`requests.jsonl`|真正收到的 accepted 响应、动作位置与频道、虚拟时间|不能从决策意图补造观测|
|`decisions.jsonl`|匹配阶段、站点、冻结调查设计、外包面积|匹配不到的字段保持未知|
|`result.json`|完成状态、耗时、路程、测量和清除成本、停止证据|未完成不是较快完成|
|`post_exit_audit.json`|退出后 UI 或本地事后适配器给出的 N、Ndir、案例编码|没有 N 或 Ndir 就不进入该局联合组成拟合|

支持审计字段 `N` / `source_total_post_exit` 及 `Ndir` / `directional_total_post_exit`。问题3缺失 Ndir 时可根据题设写为 0，并保留 `Q3_omnidirectional_fact` 来源标记；问题4缺失 Ndir 不可写成 0。合法支持为 10≤N≤16、0≤Ndir≤N。越界数量作为审计问题保留，相应拟合值置空。

首轮设计为每问 60 局 baseline 和 20 局 survey。采集器在启动前按问题和协议内哈希排序分成 3:1，`assignment.json` 中声明的划分具有优先权；校准器不会改成另一种划分。模块提供的 `assign_split(case_key)` 是按固定盐、整局标识的 SHA256 阈值划分工具，适用于另外需要该规则的预分配过程，与协议内精确 3:1 排序不是同一个规则。

动作行从不独立划分。一个案例的复制日志发生 fit/development 冲突，或同一案例的 N/Ndir 冲突时，相关实例整体标成 invalid，退出拟合和候选比较。只有 assignment 而没有启动标记、请求、结果或审计的目录是计划案例：保留目录行，但不进入实际尝试、完成率和成本分母。

`case_instance_id` / `case_group_id` 表示分析实例。官方复制日志按案例编码识别；本地实例还包含候选机制和协议，避免不同半径、误差机制共用 scenario_id 时互相覆盖。`resampling_group_id` 另由 `common_case_id` 产生，保留本地不同机制共享底层场景的关系。逐候选指标先按该整局组聚合；不同候选的比较仍相关，不应当作独立重复证据。

## 真正接受的动作和反馈条件

公共函数 `unique_accepted_actions(request_rows, issues)` 只返回 `response.accepted is True` 的动作。相同 request_id 的超时或拒绝不消耗该 ID，后来收到 accepted 才计一次；重复返回的 accepted 不重复计费、不重复测量。相同 ID 出现不同动作正文时保留首次确认动作并记录冲突。缺失 ID 的 accepted 保留但标出无法证明幂等去重，不能悄悄丢弃。

测量的反馈类别只有 direction、near、no_signal。direction 必须有有限的 `svd_deg`。阶段信息优先通过 request_id 与决策匹配，否则使用频道、坐标、返回虚拟时间匹配。测量计数由 accepted 反馈产生，不能由发送请求数量或决策计划数量替代。

条件表按以下维度分组：问题、采集协议、阶段、站点、测前存在认知、该频道测前是否已清除、事后存在证据。另保留 any_cleared_before，用于确认固定扫描发生在任何清除之前。

- 测前“已知存在”只能由此前正观测或成功清除建立。
- clear 失败不证明频道不存在，也不证明机器人没有移动。
- 事后“已确认存在”可来自本局任意正观测或成功清除；这是离线分层变量，不能回流在线策略。
- 只有完整局的停止证据才能提供 certified_absent；其他未证实频道保持 unknown。
- 未测不是 no_signal。清除后的 no_signal 与清除前的无信号单独条件化。

## 联合组成模型

模型估计 P(N,Ndir)，不把 P(N) 和 P(Ndir) 两张独立直方图相乘。仅官方 fit 中已知完整二元数量的整局各投一票；development、本地复制、未知联合数量都不进入经验项。失败局只要事后数量已知，仍可贡献组成票数。

三个候选共同输出，后续模拟应记录实际选择了哪一个。

|候选|定义|证据性质|
|---|---|---|
|empirical_joint|已观测合法二元格子的频率|保持数量相关性；零频格子不代表真实不可能|
|smoothed_joint|经验计数加总权重为 2 局的联合先验，再归一化|防止有限样本把合法组成完全排除|
|broad_joint|N 在 10…16 上均匀；问题4的 Ndir 在给定 N 下均匀|明确的宽假设，不是拟合结果|

问题3先验只支持 Ndir=0。问题4先验先选择 N、再选择 Ndir|N，天然满足 Ndir≤N。平滑总先验权重默认 2，可由命令参数明确更改。没有已知联合观测时，经验候选为空并标 unavailable，平滑候选标 prior_only，禁止把宽先验冒充已校准组成。

`model.json` 的稳定消费接口如下；省略号是文档省略，不是实际 JSON 内容。

```text
schema_version = 1
model_id = composition-<composition-model hash>
model_status = exploratory | fitted_composition_only
provenance = roots, compare_roots, n_official_attempts, n_official_cases,
             n_fit, n_planned_unstarted, retrospective_assignments, split_unit
problems["3" or "4"].count_distribution = [{n, n_directed, probability}, ...]
problems[...].composition_candidates.empirical_joint.count_distribution
problems[...].composition_candidates.smoothed_joint.count_distribution
problems[...].composition_candidates.broad_joint.count_distribution
problems[...].n_fit_known_joint
problems[...].empirical_cell_bootstrap
uncertainty_preserved = radius_models, error_models, layout_models, orientation_models
mechanism_calibration_status = awaiting_local_protocol_replications | observable_checks_only
```

默认 `count_distribution` 指向平滑联合分布。`model_id` 随组成模型而变，添加相同协议的本地比较日志不会单独改变组成模型标识。源文件 SHA256 另保存在案例特征行中。任一问题 fit 已知数量少于 30 局时自动标 exploratory；30 只是保守标记阈值，超过它仍只能称“拟合了组成”，不是充分校准的证明。

## 固定调查的联合可见性

SurveySolver 先在冻结路线的每个站点按 1…20 测量全部频道，全部扫描和冻结追加测量结束前不进行清除。分析器读取 start 事件中的计划坐标，不从已经看到的正信号反推“计划站点”。各格子的第一条清除前 accepted 观测进入矩阵。

`visibility_matrices.jsonl` 按站点保存 20 频道的反馈及 mask。`channel_visibility_patterns.*` 则转置这个矩阵：每个频道保留跨所有计划站点的 visibility 向量，未观测的坐标仍为 null。这样可以检查同一频道是否同时在多个站点可见，避免只比较站点边缘平均值。

只有全部计划站点、全部 20 频道均在任何清除前被观测，才生成以下局级联合概率。模式表本身始终保留，包括扫描失败或提前中止的局。

1. 已确认存在频道的可见站点数 K 的整局分布，输出 K=0…站点总数的所有格子及均值。
2. 六个固定相邻站点对的 P(both visible) 与 P(discordant)，分母是本局已确认存在频道数量。
3. 已确认存在频道数与事后 N 是否一致，用于发现组成或观测账本不完整。

站点对仅按计划坐标的距离排序选择，距离并列按路线索引排序，绝不根据反馈挑对。此规则在当前分析器中固定；旧探索日志不能追称采集前已预注册。不同实际坐标会产生不同设计 hash，不能仅因站点编号相同就强行合并。

每局先生成概率，随后以整局为重采样单位。一个局中的频道和站点相关，不能把 20×站点数的格子当成独立样本。完整联合模式仍不能单独识别每个源的 R、类型或朝向。

## 方向、几何与成本代理

同点复测记录报告方位的圆周差，邻点记录至此前已观测近点的位移和报告差，圆周差归一到 [-180°,180°)。这些都不是相对真实方位的误差。邻点的方向差还含真实几何方位变化，因此不能用于直接拟合传感器误差方差。

Survey 的距离和方位分层使用冻结估计点的 nominal_distance_m 与 nominal_bearing_deg。估计点不是真源；所谓距离箱是名义距离箱。measurement_design 中保守距离区间、探针类型和生成方法保留原状供审计。

外包面积收缩是求解器几何状态的代理，不是真实定位误差。该代理受测点选择和历史状态影响，应在同协议、同阶段下比较，不能把不同策略之间的收缩差全部归因于噪声。

成本先报告所有实际尝试的完成率，再报告完整局耗时、路程、测量数、清除尝试数和失败清除数。所有尝试的已观测耗时单列；失败局的短耗时只是截止停止时的投入，不是快速完成。没有耗时字段的失败案例仍进入尝试和失败分母，对成本均值则保留缺失。

`joint_constraints.jsonl` 只保存公开相容约束摘要：每个源共享一个 g、一个 R∈[1000,1500] 和固定类型、朝向 u。方向反馈限制 g 所在扇区及距离，near 提供近距约束但仍受辐射条件限制；no_signal 含不存在、已清除、超半径或落在辐射半平面外等析取。成功清除约束 g 位于提交点的 20m 闭圆盘，提交点不是 g。这里不声称完整解出联合潜变量可行集；更完整的后验归档由独立模块承担。

## 留出预测和候选机制筛查

`predictive_checks.json` 对官方 development 输出联合组成 NLL、Brier、预测概率为零的次数，以及能够匹配条件的反馈 NLL。NLL 中零预测概率不会被悄悄加 epsilon：完整均值记 null，并报告零概率次数。反馈条件表另用每类 0.5 的显式平滑以作可观察预测检查，不据此声称拟合了误差场。

`model_checks.json` 逐候选、逐问题、逐协议比较官方 development 与本地相同 baseline/survey 协议。相同协议意味着复用冻结 Solver/SurveySolver、站点、频道顺序和调查设计；候选策略 L1…L5 的不同主动测点日志不能冒充机制校准协议。局级重采样也保留 shared common_case 关系。

单个标量至少每侧 5 局才计算区间判别。实质容限是本分析规则：N/Ndir 为 1 个源，反馈概率、联合概率、可见率和面积比为 0.10，报告角差为 0.5°，其他数量、路程、时间为 max(1,官方均值绝对值×25%)。这些容限不来自题面，修改须记录新分析版本。差值定义为本地减官方。

800 局比较使用 `joint-visibility-case-boundaries-v2`。对全零或全一的局级率，普通 bootstrap 会退化成零宽区间，因此额外使用独立整局上的极端事件保守界，并扩大差值区间。对于 Y∈[0,1]，E[Y]≤P(Y>0)、E[Y]≥P(Y=1)，可以用整局是否出现非零或满值的二项事件界约束均值；每总体每尾分配 0.0125。这里绝不把同局的动作、源或站点当成独立试次。只有 5 局官方均为零时，不会因为本地也全零就自动标 matched。原官方组成模型及其 SHA256 不受这个比较阶段的修正影响。

|输出状态|条件|允许的结论|
|---|---|---|
|单项 matched|95% 整局 bootstrap 差值区间全部进入实质容限|该可观察量在指定容限下兼容|
|单项 screen_flag|差值区间全部落在容限外|探索性偏差信号，需检查设计与复现稳定性|
|候选 biased|同一个核心联合可见性指标在预定整局哈希两半均同方向超界，且各半每侧至少 5 局|降低候选优先级的探索证据，不是正式拒绝真实机制|
|候选 matched|联合组成、必需成本/探针、完整可见站点数分布及固定站点对指标均兼容，且无筛查标记|所测可观察指标兼容，仍保留未识别的机制假设|
|insufficient_evidence|缺字段、样本少、区间跨边界或必需联合证据缺失|保留不确定性，不能默认验证通过|

联合组成使用整局 (N,Ndir) 对的经验总变差、置换筛查和 bootstrap 区间；总变差上界进入 0.25 容限才可给组成兼容标记。小样本、稀疏格子的 bootstrap 有局限，不将未拒绝等同于等价。多指标区间和哈希两半稳定性检查都不是整个指标族的同时显著性保证。

baseline 往往穿插清除，不能提供完整清除前联合矩阵，因而该协议单独不会满足全部机制 matched 门槛。首轮每问 20 局 survey 的 3:1 划分只有 5 局 development，也不足以满足双半稳定偏差门槛。增加本地案例不能制造新的官方证据。此时允许输出筛查标记、建议增加下一轮官方调查，但不能为了给出模型选择而放宽状态含义。

若 development 被用于模型保留或后续策略选择，它已被消费，最终验收必须使用下一轮未见案例。不得反复查看同一留出集后仍称其独立最终测试。

按已存在频道归一化的可见率仍受 Ndir/N 的混合比例影响。组成未显示兼容时，可见性或成本偏差可能由组成造成，不能单独归罪于半径或误差场。候选完整组合的探索偏差与“排除某个半径/误差因素”分开记录；将因素标为已匹配还要求组成层兼容。在 unresolved 中保留一个未被排除的候选，不等于满足匹配门槛。后文另有不依赖总体组成拟合的逐源逻辑排除见证。只在一个组成候选下复现过某种机制，不代表它与所有组成候选的跨积都被直接验证。

## 命令和输出

在 `B_solution` 目录运行；模块只使用 Python 标准库，测试需要 pytest，不需要 GPU。

```powershell
python scripts/calibrate_round.py --input results/official_practice --output results/calibration/exploratory_practice --exploratory

python scripts/calibrate_round.py --input results/round1/cases --output results/round1/calibration --bootstrap 500

python scripts/calibrate_round.py --input results/round1/cases --compare results/round1/protocol_checks --output results/round1/calibration_with_protocol_checks --bootstrap 500

python -m pytest tests/test_calibration.py -q
```

`--input` 和 `--compare` 均可接多个根目录，递归发现案例目录。请将输出放在独立目录，并在全部采集结束后制作最终快照；采集中运行只能视为部分探索快照。`--exploratory` 可强制保留探索状态。任何一次运行都不会启动正式或演练案例。

|产物|内容|
|---|---|
|model.json|联合组成候选、来源、保留的半径/误差/空间/朝向假设|
|report.md|样本、缺失、成本、可观测证据与状态的中文报告|
|predictive_checks.json|development 联合组成与条件反馈预测|
|model_checks.json|本地各候选的逐指标区间、筛查标记、联合证据门槛|
|mechanism_assessments.json|逐机制汇总证据及 matched、biased 或 unresolved；保留所有已比较机制|
|mechanism_candidates.json|供后续模拟消费的候选列表，剔除探索性稳定 biased，证据不足明确标 unresolved|
|cost_summaries.json|全尝试与完整局分开、按候选分开的成本|
|features/cases.jsonl、cases.csv|失败、计划、缺失、整局划分、来源哈希|
|features/measurements.jsonl、measurements.csv|唯一 accepted 测量及真实条件标签|
|features/visibility_matrices.jsonl|每站 20 频道矩阵与 mask|
|features/channel_visibility_patterns.jsonl、.csv|每频道的完整跨站模式与 mask|
|features/joint_visibility_case_summaries.jsonl|每局已存在频道的可见站点数分布|
|features/joint_visibility_pairs.csv|固定相邻站点对的联合概率|
|features/response_conditionals.jsonl、.csv|官方 fit 中按真实条件统计的反馈|
|features/observable_pairs.csv、contractions.csv|同点/邻点、报告角差和外包收缩代理|
|features/joint_constraints.jsonl|共享潜变量的公开相容约束摘要|

## 已执行的探索性验证

校准模块 19 项测试通过，覆盖联合组成相关性与归一化、未知字段、问题3题设零定向、幂等动作、清除后条件、未测掩码、圆周差、失败保留、整局划分冲突、本地不入官方拟合、计划目录不计尝试、模型实例不冲突、“站点边缘完全一致但同频道跨站联合关系不同”的反例，以及小样本全零/全一事件不能靠退化 bootstrap 自动通过兼容门槛。

已实际执行旧官方演练日志与本地 18 次机制 bench 的联合解析，输出 `results/calibration/exploratory_protocol_smoke`：23 个目录，其中官方 5 次尝试、4 次确认进入；6,151 条唯一 accepted 测量；440 条频道联合模式；6 局完整 survey 扫描。12 个候选×问题×协议检查均为 insufficient_evidence。旧官方日志没有本轮统一协议，不能强行与本地 baseline/survey 合并。

本地 bench 包含平滑组成、min/mixed/max 半径和固定位置 hash 误差的 3 个候选，每个候选的问题3/4各 baseline 2 局、survey 1 局；组成来源仅旧 4 局官方探索数据。以上仅为历史解析和口径测试。后来实际完成的 160 局首轮产物及 960 局同协议检查见下节，不覆盖这些历史探索记录。

## 独立请求重放与冻结完整性审计

`scripts/audit_round_integrity.py` 不导入 Solver、RobotClient 或模拟器的计时实现；它从请求坐标、动作类型及 accepted 反馈独立重算每次移动、接收频道、测量次数、清除次数和成功频道，并检查每条 state_before / state_after 和响应虚拟时刻。

独立总时间公式为 T=L/5+switches+5×measures+3×clear_attempts+2×successes。失败 clear 仍移动但不换接收频道；拒绝、传输失败和已确认 ID 的缓存重放不再次修改状态。新 ID 对同一频道重复返回 success 是审计问题。相同 ID 不同正文、未决响应后换用新 ID、完成但未确认 exit 均单列。

时间审计报告真实最大残差；由于官方虚拟时间戳以微秒输出，判定容限为每个唯一动作 1 微秒再加 1 微秒初始余量。位置与频道、整数计数使用独立检查，不用时间容限掩盖计数错误。

调查审计根据实际 accepted 请求证明每个固定站点的 1…20 频道顺序，问题3覆盖 140 次、问题4覆盖 620 次；按 request_id 将真实动作与 phase 匹配，检查追加探针与冻结计划的坐标、频道、站点及 hash 一致，清除发生在全部调查完成之后。重新计算覆盖坐标 hash、计划 hash、预设 seed 的目标抽样结果，而非只比较日志里两处相同字符串。

完整局还检查事后 N 与唯一成功清除频道数一致、原 `.jlog` 副本文件名对应问题和案例编码、字节数及 SHA256 一致，10 个核心文件的当前内容与冻结 ZIP 中内容分别匹配冻结清单，调查源码匹配独立冻结 hash。审计只读取已公开行为日志副本的字节以做哈希，不打开隐藏场景文件。

```powershell
# 采集中：只审计已完成且已事后审计的稳定前缀
python scripts/audit_round_integrity.py --round-root results/round1 --output results/round1/integrity_prefix --complete-only

# 全部采集结束后：审计全部实际尝试；planned 仍单列
python scripts/audit_round_integrity.py --round-root results/round1 --output results/round1/integrity_final

python -m pytest tests/test_round_integrity.py -q
```

输出 overall.json、overall.csv、cases.json、cases.csv、issues.csv 和逐请求 actions.csv。审计不存在的问题不会创建假错误行；空 issues.csv 表示当前审计范围内未发现问题，范围和跳过项以 overall.json 为准。11 项独立审计测试已通过，包含第二轮两臂计划的冻结核对，以及 rejected 响应的占位时刻 0 不覆盖累计状态、被拒绝 ID 修正后可合法复用的反例。原 160 局全部 accepted=true，故该故障覆盖补强不改变其历史审计结论。

最终 `results/round1/integrity_final/overall.json` 审计了全部 160 局、53,959 条请求及唯一 accepted 动作，0 问题、0 未启动、0 跳过。独立重算 46,367 次测量、7,272 次清除尝试、2,077 次成功；40 局调查取得完整固定站点证书。最大逐响应计时残差为 1.2529264495×10⁻⁵ 秒。该结果来自全部采集正常停止后的完整快照。

## 首轮实际数据、机制筛查和唯一冻结选择

160 局官方案例中每问 baseline 60、survey 20；按预先整局哈希分为 120 fit、40 development。`results/calibration/round1/model_frozen.json` 仅拟合联合组成，SHA256 为 `975a6a9c8090c0416240afe056bc3d0cae503350b75b918a065d1a0721ab1e74`。原模型及原分析源码副本保持冻结。

P4 的 20 局 development 有 9 个组成落在经验分布的零概率格子。经验模型的负对数分数因此不可有限化；平滑联合模型平均 NLL 为 5.5733、Brier 为 0.99627，宽联合先验为 4.6003、0.99012。没有根据这些结果重调平滑强度；选择保留 smoothed_joint 与 broad_joint 是已经消费 development 的探索决策，不能把原 development 再用作最终独立验证。

同协议本地检查实际完成 800 局主设计与 160 局宽组成补充：主设计为平滑组成下 mixed/hash、mixed/correlated150、mixed/extreme、全 R=1000/hash、全 R=1500/hash 五种；补充仅是 broad_joint×mixed/hash。全部 960 局完成清除、0 已报告外包违规。`results/calibration/round1_checked_combined` 保留全部 24 个候选×题号×协议组，原统计状态均为 insufficient_evidence；不以本地样本数弥补官方每问 development survey 仅 5 局的不足。

原统计状态之上有独立筛查层，结果与证据 SHA 保存在 `mechanism_screening.json` 和 `post_screen_report.md`：

- 公开观测与最终保守外包产生 2,030 条带数值护栏的逻辑见证，排除“所有源 R=1000”与“所有源 R=1500”这两个点质量候选。P4 对 R=1500 不使用无信号推理；完整共享候选已被 P3 证据否定。证据目录为 `results/calibration/radius_extreme_witnesses`。
- 预冻结 1 m 邻点设计中的报告角差在官方 P3 fit 15 局、development 5 局均很小，而平滑及宽组成的 mixed/hash 本地参考均表现出约 0.7° 的均值。四个局级差值区间下界均超过预定 0.5° 筛查容限，故移出这个精确定义的 hash 实现。该量仍含几何变化，是同协议联合反馈代理，不能称真实误差。诊断为事后解释性筛查，未宣称全指标族显著性控制。证据目录为 `results/calibration/round1_mechanism_diagnostics`。
- 对 correlated150 精确公式和 ±1° 极值公式，独立邻点约束审计覆盖 40 局调查、160 对可评估观测，0 错误、0 排除见证。只有出现见证才排除，零见证不等于验证通过。证据目录为 `results/calibration/neighbor_error_witnesses`。

第一阶段唯一机制输入是 `results/round1/mechanisms_selected.json`，SHA256 为 `76fd7735a152f6f7c201a0d736945a3847af8eaa7ea780aa8bd341ae6ff7ca1e`。其中保留 mixed/correlated150 与 mixed/extreme150，均明确 unresolved。原有六个完整组合、五个物理参数候选和 24 组统计记录不被改写。被筛出的合法机制仍可用于 broad/stress 压力测试。broad_joint 与两个存活噪声族没有完整同协议直接验证，其交叉组合属于待检查的不确定性范围，不能宣称已恢复官方生成器。

## 第一阶段开发集独立完整性审计

`scripts/audit_local_stage1.py` 不导入 solver 或 simulator，只复用独立请求审计器。它先按 manifest 的 development 标签选局，然后才访问结果路径；confirmation 结果不打开。除冻结源/输入 ZIP 和当前文件哈希、场景内容哈希、整局预分割哈希秩外，还检查每局四臂配置仅有规定的 L 值差异、请求状态和计数、失败惩罚，以及本地固定源和误差场实际产生的每条 accepted 反馈。

本地计时同时做两种核对：连续公式 T=L/5+switches+5×measures+3×clear_attempts+2×successes，以及每次移动先舍入至微秒的精确整数累计。后者要求逐响应和最终时间完全相等。固定场反馈核验是本地数据完整性检查，不是官方误差模型真实性检验；不会读取任何官方隐藏参数。decision 日志仅核对起始冻结配置并保存完整文件哈希，不宣称独立重算后续所有几何外包。

```powershell
python scripts/audit_local_stage1.py --input results/round1/local --output results/round1/local/integrity_development_prefix --complete-only
python scripts/audit_local_stage1.py --input results/round1/local --output results/round1/local/integrity_development
python -m pytest tests/test_local_stage1_audit.py tests/test_round_integrity.py tests/test_calibration.py -q
```

新审计器 11 项构造测试通过，包含重哈希仍能发现非法划分、结果计数/误差场篡改、反馈与本地真值矛盾、冻结 bundle 的真实映射哈希含义，以及 confirmation 目录即使放入不能解析的 JSON 也不会在开发审计时被打开。确认访问必须显式提供冻结选择文件，且仅打开选中臂。运行中的不完整局单列 pending，不记作短时成功或已证实失败。

第二轮官方计划是未配对的独立 baseline/candidate 两臂。已提前冻结的 `results/round1/validation_precision_decision.json` 选择 5% 相对效应作为规划值，每问最多 60 局、每臂 30 局；在首轮方差近似下，P3/P4 功效约 0.458/0.604，预算并不足以可靠区分 5% 改善。后续审计应核对实际 plan 与该设计、候选源码冻结和分配表一致，不能报告官方同局配对区间，也不能将新 validation 局重新纳入 fit。

## 全部已选本地阶段的最终独立证书

|阶段/分区|不同场景|审计局臂|唯一 accepted 动作|结论|
|---|---:|---:|---:|---|
|Stage1 development|800|3,200|922,592|0 问题|
|Stage1 confirmation，仅冻结 L1/L5|200|400|121,000|0 问题|
|Stage2 development|320|640|182,859|0 问题|
|Stage2 confirmation，P3 单臂/P4 双臂|80|120|41,312|0 问题|
|Stage3 development|320|960|266,152|0 问题|
|Stage3 confirmation，仅保留 joint 单臂|80|80|21,412|0 问题|

合计 5,400 局臂、1,555,327 条动作、175,680 个方向报告独立重算一致。每次移动先舍入至微秒的精确累计全部一致；连续成本公式最大残差约 1.0215×10⁻⁵ 秒。Stage1 未选中的 L2/L3 确认区共 400 次运行始终不读，所以以上不是“全部 5,800 次物理运行逐动作都经独立审计”；该部分的全清可靠性来自冻结运行器聚合。各 `integrity_development` / `integrity_confirmation` 目录保留 `audit_sources` 对应版本，便于复现历史证书。

`scripts/audit_local_candidate_stages.py` 另核验后续场景与所有前期世界不共用 seed 或固定源/误差世界、实际 `arm_definitions` 只有声明因素变化、NoSignal 记录的分支权重/分项得分/最小值选择及后续真实测点相符。完整基线回退保留原选择器日志格式，不伪造无信号分支概率。5 项构造测试通过。该代码审查确认 NoSignal 只使用观测账本、候选状态和机器人已知位置，未访问模拟器真值；有限状态权重仍是启发式，未校准为真实后验。

```powershell
python scripts/audit_local_candidate_stages.py --input results/round1/stage2 --output results/round1/stage2/integrity_development
python scripts/audit_local_candidate_stages.py --input results/round1/stage2 --output results/round1/stage2/integrity_confirmation --partition confirmation --selection results/round1/stage2/selection.json
```

`scripts/diagnose_local_measurement_outcomes.py` 已独立对齐 Stage2 的 760 局日志、165,510 次 accepted 测量。输出 `results/round1/stage2/measurement_outcome_diagnostics` 按实际 measurement_reason、分区、池、组成、机制、策略分开。P4 每局局部测量次数不变，开发四设计组平均多取得 1.0625–1.25 次方向、少同量无信号；确认区为 0.875–2 次。整局清除尝试也减少，但触发兜底的目标数没有一致下降，不能把结果写成“更少进入兜底”或已证实某一种因果机制。P3 开发反馈与成本完全一致。

## 第二轮计划的独立核对

`scripts/audit_validation_design.py` 根据首轮之后、Stage1 性能审阅之前冻结的精度决策核对实际第二轮计划：每问 60、每臂 30、相对效应 .05、先验方差与功效字段、固定 seed 的每块 2+2 分配、候选策略和文件哈希。它不读取结果、不派发动作。2 项构造测试通过；实际计划核对也为 0 问题，见 `results/round2/design_integrity.json`。这里“通过”只指计划一致，不代表官方新结果已经完成。

```powershell
python scripts/audit_validation_design.py --plan results/round2/plan.json --precision results/round1/validation_precision_decision.json --output results/round2/design_integrity.json
python scripts/audit_round_integrity.py --round-root results/round2 --output results/round2/integrity_prefix --complete-only
```
