# 筛选数据集上的算法迭代

本工程针对问题 3、4。输入 360 个精确世界已校验，误差场按案例内 `error_field.seed` 固定重放。既有强冠军为 P3 L1、P4 L1+NoSignal；原 L5 另作历史对照。方案只根据公开题号及合法反馈行动。

最终结果见 [RESULTS.md](RESULTS.md)，具体获选参数见 [campaign/frozen_selection.json](campaign/frozen_selection.json)。数学推导见 [docs/first_principles.md](docs/first_principles.md)，逐轮机制见 [docs/round1_mechanisms.md](docs/round1_mechanisms.md)。原工程保持独立，本目录包含运行所需的冻结代码。

## 直接测试一个场景

在本目录打开 PowerShell。已实测 Python 3.13.5；运行求解器只需 Python 标准库，测试套件另需 pytest。

```powershell
Set-Location 'D:\2026WorkExperience\CUMCM2026Problems\B_solution_side_research\selected_dataset_iteration_20260911'
& 'D:\st_python\python.exe' solve_local.py --case 'dataset\official_composition_H1_H2_360_20260911\cases\round-calibrated-p3-0000-a21d558e975c4b8b.json' --output 'user_runs\p3_selected'
& 'D:\st_python\python.exe' solve_local.py --case 'dataset\official_composition_H1_H2_360_20260911\cases\round-calibrated-p3-0000-a21d558e975c4b8b.json' --output 'user_runs\p3_champion' --policy champion
```

把 `--case` 替换为自己的案例 JSON 即可；题号从 JSON 读取。问题 4 示例文件为 `cases/round-calibrated-p4-0000-320fa8012f945f59.json`。每次输出必须使用新目录。

终端输出审计是否全清、总虚拟耗时 T、每源耗时 T/N。目录中保留 `result.json`、请求日志与决策日志。具体输入字段、返回码和可移植部署规则见 [docs/usage_cli.md](docs/usage_cli.md)。当前入口全部离线运行。

## 重跑全部 360 个用户场景

```powershell
& 'D:\st_python\python.exe' run.py stage --campaign campaign --specs campaign\validation_specs.json --output user_runs\all360 --workers 8
```

这会在相同 360 世界上运行每题冻结候选与强冠军；`summary.json` 是分题、分 H1/H2 的配对结果，`results.json` 是逐场结果。`cases` 下保存压缩完整轨迹，`source` 与 `runtime` 是本轮实际执行快照。源码或冻结模拟器漂移时拒绝确认或将结果标记无效。

原 `campaign` 记录了本机模拟器的绝对路径。在其他机器或创智上，单场景 `solve_local.py` 会自动使用包内模拟器；批量实验请先按下节的 `prepare-campaign` 创建新campaign，以登记新机器的路径，再运行 `stage`。不要手工改原冻结协议或摘要。

## 改算法继续迭代

保留当前工程的交付副本，再修改工作副本中的 `methods/solver.py`、`methods/clear_cover.py`、`methods/scheduling.py` 或候选配置。用新 campaign、新 seed 登记下一轮；不能在当前冻结选择上直接改源码后继续声称是同一版本。

```powershell
& 'D:\st_python\python.exe' run.py prepare-campaign --dataset dataset\official_composition_H1_H2_360_20260911 --calibration inputs\calibration_frozen.json --output campaign_v2 --seed 202609120001 --exclude-manifest inputs\exclude_next_campaign.json
& 'D:\st_python\python.exe' run.py stage --campaign campaign_v2 --specs configs\round3.json --group-limit 30 --output user_runs\v2_screen --workers 8
& 'D:\st_python\python.exe' run.py stage --campaign campaign_v2 --specs configs\full.json --output user_runs\v2_full --workers 8
& 'D:\st_python\python.exe' run.py freeze --campaign campaign_v2 --stage user_runs\v2_full
& 'D:\st_python\python.exe' run.py stage --campaign campaign_v2 --partition confirmation --specs campaign_v2\validation_specs.json --output user_runs\v2_confirmation --workers 8
& 'D:\st_python\python.exe' run.py stage --campaign campaign_v2 --partition stress --specs campaign_v2\validation_specs.json --output user_runs\v2_stress --workers 8
```

上面是流程示例：将候选配置替换为本次实际要比较的方案；每份配置必须保留精确 `champion: {"implementation":"champion","limit":1}`。全量阶段必须包含全部 360 世界，才能冻结每题一个候选。筛选后再运行新确认集；看过确认结果后如继续调参，应另建新一轮确认世界，并把本次世界加入排除清单。

`inputs/exclude_next_campaign.json` 已包含此前三阶段的 1800 个世界以及本轮新增 288 个世界；它用于下一轮避免世界复用。所有本轮已看过的场景只能当开发证据。240 个新增 H1/H2 世界与 48 个压力世界仍是合成模拟，不代表独立官方测试分数。

## 文件入口

- `methods/solver.py`：统一策略工厂；可用 `make_solver(client, problem, spec, decision_log=...)` 接入已有合法客户端。
- `configs/round1.json`、`round2.json`、`round3.json`、`full.json`：各轮候选参数。
- `campaign`：登记世界、验证约定及不可变选择。
- `runs`：可追溯的实际实验记录；`reports`：最终独立复核。
- `docs/prior_evidence.md`：此前官方与本地实验结论；`docs/testing.md`：实现测试记录。

压缩交付包保留代码、数据、配置和逐轮汇总/计划/源码快照；体积较大的逐案例轨迹保留在原工作目录，亦可由本地重跑生成。
