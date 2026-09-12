# 用户360场景上的综合算法迭代

这是独立本地工程，综合先前官方/本地验证、几何覆盖、紧致清除、路线重排与动态规划的实验结论。主结果见 `RESULTS.md`，最终部署配置见 `campaign/deployment_selection.json`，逐轮取舍见 `reports/round*_review.json`。

本轮主要冠军已经升级：Q3 为上轮 r1150 + bbox + hull 调度，Q4 为此前经过合成确认的25站 + RefinedLocal L1。原官方验证过的 L1 与另一条 P4 L2 路线作为历史对照。获选算法只根据公开题号和合法反馈行动。

## 运行单个场景

```powershell
Set-Location 'D:\2026WorkExperience\CUMCM2026Problems\B_solution_side_research\synthesis_iteration_20260911'
D:\st_python\python.exe solve_local.py --case 'dataset/official_composition_H1_H2_360_20260911/cases/round-calibrated-p3-0000-a21d558e975c4b8b.json' --output 'user_runs/p3_selected'
D:\st_python\python.exe solve_local.py --case 'dataset/official_composition_H1_H2_360_20260911/cases/round-calibrated-p3-0000-a21d558e975c4b8b.json' --output 'user_runs/p3_champion' --policy champion
```

将 `--case` 替换为任意合法场景 JSON，公开 problem 字段自动选择 Q3/Q4。每次指定不存在的输出目录。结果包含总时间、每源时间、全清审计、源码与场景哈希、请求和决策轨迹。运行仅使用冻结本地模拟器，不调用官方或远程平台。

输入支持用户数据集的完整格式，必须有 `problem`、10—16个 `sources` 和 `error_field`。严格使用 `error_field.seed/mode/correlation_length_m`，不以几何生成 seed 代替。场景真值仅用于评测器构造环境及事后审计，不传入策略工厂。

复制整个包到 Linux/创智后，用 Python 3.10 及以上执行同样的 `python solve_local.py ...`。求解器和实验器使用标准库；测试另需 pytest。单局入口优先使用包内数据运行时，避免依赖本机绝对目录。

## 接入其他多元评测器

```python
import json
import sys
from pathlib import Path

root = Path(r'D:\2026WorkExperience\CUMCM2026Problems\B_solution_side_research\synthesis_iteration_20260911')
sys.path.insert(0, str(root / 'vendor'))
sys.path.insert(0, str(root))
from methods.solver import make_solver

selection = json.loads((root / 'campaign/deployment_selection.json').read_text(encoding='utf-8'))
name = selection['choices'][str(problem)]
spec = selection['validation_specs'][name]
solver = make_solver(client, problem, spec, decision_log=decision_log)
result = solver.run()
```

传入你已有的合法客户端与公开题号即可；不要传 case_id、H1/H2、真实数量或坐标。该工程沿用 `bsolver` 命名空间，若评测进程已经导入其他路径的 `bsolver`，请为本候选使用独立子进程，避免混合版本。单局 CLI 已检查模块来源。

## 复现与继续迭代

```powershell
D:\st_python\python.exe -m pytest tests -q
D:\st_python\python.exe run.py stage --campaign campaign --specs campaign/validation_specs.json --output user_runs/replay360 --workers 6
```

第二条在本机登记的相同360世界上复现冻结候选与冠军，不是新的独立验证。各 stage 执行自己的源码及模拟器快照；运行期修改工作副本不会影响已启动快照。原始本轮输出拒绝覆盖。

在其他主机做批量实验、修改算法或继续选参前，创建一个新的 campaign，并为新确认集使用新 seed。示例：

```powershell
python run.py prepare-campaign --dataset dataset/official_composition_H1_H2_360_20260911 --calibration inputs/calibration_frozen.json --output campaign_next --seed 202609125301 --fresh-per-group 40 --exclude-manifest inputs/exclude_next_campaign.json
python run.py stage --campaign campaign_next --specs configs/round3.json --group-limit 24 --output user_runs/next_screen --workers 6
```

已有360世界及本轮已查看的新确认/压力世界均须视为开发证据，不能通过重命名 confirmation 重获独立性。`inputs/exclude_next_campaign.json` 包含本轮结束后需排除的已存档世界。全量开发选优后再调用 `run.py freeze`，它会冻结每题最多一个候选；确认阶段必须使用 `campaign_next/validation_specs.json`。本轮的确认结果不能用于再次改参并重新声称确认。

## 文件与结果口径

- `docs/research_plan.md`：历史结论如何转化为候选、成本推导及本轮验证约定。
- `methods/synthesis.py`：确认16源后的阶段切换、共享补测、局部费用门槛、两目标排序及数量阈值消融。
- `methods/solver.py`：新旧策略统一工厂；`methods/previous.py` 保留上一轮实现。
- `configs`：实际执行的各轮参数；`campaign`：世界登记、确认前冻结及最终采用版本。
- `runs/*/summary.json`：分题/H统计；`results.json`：全部逐场结果；`cases`：压缩的完整轨迹。
- `audit.py`：独立重算固定反馈、逐动作微秒计费、包络及真实覆盖/停止账本。
- `reports/final_metrics.json`：最终比较、置信区间、最坏退步和成本分解。

数据包中的360世界是基于官方数量/类型统计的合成筛选集，已经用于历史选优，本轮全部作为开发集。新确认集也只是同一工作机制下的新合成世界；报告中的提升不等于新增官方成绩。最终选择是本次候选与目标分布下的经验最优，不是全策略空间最优证明。
