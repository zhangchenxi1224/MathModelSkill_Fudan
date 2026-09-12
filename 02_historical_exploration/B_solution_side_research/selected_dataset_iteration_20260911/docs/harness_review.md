# 本轮数据重放与实验门禁独立审阅

日期：2026-09-11。审阅对象为根目录 `experiment.py`、`methods/solver.py`、数据包冻结协议/模拟器及相关导入入口；只新增本报告和 `tests/test_harness.py`，没有修改运行时。没有访问官方接口，没有读取正在执行的候选成绩或新确认/压力性能，也没有依据新确认结果调整任何策略。

本次独立测试命令：

```powershell
& 'D:\st_python\python.exe' -m pytest -q tests/test_harness.py
```

首轮结果14 passed in 12.23s；确认来源与按题选择范围补强后17 passed in 7.83s；进一步补齐强冠军范围门禁后，最新结果为 **27 passed in 12.61s**。测试运行产生的少量完整局仅用于重放一致性检查，不能计为新的独立策略验证样本。

## 1. 已核实的数据重放与公平基线

数据包实际类型为 `existing_synthetic_scenario_subset`，360世界按H1/H2×P3/P4各90。旧标签含288 development、72 confirmation；所有360在本轮统一用于development_regression，未将旧确认重新包装为新留出。病例逐个验证scenario内容hash，360个完整sources+error_field世界hash互异。

最容易发生的错误seed已被针对性检查：模拟器使用 `case['error_field']['seed']`，不能用 `case['seed']`。本包所有360病例这两个seed都不同。H1和H2均测试了同位置/同频道重复输出相同、输出位于[-1,1]，并验证替换为case seed确实会改变反馈。runner没有重新运行世界生成器来替换用户给定场景；sources、位置、半径、方向和错误场定义直接来自病例。

数据包 `bsolver_frozen` 的协议与模拟器字节hash分别为 `a75e1c41257cc6fd4debc1ec96053adf720c6e8db915765bd2ab389f3b41fe1a`、`f29b67f0cd62bc1231254eee859908d37842e43d580205cd1aa4a869215066aa`，与工程vendor对应冻结文件逐字节相同。Q4 NoSignal代码核实为 `2073393686e1538ad046965cbc3b4cf1f16b6882a157b6efa5c7855ff50be6c0`。

强基线工厂直接调用旧冻结类：P3 `Solver(L1)`，P4 `NoSignalSolver(L1, NoSignalConfig())`；不是由新的IterationSolver近似重写旧策略。两题均保持active、joint、d0=800、λ=2、原覆盖与认证配置。

独立完整局比较使用一例P3/H1和一例P4/H2，对照“原vendor协议/模拟器+直接旧冻结求解器”与“包内冻结协议/模拟器+新make_solver champion”。两侧全部全清，逐次measure/clear的坐标、频道、接受状态、方向/无信号/清除反馈和虚拟时间完全一致；总虚拟时间、移动、检测、切频道、清除尝试、成功数、兜底数及停止证据完全一致。只从比较中排除 `real_timestamp_ms`，因为顺序执行的实际墙钟本来不同。另将真实worker输出gzip动作日志与直接运行的P3冠军逐动作核对，完全一致。

求解器收到的是公开RobotClient、problem与预定spec；没有传case_id、H1/H2标签、真实N、真实源坐标、错误seed或隐藏方向。源真值只在环境构造和运行后全清/外包审计中使用。

## 2. 已反馈并已核实修正的隔离/门禁问题

初读版使用外部共享dataset runtime，并在每阶段重新采当前hash；确认病例也只有现场hash，不能拒绝开发后替换。已在阶段1派发前反馈给主执行者；修正版本现已包含：

1. prepare将development、fresh confirmation、stress的内容hash以及包内runtime原hash写入protocol。
2. make_stage先核对登记病例与runtime，再把全部策略源及三个环境文件复制到本阶段独立目录；启动入口为阶段内 `source/run.py`，runtime路径指向阶段内 `runtime`。
3. worker开始和结束都核查策略源与runtime；execute开始/结束也核查。结束source_valid不符则失败惩罚，整批源漂移不作为有效完成阶段。
4. freeze核对plan自hash、protocol hash、全部360病例内容与登记hash；缺失/重复结果、源漂移或非完整360数据阶段不能冻结。
5. 非development必须存在冻结选择、规格完全相同、源码完全相同；group_limit仅允许开发筛选，不能截断确认。

新增测试实际构造了登记病例被修改、runtime副本被改、阶段完整源与runtime复制、缺失冻结选择、截断开发冻结等场景，并核实相应拒绝。快照隔离也意味着根目录后续开发修改不会改变已启动阶段。

本次审阅时campaign登记的新确认是60/题/假设，共240个新合成世界；压力集48个。报告仅读取protocol里的登记信息与hash，没有打开其性能。登记hash如下，供最终确认前比对：

