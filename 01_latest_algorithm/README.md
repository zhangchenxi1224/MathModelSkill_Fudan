# P3 / P4 全反馈实验

本目录独立保存策略、数据快照和实验结果。推荐的实验入口是 `evaluate_selected.py`：它按公开题号选择 P3/P4 策略，默认在完全相同的场景和固定误差场上同时运行冻结基线。最终选择及计算参数见 `selection.json`。

本轮冻结基线是此前侧线部署的 P3 `p3_r1150_w1`、P4 `p4_L2_d1200_w1`。新数据来源目录发布的 `compact1150`、`merged25_l1` 另有同场景比较，不能混用两种对照的提速百分比。

## 直接使用

在 PowerShell 中运行：

```powershell
Set-Location -LiteralPath 'D:\2026WorkExperience\CUMCM2026Problems\B_solution_side_research\all_feedback_experiments_20260912'

D:\st_python\python.exe -X utf8 -u evaluate_selected.py --dataset inputs/new240/cases.json --output runs/my_new240 --workers 6
```

换成自己的 JSON、包含场景 JSON 的 ZIP，或场景目录即可。每次使用新的输出目录。只运行候选、不重新跑基线时，加 `--no-baseline`。

输出中，`RESULTS.md` 按 H1/H2 分别汇总；`results.json` 是逐场景结果；`cases/` 保存原始反馈、决策和单局指标。直接接入其他评测框架时：

```python
from selected_policy import make_solver

solver = make_solver(client, problem=3)  # client 使用现有 RobotClient 接口
result = solver.run()
```

策略根据反馈在线改变行动，不读取场景真值、案例编号或 H 标签。

## 当前两个方案

- P3：`dp_geometric`。保留全部反馈的几何约束，进行目标内部最多两步前瞻，每次只执行第一步并重新规划。
- P4：`three_feedback_fast`，位置细分深度 3。联合考虑有方向、near、无信号三种反馈的后续成本，采用局部一步决策。

这两者沿用已有的全局搜索站点与动态服务顺序。它们是有限候选、有限视界的近似策略，不是已经求解的全局最优多频道规划。概率贪心排序及低概率截断的实验代码和原始结果保留，但不属于推荐组合。

计算预算由候选数、粒子数、节点数等确定；当前 60 秒仅为单次规划的紧急保护上限。旧实验中触及 5 秒保护的场景另行补跑，最终汇总记录结果来源。未触及旧保护的完整搜索可复用。物理移动、检测和清除成本没有改变。

## 为什么这样做

同一场景完整清除后的时间为：

\[
T = D/5 + 5M + S + 3A + 2C
\]

其中 D 是移动米数，M 是检测次数，S 是换频次数，A 是清除尝试次数，C 是成功清除次数。信息是否有价值，取决于它能否减少后续行动成本，而不是仅看可行区域缩小多少。

P3 的无信号结合此前正反馈，约束同一个接收半径，并排除不可能位置。P4 保留全向与定向解释，同一假设内的半径和朝向必须同时满足全部历史反馈。清除失败同样作为约束保存。位置单元是可行区域的保守近似；工作概率只用于决策估计，不把零采样概率当作不存在的证明。

## 数据和结果位置

- `inputs/new240/cases.json`：用户新补发的 240 场景快照，P3/P4 各 120；每题 H1/H2 各 60。
- `inputs/core960/p3.json`、`p4.json`：用户旧 960 场景，分别 380、580。
- `inputs/*/receipt.json`：原始文件路径、散列和数据用途说明。
- `reports/final_new240/`：新 240 最终完整比较。
- `reports/final_vs_mainline_new240/`：与数据来源目录最新发布版本的同场景对照。
- `reports/FINAL_REPORT.md`：最终结论、完整结果表入口及补跑说明。
- `runs/`：全部运行与原始日志；补跑不会覆盖之前的结果。

新 240 与旧 960 没有重叠。新 240 用于本轮迭代，H1 为主要参考、H2 为敏感性对照；旧 960 用于扩大回归检查范围。数量组成参考官方数据，但位置、半径、朝向和误差机制仍含仿真假设。本轮结果均为本地仿真，不是新一轮官方评测。

## 继续研究

`run.py` 保留全部实验臂；`calibrate.py` 可从明确指定的训练世界拟合简单先验。其他实验臂应显式选择，不会自动替换当前组合。例如：

```powershell
D:\st_python\python.exe -X utf8 -u run.py --dataset inputs/new240/cases.json --arms geometry,three_feedback --output runs/ablation_next --workers 6
```

`run_fast.py` 跳过重复路径计算，使用已产生的有限浮点坐标直接计距。它不改变覆盖点集合和路径成本公式；等价比较见 `runs/fast_cover_equivalence.json`。
