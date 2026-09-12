# 新筛选数据集迭代前：历史实验与可复用策略证据

审阅日期：2026-09-11。本文件只读历史汇总、冻结配置、源代码及明确列出的旧确认结论；没有运行官方 API，没有改动主工程，没有打开本次用户 H1/H2 360 数据集的性能或留出结果。历史报告中“尚未执行”的叙述按时间快照解释，最新状态以 round2 的已完成验证为准。

路径约定：`M = D:/2026WorkExperience/CUMCM2026Problems/B_solution`，`S = D:/2026WorkExperience/CUMCM2026Problems/B_solution_side_research`。下面相对路径均带 M 或 S 前缀。

## 1. 当前最强且有独立官方演练支持的基线

|题号|类|L|测点 sensing|调度 scheduling|覆盖|其他参数|
|---|---|---:|---|---|---|---|
|Q3|`bsolver.strategy.Solver`|1|active|joint|原点+半径 900√3 的正六边形|d0=800 m，bin=4°，λ=2|
|Q4|`bsolver.nosignal_sensing.NoSignalSolver`|1|active|joint|triangular，spacing=None 即 950 m，31点|d0=800 m，bin=4°，λ=2|

两题均为 `epsilon_deg=1.0051`、`clear_radius=19.999`、`nearest_safe=true`、`opportunistic_known_measurements=false`、`reserve_real_s=5`、`max_virtual_s=360000`。题面误差是 1°；1.0051° 是实现额外保守量，不能反写成题面规则。

Q4 NoSignal 参数：`directional_prior=.5`、`position_sample_count=7`、`radius_samples=[1000,1250,1500]`、`orientation_sample_count=24`、`interior_fraction=.75`。它是在原安全控制流程下替换有限候选排序，不是已校准的贝叶斯后验。

精确冻结规格为 `M/results/round2/candidate.json`，候选内容 SHA256 为 `3e305f7f13a9e22d8b37a6e3d5ab3b1321357a3a8d4c418364ca8adae5424816`。不能把最初的 L5 六路线 robust 当作唯一“当前基线”。新数据集迭代至少应包含本表的每题冠军；L5只作为历史锚点或消融。

## 2. 已完成官方验证：120 个新案例，独立分臂

来源：`M/results/round2/validation_summary.json`、`validation_summary_reviewed.md`、`M/docs/official_validation_interpretation.md`。每题基线和候选各30个不同案例，不能按序号伪配对；固定独立分臂 bootstrap 5000次。

|题号|比较|旧均值/s|候选均值/s|候选减旧/s|既定独立95% CI/s|样本均值降幅|
|---|---|---:|---:|---:|---|---:|
|Q3|L5 → L1|5043.29|4400.64|-642.65|[-826.29,-452.42]|12.74%|
|Q4|L5 → L1+NoSignal|11371.88|10663.89|-707.99|[-1117.47,-264.38]|6.23%|

四组均30/30清除完成；0失败不等于总体风险为0，每臂失败率 Wilson 95%区间为[0,11.35%]。Q4的结果是组合效应，不能拆成NoSignal单模块提升6.23%。官方看不到源真值，外包违规字段为空，不能写作“官方真值检查零违规”。公开动作独立审计120局、34861个唯一接受动作、1542次成功清除，问题数0。

|题号/臂|中位数/s|样本P90/s|样本最大/s|
|---|---:|---:|---:|
|Q3 L5|5043.95|5639.19|5680.56|
|Q3 L1|4334.27|4862.37|5223.68|
|Q4 L5|11537.49|12451.71|13157.59|
|Q4 L1+NoSignal|10659.34|11254.32|11780.68|

组成有限样本不平衡：Q3源数均值13.27→12.70；Q4源数12.53→12.90、定向源数7.07→6.03。现有随机分臂支持总体平均比较，不能宣称固定组成和所有尾部均受益。未执行正式测试。

## 3. 三阶段本地选择：哪些已确认，哪些只是未被拒绝

主要本地执行6760次：策略迭代5800次/1800不同世界，另机制检查960次。全部报告完成、外包真值违规0。独立行为审计实际覆盖5400个已选开发/确认运行、1555327请求、175680方向值，问题0；400个L2/L3旧确认结果保持未读，不可把5400审计写成5800。最新版主工程测试记录276项通过（`M/results/round2/test_run_final_delivery.json`）。

