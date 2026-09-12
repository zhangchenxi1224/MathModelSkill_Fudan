# 369 → 960 → 240 主线本地实验

本目录独立保存源码快照、模型、场景和实际日志；不修改父项目原策略，不访问官方HTTP接口。

## 实验口径

- 369局真实官方记录，按案例编码去重。公开的N/Ndir用于数量组成拟合，精确位置/半径/朝向/误差场未被恢复。
- 固定先验总权重2局。旧120局始终属于开发拟合；从其余249局按题号和固定哈希留49局做模型开发检验。比较后用全部369局更新同一模型族。
- H1/H2沿用150米空间相关与极端格点误差代表模型，不把二者当作已识别官方机制。
- 320次校准对照严格复用首轮基线/调查协议，并逐官方案例匹配N/Ndir；目标布局为新合成布局。
- 960个已见场景原样配对。按新/旧联合组成比值给出重要性加权结果，另报未加权与H1/H2结果。稀疏场景及权重有效样本量不隐藏。
- 开发选择规则在protocol.json中冻结，每题最多一个候选。选择之后生成240个新场景（每题120、每个H60），只确认，不继续选参。
- P3比较主线、1150米与1200米紧致环组合；P4比较主线25点、侧线31点L2、合并25点L1及一次原地补测。
- 原地补测保持条件基线清除顺序。逻辑推演失败点与真实已执行失败账本分离；只有整20米清除圆与新外包严格分离才跳过。每目标最多一次，阈值20格。

## 运行

Python：`D:/st_python/python.exe`，核心实验只需标准库。数据来源保持原绝对路径，源码运行使用本目录快照。

```powershell
D:/st_python/python.exe -X utf8 -u run_pipeline.py prepare
D:/st_python/python.exe -X utf8 -u run_pipeline.py smoke --workers 4
D:/st_python/python.exe -X utf8 -u run_pipeline.py protocol_checks --workers 6
D:/st_python/python.exe -X utf8 -u run_pipeline.py protocol_report
D:/st_python/python.exe -X utf8 -u run_pipeline.py development --workers 6
D:/st_python/python.exe -X utf8 -u select_fidelity.py select
D:/st_python/python.exe -X utf8 -u run_pipeline.py confirmation --workers 6
D:/st_python/python.exe -X utf8 -u select_fidelity.py finalize
```

数据和选择已存在时不重复prepare/select；批量执行可继续未完成任务，已有结果需匹配场景与代码散列。也可运行 `run_all.ps1` 顺序执行剩余步骤。

## 结果

- `report/results.html`：汇总和完整逐局表。
- `report/results.md`、`report/summary.json`：开发与确认结论。
- `calibration/`：369官方记录来源、数量模型、同协议反馈比较。
- `data/`：960原样开发场景、240新确认场景。
- `runs/`：每次执行结果、gzip原始请求/决策日志与进度。
- `selection.json`：在新确认前冻结的每题候选。

本地全清不能代替新的官方迁移验证；本轮没有新增官方演练或正式测试。失败局保留原耗时，候选选择使用360000秒失败损失，不能利用失败提前退出获益。

## 本轮反馈后的模型判断

H1作为主要拟真参考；H2因两题、两协议中的外包收缩偏快，保留为敏感性模型。P4旧场景覆盖新组成质量约56%，加权有效样本量仅约20—22，因此在查看候选性能之前声明改用H1未加权开发排名，同时要求H2不退步。详见 selection_amendment_preselection.json。H1确认区间需支持改善；混合模型均值不解释为官方预期时间。最终入口为 report/mainline_results.html。


## 确认集生成前的组成模型选择

同49局保留数据中，P4宽联合模型NLL为4.6022，更新经验平滑为5.5333，差值95%区间[0.1469,1.7621]；P3为1.9459与1.9951，差异尚不明确。原有固定模型族中，宽联合的保留NLL/Brier均较低，因此240新场景采用它作为代表：N均匀10—16，P4的Nd在给定N后均匀0—N。这个选择使用官方组成反馈，未使用候选运行耗时；不宣称已识别真实官方分布。更新的369局经验平滑模型仍保留，但不强行作为唯一生成器。详见 calibration/generation_model.json。


## 已部署入口

最终结论见 `report/DELIVERY.md`，逐局明细见 `report/results.html`。推荐方案由 release.json 指定。

```powershell
Set-Location -LiteralPath 'D:/2026WorkExperience/CUMCM2026Problems/B_solution/results/official369_mainline_20260912'
D:/st_python/python.exe -X utf8 -u solve.py --local-case data/confirmation240.json --case-index 0
D:/st_python/python.exe -X utf8 -u solve.py --local-case data/confirmation240.json --case-index 120
```

上面分别为P3/P4的本地演示。原始请求、决策与结果保存到自动创建的user_runs子目录，策略只能通过客户端读取观测。

HTTP入口已接入已有适配器，本轮没有调用；只有用户决定开启相应官方演练后才执行：

```powershell
D:/st_python/python.exe -X utf8 -u solve.py --http --problem 4 --robot-id '<用户配置的队号>'
```

默认地址http://127.0.0.1:2026，arena_id固定default。不会保存登录密码或自动启动正式测试。

### 数据包复现口径

压缩包保留冻结场景和模型判断的产物，因此解压后可直接复跑本地开发与确认，不必重新抓取官方记录。`run_all.ps1`会跳过已完成且与代码/场景匹配的任务；要从零复跑，可将轻量包解压至新的目录后执行（包内没有逐案例缓存，因此会实际重跑）。Python路径可在run_all.ps1中按本机位置调整。

从官方原始文件重新执行prepare需要原项目的绝对路径；本轮数据驱动的模型选择与规则修订分别在calibration/和selection_amendment_preselection.json中保存。已冻结流程应使用select_fidelity.py的select/finalize，不能改回run_pipeline.py内旧的select。

轻量包不重复收录runs/cases中的全量压缩动作日志，日志在本目录runs/。可运行 `D:/st_python/python.exe -X utf8 package_delivery.py` 重新生成交付摘要和轻量包。
