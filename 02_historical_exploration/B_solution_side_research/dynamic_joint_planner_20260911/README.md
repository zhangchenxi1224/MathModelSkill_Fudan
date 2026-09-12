# 跨频道短视界动态规划：独立评测候选

已实现 `dynamic_joint_h2`：收到一次真实反馈后，统一比较搜索、补测、有几何保证的清除、有限试探和光学网格清除；按反馈分支评估两步，只执行第一步，然后重新规划。它可以中断当前目标、转去另一频道，再回来处理。

这是一个待比较的实验候选。实现了动态决策并不代表比现有冠军更快；发布前小规模结果见 `docs/validation.md`。默认冠军仍为 Q3 的 L1 normal / Q4 的 L1 nosignal，两者均使用 joint 调度。

## 快速使用

Windows PowerShell：

```powershell
Set-Location 'D:\2026WorkExperience\CUMCM2026Problems\B_solution_side_research\dynamic_joint_planner_20260911'
D:\st_python\python.exe -m pytest tests/test_dynamic.py -q
D:\st_python\python.exe run.py solve --case '你的场景.json' --output 'results/你的场景_dynamic' --config 'configs/dynamic_h2.json'
D:\st_python\python.exe run.py solve --case '你的场景.json' --output 'results/你的场景_champion' --policy champion
```

输出目录必须不存在。运行只连接随包冻结的本地模拟器，没有官方或远程请求。已有场景的 `error_field.seed`、`mode`、`correlation_length_m` 会原样用于误差场；不会误用几何生成种子替代误差种子。

同样可复制整个目录到 Linux，使用 Python 3.10 及以上运行 `python run.py ...`。策略和评测器仅需标准库；测试需要 pytest。

## 加入现有多元评测

推荐让现有评测器继续创建客户端和模拟器，只把这个工厂加入候选分发：

```python
import json
import sys
from pathlib import Path

root = Path(r'D:\2026WorkExperience\CUMCM2026Problems\B_solution_side_research\dynamic_joint_planner_20260911')
sys.path.insert(0, str(root))
from dynamic_joint import make_solver

spec = json.loads((root / 'configs/dynamic_h2.json').read_text(encoding='utf-8'))
solver = make_solver(client, problem, spec, decision_log=decision_log)
result = solver.run()
# 评测器随后按自己的流程对照真值审计、保存结果，并关闭 client 日志。
```

工厂签名与现有 `make_solver(client, problem, spec, decision_log=None)` 一致。模块使用独立命名空间 `dynamic_joint._core`，不会替换已经导入的 `bsolver`。策略不接收整个 case、真实总数、真实坐标、真实类型或模拟器对象；实际接入时沿用现有 `RobotClient` 接口即可。

可注册以下配置：

| 配置 | 作用 |
|---|---|
| `configs/dynamic_h2.json` | 主候选：两步、跨频道、广泛数量先验 |
| `configs/dynamic_h1.json` | 对照：相同框架，只看一步；用于辨别两步规划的贡献 |
| `configs/official_prior/dynamic_h2_prior_p3.json` | Q3 两步 + 已冻结官方统计的数量边际先验 |
| `configs/official_prior/dynamic_h2_prior_p4.json` | Q4 两步 + 数量边际、定向源比例先验 |

后两个配置分别只支持所标记的问题。它们的来源和哈希见同目录 `provenance.json`。先验取自已有 `composition-d0efea5015d5ecb6`，其 160 个官方案例中 120 个属于拟合集。这里只导出了模型统计，没有读取其案例坐标；数量先验混合 35% 的 10—16 均匀分布，Q4 类型比例也混合 35% 的 0.5。

若要换成后续冻结的统计模型，可以导出到一个新目录：

```powershell
D:\st_python\python.exe export_prior.py --model '已冻结的model.json' --output 'configs/新先验'
```

请在评测前固定配置和先验。不要从正在评测的场景真值中估计当局总数或位置，也不要利用确认集结果反复调整同一确认集上的参数。

## 决策中实际用到的信息

- 当前位置、当前接收频道、各频道观测历史、保守位置包络、失败清除点。
- 每个频道哪些覆盖站点已经测过；不同源的已发现数和已清除数。
- 历史无信号对固定位置、接收半径、方向的共同约束；Q4 保留“超出半径或处于背面”的可能性。
- 数量约束带来的跨频道存在概率变化，以及剩余运行时间、每频道补测/试探预算。

数量推断里“已确认 K 个”包括已清除和已发现未清除。K=7 时至少还有 3 个未发现，K=15 时最多 1 个，K=16 时不再生成寻找新源的动作，但继续清除已发现源。历史概率不会直接将频道标为不存在，也不会使程序在清除 13 个等经验数量后提前结束。

默认每次最多比较 6 个候选，考虑附近 3 个已发现目标、2 个覆盖站点、最多 3 类反馈分支；两步视界，上限 250 个动作节点、约 0.25 秒规划预算。每个已发现目标最多补测 2 次、概率试探 1 次；每个未知频道最多增加 1 次非覆盖站点检测。完整状态、候选分数、反馈分支、第二步候选和预算退出原因都会记入日志。

## 输出和审计

每次 `solve` 保存：

- `result.json`：完成证据、总虚拟时间、实际耗时、计时残差、包络审计、规划完成/超时次数。
- `case.json`：该次外部评测场景的副本。
- `decisions.jsonl.gz`：每次反馈和动态候选的解释记录。
- `requests.jsonl.gz`：协议请求与响应记录。

`audit_complete=true` 要求：完整退出、模拟器确认全清、每次保守包络包含真值、存在完成证据、计时残差小于 0.001 秒且执行前后代码哈希一致。外部真值只在评测器里用于这项检查。

需要重新跑随包工程检查时，用新输出目录：

```powershell
D:\st_python\python.exe run.py smoke --output results/smoke_新编号 --workers 2 --include-h1
```

这会复现相同的 8 个工程场景 × 3 个策略，共 24 次运行；不是新的独立确认集。场景为本模块新生成的完整 10—16 源合成世界，含随机、边界、聚集、最后一个源朝外等布置，以及不同固定误差场。它们没有加入或改动主实验的 360 个数据集及当前实验任务。

数学假设、有限完成条件和当前近似见 `docs/design.md`；发布代码与配置哈希见 `release_manifest.json`。