### Stage1：减少局部测向次数 L5→L1

开发800世界×4臂=3200次，另确认200世界×4臂物理生成800次。先冻结L1后只读L1/L5的400确认运行，所有8个设计组均值差及区间上界为负。下面为已选确认组，差值均为L1减L5：

|池/组成|题|配对世界数|均值差/s|惩罚时间95% CI/s|退步比例|最坏差值/s|
|---|---:|---:|---:|---|---:|---:|
|broad/broad_joint|3|40|-467.97|[-555.89,-381.85]|2.5%|+4.23|
|calibrated/broad_joint|3|20|-499.72|[-585.30,-414.82]|0%|-164.51|
|calibrated/smoothed_joint|3|20|-445.36|[-540.17,-352.81]|0%|-112.93|
|stress/stress_design|3|20|-216.20|[-283.73,-150.46]|5%|+6.18|
|broad/broad_joint|4|40|-509.31|[-633.74,-354.79]|2.5%|+1739.41|
|calibrated/broad_joint|4|20|-646.33|[-787.62,-492.56]|5%|+296.20|
|calibrated/smoothed_joint|4|20|-440.36|[-555.77,-322.44]|5%|+127.10|
|stress/stress_design|4|20|-366.93|[-505.28,-201.07]|10%|+487.64|

开发期Q4最大单例退步1279.60s，四个小机制细层均值为正但区间跨0。L1提供很强的平均节约证据，却不是逐场景支配L5。来源：`M/results/round1/local/development/review.md`、`local/confirmation/review_design_groups.json`、`M/docs/local_iteration_results.md`。

### Stage2：固定 L1，NoSignal 相对 normal

Q3逐例完全相同，保留normal。Q4开发4组均值差为-88.48、-192.43、-153.04、-234.86s；其中2组CI跨0，退步比例37.5%—51.56%，最坏单例+1027.15s。

Q4冻结确认40世界、80运行：broad n16均值-246.79s，CI[-523.86,10.16]；calibrated/broad n8均值-7.38s，CI[-313.61,266.53]；calibrated/smoothed n8均值-131.20s，CI[-459.27,197.75]；stress n8均值-620.35s，CI[-1314.73,-10.59]。三个区间跨0，退步比例31.25%—50%，最大退步+581.15s。按预定“无明确退步”的拒绝门槛保留，不构成稳定单模块优越性证明。来源：`M/results/round1/stage2/{development,confirmation}/review_design_groups.json`和`confirmation/gate_decision.json`。

### Stage3：joint 保留，另外两种调度淘汰

固定前两步后，在320开发世界×3臂=960运行比较joint、immediate、scan_then_clear。16个题/池/替代调度组均值全部比joint慢，15组CI下界为正。immediate最差组均值退步Q3 +2177.06s、Q4 +1969.17s；scan_then_clear最差组Q3 +619.91s、Q4 +854.50s。冻结后80个新世界只运行joint保证可靠性；不存在“调度改进”的确认比较。来源：`M/results/round1/stage3/development/development_gate_review.json`。

三个阶段世界不同，阶段差值不能相加得到最终收益。

## 4. 支线成果与可投入新迭代的候选

### Q3 七点收缩覆盖

`S/q3_cover_optimization/RESULTS.md` 已完整跑112局、44不同世界、8项几何测试。12开发世界四候选选1300m；32测试世界仅比冻结1300与旧布局。32例中含12历史保留例和20全新例，不能称32全新独立官方样本。

覆盖半径严格公式 `rho(r)^2=max(r^2/3,1800^2+r^2-1800*sqrt(3)*r)`。1300m对应rho=936.483374m，裕量63.516626m；保留原点、六方向、访问顺序和逐频道完整覆盖。测试均值4818.43→4673.02s（-3.0177%），23快9慢；P95 5838.65→5993.18s（+2.6466%），最坏配对退步+537.46s（+10.1175%）。仅20全新例均值降低1.6654%，其P95和最大值均上升。最坏退步主要是局部测点移动增加2428m，不能用静态环路线缩短直接推断任务收益。

接口：`ContractedCoverageSolver(client, config, decision_log, ring=1300.0)`；必须显式传1300，类默认1125是解析候选而非实验冠军。新六路线已内置等价的 `make_solver(..., route='robust', options={'ring_radius':1300.0})`。只适用于Q3，不能把该七点覆盖用于Q4。

### 六条路线 diverse_exploration