|分区|内容SHA256|
|---|---|
|development|`1a50d13e39982f05090d791108d344b5665d185ee4c8c0463d07f81661d14b05`|
|confirmation|`a1b16a51fd64b9af4d14321484946dc5f5a70f1f0186845852acc3144edeed92`|
|stress|`dcfd566ab1810aa6192f92692590ac6377484a5041035853f5ccb6536ef44ce6`|

## 3. 失败统计和选择口径

worker只有在求解器status=complete、环境all_cleared、停止证据非空、真值外包无违反且计时残差小于.001s时才标audit_complete；否则预定损失360000s。正常预算失败仍保留实际已耗时间，不能用短失败冒充快策略。构造预算不足与人工短失败分别验证了罚时及配对差，结果正确。

无成功局的p95_success为null；异常局缺失移动距离现为null并附walk_observed_n，已修正初版以0代替未知的偏差。正常失败可resume复用，不能重跑到成功覆盖记录；存在中断但尚未完成的目录先归档后重新派发。

开发选择按每题H1/H2两个均值均不劣于强冠军且候选全清，再最小化均值；输入每格90例保证合并均值恰为等权H1/H2均值。两题的开发与新确认是不同目的，最优只指此次搜索空间和数据设计，不是连续策略空间全局最优。新确认协议预定两题97.5%配对bootstrap主区间，对应Bonferroni家族5%；H分组95%区间为描述性。此处只审读协议，并未执行或审阅这些效果区间。

## 4. 确认来源绑定与按题选择范围复核

首轮审阅指出freeze没有将protocol hash存入selection，确认入口不能防止freeze后同时替换protocol与确认病例/hash。主执行者现已修复：selection保存protocol_sha256，非development入口强制相同。新增测试实际构造“冻结selection后同时替换确认世界与其protocol登记hash”，入口以`campaign protocol changed after selection`拒绝，且未建立执行目录。本轮没有实际修改登记确认世界，测试仅用临时合成文档。

后续还发现按题范围边界：若开发候选显式声明problems=[3,4]却只被P3选中，原freeze会把原适用范围[3,4]带入确认，让P4多执行一个未选中的候选。主执行者已改为按实际choices累积范围：首次选中只记[problem]，同名被另一题选中再合并。新增两个构造测试分别验证不同候选分题获选、同一候选两题获选；输出范围分别正确为[3]/[4]或[3,4]。这些均用虚构unit-test统计隔离派发逻辑，没有读取真实确认成绩。

另一个已修正的防御性边界是只检查champion名称、未强制覆盖两题。此前若所有spec都限制为problems=[3]，360世界plan可能只要求180个P3运行，P4被回退到未执行的champion。主执行者现已在stage与freeze共同调用validate_specs：除了可选的problems字段外，champion必须精确为implementation=champion、limit=1，题号必须覆盖[3,4]。新增测试分别从两个入口验证拒绝仅P3、legacy_L5、L2及偷偷修改d0的控制规格；默认两题与显式[4,3]范围均可接受。所有27测试通过，当前审阅发现的确认绑定及按题派发边界已解决。原真实`configs/round1.json`从一开始就是L1两题冠军，所以此边界没有污染已启动阶段。

异常抛出导致的harness_failed仍可能将实际已耗时间记null；主损失360000不会丢失，因此失败不会得到选择优势。若异常发生在已执行部分动作后，最终报告可从保留请求日志另行恢复已耗时间，不能把null写成0。当前独立测试针对正常预算失败及重放完整局，不冒充所有运行日志的逐动作独立计费审计。

## 5. 审阅版本

首轮14测试通过时：

|文件|SHA256|
|---|---|
|experiment.py|`9007ec37794e0ef725283b0497fce64f295daaf6168fb67c034f1c0c6902dcee`|
|methods/solver.py|`d2d46aba757b5c00372948532e07c767f3497ab361cc8ebe4f2cc05bdc39963d`|

读取阶段1 `runs/round1/source/experiment.py` 核实与上述runner hash相同。仅核实源，没有读取该阶段正在产生的性能。其余几何清除覆盖与调度算法的正确性应结合对应专项审计与测试，不由本报告越界代替。

17测试通过后读取的根目录runner hash为`2e9c65e6c78ab0435dc5d808c79112aecb22a5eae1d0abf22af4f122da198115`；methods/solver.py为`591a953f4dc553cd7d137ed03ca903294b6fc5a0a49d33088a2d80a0d7492c14`；test_harness.py为`50c6f95cea2fa5f540f99b15584acd24603bcdb10d220c6891c1f5a7461d7d29`。这些后续根目录变化不改已启动阶段的独立快照，最终冻结必须绑定实际最后全量开发阶段的源码。

27测试通过后读取的runner hash为`4913f18c4862c9ad1ee9268561702c1738ccb7dbe4a8a32a4345b8a96b927d4d`；methods/solver.py保持上行`591a953f…`；test_harness.py为`11a7dd61d4efe6b2e66c090abfcbac7dd96fd13e9dd911935e419e9b95af7d6d`。
