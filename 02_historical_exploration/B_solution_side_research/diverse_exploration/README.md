# B题六路线探索：本地运行入口

实际工程路径：`D:\2026WorkExperience\CUMCM2026Problems\B_solution_side_research\diverse_exploration`。

这是参考资料六条路线的独立可运行实现。包含集合成员基线、Fisher、信息增益、两步规划、路径与共享停靠、有限清除试探。冻结的原核心位于 `vendor/bsolver`，10个文件与原joint_triangular的 `1b673484…` 版本逐文件一致；核心运行仅依赖Python≥3.10标准库，验证使用pytest。全工程默认只运行本地模拟器。

先读 [第一性原理分析与路线边界](docs/first_principles.md)，再读 [现有数据证据审计](docs/evidence_audit.md) 和 [本次交付核验](docs/delivery_validation.md)。早期审计记录的是当时快照；交付前的新机制进展见 `docs/latest_evidence_snapshot.json` 及分析文档第4节。

## 直接开始下一轮本地迭代

Windows PowerShell，先进入上述工程目录：

```powershell
.\start_local.ps1 -Workers 2
```

默认读取 `results/research_ready_final/manifest.json`：**360个未运行的新世界，240 development + 120 confirmation**。开发分两题、三池（composition_fit / broad / stress），每格40个世界，六路线共1440次运行。确认每格20个世界，最多240次运行，仅比较原基线和每题一个冻结候选。本轮已经准备好该计划，没有提前运行或挑选确认结果。

同一计划继续未完成工作：

```powershell
.\start_local.ps1 -Workers 2 -Resume
```

开发全部完成后，按manifest中的确定规则冻结候选，再开启确认：

```powershell
.\start_local.ps1 -Phase freeze
.\start_local.ps1 -Phase confirmation -Workers 2
```

候选必须各池平均惩罚时间不升、无新增失败；至少一池改善，否则保持robust。保留所有退步、失败和小样本机制层。确认文件不能用改名选择文件替换，冻结选择不能覆盖。准备阶段尚未选择候选，不能将文件存在理解为新路线已经胜出。

完整命令行入口可以从任意目录运行：

```powershell
D:\st_python\python.exe .\run.py --help
D:\st_python\python.exe .\run.py prepare --help
D:\st_python\python.exe -m pytest -q
```

脚本默认优先使用本机 `D:\st_python\python.exe`，也可用 `-Python <路径>` 替换。建议与隔壁实验并行时先用2进程；增加到4/8进程前观察CPU及规划超时率。GPU并非运行要求。

## 现成的工程验证

当前发行源码的完整局测试位于 `results/smoke_release/engineering`，状态、成本、配对退步和动作日志均已落盘。`summary.md` 为分池表，`results.json` 为逐局结果，`route_diagnostics.json` 检查规划是否实际采用、低粒子/超时回退、共享测点及清除试探。

这些样本用于工程验收，不用于决定胜出策略。最终研究计划已显式排除全部本旁支工程世界、旧未运行计划和隔壁1000世界manifest的重复种子/隐藏世界。`design_coverage.json` 列出实际组成与机制覆盖；40个Q4 broad开发世界不等于穷举98种合法组成。

## 改配置、加路线或扩规模

修改 `configs/planning_defaults.json` 或代码后，必须 `prepare` 到新目录，指定新种子并显式排除已用manifest。不要改已准备好的manifest：运行前后与每个worker都会校验源码、配置和场景散列。

下面生成一个不使用官方组成拟合的广覆盖/压力研究示例：

```powershell
D:\st_python\python.exe .\run.py prepare --profile research --per-cell 100 --confirm-per-cell 50 --seed 202609130900 --options configs/planning_defaults.json --exclude-manifest results/research_ready_final/manifest.json --exclude-manifest results/smoke_release/manifest.json --output results/next_broad
D:\st_python\python.exe .\run.py run --manifest results/next_broad/manifest.json --partition development --workers 2
```

有新的校准交接时，通过 `--calibration` 载入冻结组成模型，通过 `--mechanisms` 载入保留/未决机制列表；被排除机制不能进入composition_fit，broad/stress仍保留极端情况。默认工作粒子先验与外部场景组成模型是不同层：策略不接收当前真N、Ndir、半径、朝向或误差种子。

`fisher/infogain/rollout/probe`均为已发现频道的局部决策实现。rollout有真正的两步反馈条件化，但不是完整多频道POMCP；joint_route也未做任意连续覆盖删点。这些范围与后续扩展方向见 `docs/first_principles.md`，不能把路线名称当作已实现更大的算法系统。

## 日志、恢复与版本

每个世界/路线保存独立case、result以及完整gzip请求/决策日志。失败损失360000秒是预先规定的评价值；运行失败时不知道的实际成本保持null。正常失败在resume中复用，不能重跑到成功后删除失败。

Ctrl+C/系统终止可能留下 `RUNNING.lock`。先确认该计划没有活动执行进程，再移除该分区的这个锁文件后resume；不要同时启动两个同计划入口。半成品目录保留到 `interrupted_attempts` 后才重新派发。若出现 `INVALID_SOURCE_DRIFT.json`，该批次禁止恢复复用，应保留证据并建新计划。每次prepare同时保存可恢复的 `source_snapshot.zip`；源码起止检查是工程防护，并非对任意运行时修改的形式化证明。

`summary.elapsed_s`是最近一次调用耗时，resume验读已完成结果时可能很短；逐局program_real_time_s不被改写。首次运行耗时另在交付核验记录。虚拟时间与程序墙钟严格区分。

## Linux / 创智CPU可移植包

发行ZIP包括本地源码、配置、测试、文档、发行烟测结果和已准备的新manifest。解压后可直接：

```bash
bash start_local.sh
```

默认4个CPU进程，可用 `bash start_local.sh results/research_ready_final/manifest.json development 8` 调整。确认时需要先运行 `python3 run.py freeze-selection --manifest ...`，再给run命令提供 `--selection`。这份交付已准备Linux入口，没有创建或占用新的远程实例。

`results/smoke_v1`、`results/smoke_final`和`results/research_ready`为开发期保留记录；其中早期Windows请求日志未关闭导致压缩后删除失败，原日志仍完整保留。已修复为关闭日志再压缩，最终入口统一使用 `smoke_release` 与 `research_ready_final`，旧结果不改写成新版本。