现有完整发行验证是8世界×6路线=48运行、48全清、0外包违规，38项测试；每题只有4工程世界。新增 `research_ready_final` 的360世界/1440开发运行计划仍只有manifest和源快照，没有开发成绩与冻结选择。不能把“计划已准备”写成“已证明六路线最优”。

工程烟测均值：Q3 robust4693.58、fisher4622.32、infogain4401.72、rollout4316.20、joint_route4370.96、probe4327.64s；Q4分别11063.82、10899.02、10637.76、10796.36、9933.04、10815.46s。这些仅决定值得投入新开发的路线，不能当作最终选择证据。此六路线配置来自旧L5基线，还没有和本轮L1+NoSignal冠军作大规模配对。

1099次概率规划中994采用、105因粒子不足回退，无规划超时；joint_route真实执行32次共享测向、37次未执行覆盖后缀重排；probe77次、成功56次。这是机制工程证据，不是一般成功率。

可复用接口 `diverse.policies.make_solver(client, config, route, decision_log=None, options=None)`，route为robust/fisher/infogain/rollout/joint_route/probe。概率默认粒子64、候选8、每步0.2s预算、至少8粒子、最多512位置提案、directional_prior=.5、角分箱2°、terminal_weight=1、fisher_ridge_m=1800。joint_route默认每目标至多1个共享测量、共享后验半径比门槛.9；共享测量会消耗该频道L预算。它重排未访问的同一覆盖点集合并改服务选择，没有连续覆盖删点。probability路线仍保留独立硬集合、有限试探和光学兜底。

### Q2 精度专项

Q1已有构造反例再次强调证书口径：边长39m的等边三角形直径虽小于40m，最小包围圆半径22.51666m，不能据“直径一半<20”清除。最近安全点也可比MEC圆心更省走路；例中矩形后验从当前位置(100,0)到圆心需100m，到最近安全点只需88.9223m。来源：`M/results/q12_final/q1_examples.json`。

`S/q2_certified_frontier/README.md` 的v2共336世界×4策略=1344条、20测试通过；所有策略都未丢信号、后验保留真值。precision平均MEC半径26.07m（fixed32.60m）、样本最大44.85m（fixed78.79m），但平均多走197.79m且两测进入19.999m比例25.89%低于fixed28.87%。precision_800两测成功率40.18%，平均半径33.56m。说明“更准”不自动等于“更快清除”。40官方首次上下文只做几何上界重算，新点未在官方执行。两测不可辨识下界25.7295668m意味着不能保证所有合法场景两测进入20m。可复用Q2几何作候选，但没有整局平均时间最优证据。

更早的90个合法首次合成场景×fixed/estimated/active=270条比较，第二误差固定+.75°：平均移动806.23/710.61/437.32m，平均MEC半径30.25/55.45/64.33m，两测清除比例35.56%/23.33%/38.89%，全部无no_signal；不能把这些数字与v2不同设计直接合并。来源：`M/results/q12_final/q2_summary.json`。

### 更早的消融与参数敏感性

旧32世界×6变体=192运行全清：active优于fixed类基线、nearest_safe有小幅收益，联合调度与Q4三角覆盖进一步降低时间；两题旧test各8世界joint_triangular均值5091.58/11357.91s。旧版本核心b3cdd5b…，最终1b673484…另跑12次回归、结果和动作序列一致，不能把192全部改写成新源生成。

42次敏感性只用4个预定tune世界：d0=1600相对800的两例平均配比Q3 -0.752%、Q4 -13.416%，但Q4一例10979.77→11430.14变慢，另一例12046.81→8320.19变快；λ=1时Q4 -5.130%，Q3 +.610%。bin=8相对4效应小，计算更省；三角990相对950在Q4两例平均+6.173%，方格650/700也较慢。此证据支持在新开发集探索d0、λ，不支持直接固定冠军值。来源：`M/docs/sensitivity.md`、`M/results/summary/summary.md`。

## 5. 第一性原理如何约束本轮目标

任务总成本为 `T = distance/5 + 5*measure + switch + 5*clear_success + 3*clear_failure`。新策略最终应优化完整任务成本，先满足全清、合法动作、覆盖/停止证书，随后比较均值并披露P95、最大值、配对退步与全部失败。

