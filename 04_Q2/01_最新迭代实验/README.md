# 第二问三反馈实验主线

本目录在同一评价器下比较原保证接收方案、允许无信号的固定候选，以及允许无信号后的搜索方案。主要指标是两测后最坏合法反馈的最小包围圆半径；不把移动距离或20米清除比例放进选点目标。

## 已实现

- 原始圆、圆外排除区域、方向半平面的交，使用解析圆弧和线段求边界及最远点，不先把圆变成多边形。
- direction、near、no_signal三个反馈分支；两次共享同一个未知接收半径。
- 返回角物理误差为±1°，只对0.01°格点上的合法方向报告构造下界见证。自适应角区间完整覆盖36,000个报告。
- 对每个候选点输出最坏半径的上下界及差距；精度不足时保留差距。
- DIRECT、Nelder–Mead、DE搜索和多候选精细复核。原方案的测点来自已冻结记录，只使用对应首次公开观测。
- 对最终候选可用独立矩形区间覆盖检查复核圆弧计算的上界。

三反馈位置约束为：

```
direction: P1 ∩ 第二角域 ∩ {5 < d2 ≤ 1500}
near:      P1 ∩ {d2 ≤ 5}
no_signal: P1 ∩ {d2 > max(1000,d1)}
```

无信号不是失败或任意罚分；它也缩小相容源位置。原地重复检测只保留首次报告，不能制造第二份独立信息。

## 实验设计

四个对照为 `old_fixed`、`old_precision`、`open_seed=(850,600)`及 `open_search`；坐标均先转到“首次位置为原点、首次示向度为x轴”的局部系，目标圆也随之变换。

主线场景为原点、目标圆边缘附近、目标圆外首次测点，并从已归档官方P3记录中按原顺序每局取一个首次上下文。真实上下文只比较模型内最坏界，不生成虚构的官方第二反馈。

合成反馈在选点冻结后产生：覆盖多个距离、首次角域两侧和中间、两个相容接收半径及五个第二误差。每个物理世界对四种策略配对，记录实际反馈、估计坐标、包围半径及真值覆盖。它们是敏感性设计，不是对官方源分布的估计。

## 本地运行

使用Python 3.11及以上；当前环境为Python 3.13.5。已有 `D:\st_python\python.exe` 可直接运行。

```powershell
Set-Location '04_Q2/01_最新迭代实验'
powershell -ExecutionPolicy Bypass -File .\run_local.ps1 -Budget 80 -OfficialCases 2 -Workers 2
```

增加搜索预算或首次上下文：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_local.ps1 -Budget 200 -OfficialCases 8 -Workers 2
```

跨平台直接运行：

```text
python -m pip install -r requirements.txt
python experiment.py --output results/new_run --budget 80 --official-cases 2 --workers 2
python -m pytest -q
```

输出目录必须为新目录。`summary.json`汇总主要比较，`cases/`保存每个场景的搜索轨迹、最坏报告及上下界，`feedback_rows.json`保存全部配对反馈。`manifest.json`记录参与计算的代码和输入哈希，`completion.json`表示本轮完成。

## 结果应如何解释

当前默认搜索是有限预算实验，不是全局最优证明。DIRECT或DE停止不代表求得全局最优；全域下界目前仅记录平凡的0，未进行空间分支定界认证。旧保证接收模型的25.73米下界不能直接移植到本模型。

解析几何使用带外扩余量的双精度运算。严格开边界只为上界放宽；下界必须来自满足原条件的合法点。最终的矩形覆盖复核与圆弧实现独立，但同样不是形式化证明助手验证。报告会区分常规解析数值上界与经过独立覆盖复核的上界。

汇总优先阅读 [实验结果](results/final_comparison/RESULTS.md)与[对照图](results/final_comparison/comparison.png)。主实验见 `results/mainline_v1/`，新增目标圆裁剪压力场景见 `results/clipped_geometry_v1/`。最终候选的独立覆盖复核见 `results/final_comparison/verified_comparison.json`。各次运行保留匹配其manifest的源码快照。

指定已归档的首次上下文继续实验：

```text
python experiment.py --output results/clipped_new --budget 160 --workers 2 --context-ids r1-p3-survey-007-ch03 r1-p3-survey-013-ch01 r1-p3-survey-018-ch01
```

上述三个压力场景按公开首次位置和方向选择，用于覆盖明显的目标圆裁剪形态，不是独立随机确认集。`finish_results.py`可重新生成这两批结果的汇总与图，需要另装matplotlib；它不参与选点。
