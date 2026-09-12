# 最终独立审计工具：执行前核验

工具为根目录 `audit_final.py`，测试为 `tests/test_audit_final.py`。2026-09-11完成构造测试：**18 passed in 36.68s**。所有测试仅使用自造的临时病例、元数据及请求日志；未打开真实确认/压力成绩。脚本没有导入experiment.py、methods、vendor或数据包runtime，不会执行求解器或连接官方接口。

最终阶段完成并由主执行者明确通知后，运行：

```powershell
& 'D:\st_python\python.exe' .\audit_final.py
```

默认输入为campaign/frozen_selection.json、runs/full、runs/confirmation、runs/stress；默认输出reports/independent_audit.json和reports/independent_audit.md。工具要求全部阶段plan/results/summary齐全且没有RUNNING.lock或INVALID.json，否则在打开任何结果前拒绝。已有同名独立报告也拒绝覆盖。

审计内容：

- 冻结selection、protocol、各stage plan、登记病例、输入世界hash、独立策略源与runtime副本的完整关联。
- 全部预定case×variant实际结果完整性，每局规格、题号、H标签、来源hash、源码有效标志及逐局文件和总表一致。
- 独立复算全量开发的最终选择规则，检查确认只含每题真正选中的一个候选及固定L1强冠军。
- 唯一accepted请求逐动作重新计费：移动按5m/s并复现微秒舍入；measure5s；实际调频1s；光学尝试3s；成功另加激光2s。clear不改变接收机当前频道，不额外计调频；重复幂等请求只计一次。
- 使用本地世界来源和错误场seed独立重算每次方向/near/no_signal与clear真假；仅忽略墙钟real_timestamp_ms，保留坐标、频道、反馈和虚拟时间。独立错误场转写在自造坐标/seed上与冻结runtime的六种模式核对一致。
- 逐决策序号、请求对应、源真值外包保留、停止证据的实际测点与频道账本核验；该工具不替代专项连续覆盖几何证明。
- 每题选中方案相对冠军的H1/H2与混合均值、差值、均值比改善、逐局百分比改善均值、T/N、P95、最大值、退步数、最大退步、失败和成本分项。
- 使用独立主种子202609119731，默认10000次whole-case配对bootstrap；两个确认主对比使用97.5%区间，H分层95%仅描述，开发区间不冒充独立显著性。

构造测试覆盖完整16源请求费用、clear频道语义、幂等去重、错误时间/真值/外包/调用数拒绝、错误seed识别、常数差bootstrap、未齐阶段禁止读取、完整三阶段伪造fixture的hash链和独立指标、源篡改及缺失配对拒绝、短失败保留360000s惩罚。元数据fixture为了测试边界显式关闭日志核验；正式命令始终启用逐请求和逐决策核验。

预期真实运行会核验full全部1980运行以及冻结后确认和压力全部运行；各阶段以实际plan中的预定case×variant为准，不从目录条数推算完成量。这个文件仅说明工具已准备，不包含任何真实最终成绩。