官方首轮各题60个正常基线局：移动占Q3 84.19%、Q4 78.31%；失败clear本体只占.69%/1.98%，大量增加检测来避免3秒失败往往不值，检测前后行走才是关键。官方新候选节省也主要来自移动和测量，即使失败clear次数Q3 11.07→18.93、Q4 84.57→87.90增加，仍整体更快。

Q4首轮coverage相关成本占64.40%，最后一次成功后仍需覆盖的尾段全体均值862.61s，最大3599.10s。该“最后一次”只有事后评估器知道，绝不能以真实N或真实已清完信号给策略提前退出；应研究覆盖点复用、合法停止证书和衔接路线。Q3局部测向前移动占36.07%，适合L预算、测点成本、清除试探和局部路线联合优化。

因此合理顺序是：用强L1冠军锚定新数据 → 对L、d0、λ、Q3合法环半径作便宜配对搜索 → 比较信息/试探/共享路线在强基线之上的增量 → 对开发最佳少数冻结 → 一次性开启本次预留确认，报告实际结果。任何有限搜索的最优都须表述为“本次候选集/开发集最优”，不能称全策略空间全局最优。

模型组成拟合并未识别位置、半径、朝向和误差联合分布。Q4 no_signal是半径/方向等析取信息，不能当作单个排斥圆硬裁剪；同一位置误差固定，重复测量不能模拟独立降噪。候选排名可使用概率假设，清除和终止必须保留硬证书。新用户筛选数据集不是官方hidden生成器的直接真值样本这一点，应由数据审计按包内实际来源确认。

旧round1最终保留的两个机制代表为mixed半径+correlated150、mixed半径+extreme150，均明确标记unresolved，而非“已识别官方机制”；来源 `M/results/round1/mechanisms_selected.json`。这也说明新H1/H2候选应分别报告，避免在某一个假设优势掩盖另一机制退步。

## 6. 冻结与当前可复用文件散列

|对象|SHA256|
|---|---|
|共同冻结10文件核心摘要|`1b673484453104a5d87d4605d30b20826a3b7ec64769fd6ce8d3c8f7a1ad24f6`|
|M/src/bsolver/strategy.py（本次只读核实仍匹配）|`4fb73ff9ec38d3d7741f25efca469ea27d77908198850b4911e461058612485b`|
|M/src/bsolver/nosignal_sensing.py（本次核实匹配冻结）|`2073393686e1538ad046965cbc3b4cf1f16b6882a157b6efa5c7855ff50be6c0`|
|Stage1选择内容hash|`d52358e443a2d32c7bca10eb8b8812c64cf64da1fbd08c82e251c7ad7c91a6d4`|
|Stage2选择内容hash|`0fabf5f0a3e515fccfafac5147c7a8edcd40387d2ac81403b4c1f15ae620200d`|
|Stage3选择内容hash|`f20b3dc942e5cb72a9c2b7c8d14a4f451af9be5ee8d7a77387626f7e090fe653`|
|官方round2计划内容hash|`d24e331e1462156a9f885e5e39428d05bee7a30ec6496ef9dcd7861e00452131`|
|官方结果原JSON文件hash（整理版记录）|`ab9d62725391922e5ee3650626ba4c6810cc4d0f6828ec2665d6b9f9bbd93089`|
|S/q3_cover_optimization/coverage_opt.py 当前文件|`e5d904293afaabe8ac469889394fd41e69094fa98c6a80c806824d58330669e8`|
|S/diverse_exploration/diverse/policies.py 当前文件|`31dcbf577223a2dc9fda9f7f910f52334db9551bb2cd7cae8c50424e57c1126e`|
|S/diverse_exploration/diverse/planning.py 当前文件|`cfbfe9b94c9bdbcd7df2089495aff822f73f4dd559c0eb6f64de5592af2ed2a0`|
|S/diverse_exploration/diverse/belief.py 当前文件|`d68fefa4ec5ba5f1314102d5b60e10d8f31383b606e7a0ac4ab0449b55e1cf60`|

内容hash与文件字节hash不能混称，selection.json有自带selection_sha256，同时round2/candidate.json还记录了选择文件的字节hash。完整逐文件核心记录见 `M/results/round1/baseline_freeze.json` 与 `S/diverse_exploration/vendor_provenance.json`。新迭代应独立快照其实际运行源及输入manifest，避免混入同时进行的其他实验源改动。

本文件为历史证据整理，不生成任何新的效果成绩，也未读取本次筛选数据集的确认表现。
