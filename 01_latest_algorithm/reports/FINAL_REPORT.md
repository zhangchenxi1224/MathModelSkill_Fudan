# 全反馈实验：新240迭代与旧960回归

本轮推荐 P3 使用局部两步几何前瞻 dp_geometric，P4 使用局部一步三反馈成本评估 three_feedback_fast（位置细分深度3）。新旧数据无重叠，保留候选在全部 **1200/1200 场景完整清除**，最终记录没有几何区域误排。

按用户指定用途，新240用于迭代，H1为主要参考、H2为敏感性对照；旧960扩大回归范围。数量组成参考官方记录，位置、半径、朝向与误差机制仍保留仿真假设。本报告是本地仿真结果，不作为本轮反复选参后的独立确认，也不宣称全局最优。

## 与本轮冻结侧线基线比较

冻结基线为 p3_r1150_w1 / p4_L2_d1200_w1。提速按同场景平均虚拟任务时间计算；每源时间和完整逐局指标见明细。

|数据|题目|完整清除|H1提速|H2提速|全组平均任务秒：基线→候选|
|---|---|---:|---:|---:|---:|
|新240|P3|120/120|5.38%|5.61%|3720.46 → 3515.94|
|新240|P4|120/120|3.51%|1.56%|8513.88 → 8298.37|
|旧960|P3|380/380|5.81%|5.29%|3732.81 → 3525.67|
|旧960|P4|580/580|2.80%|1.97%|8476.30 → 8274.04|

## 与数据来源目录最新主线发布版本比较

比较对象为 P3 compact1150、P4 merged25_l1。复用了来源目录已完成的同场景结果，逐世界散列已对应，仿真器与协议文件一致；没有重复运行或改写来源项目。此表与上一表的基线不同。

|数据|题目|H1提速|H2提速|
|---|---|---:|---:|
|新240|P3|7.08%|7.52%|
|新240|P4|4.84%|2.63%|
|旧960|P3|7.61%|7.49%|
|旧960|P4|3.99%|3.11%|

## 收益与变慢的来源

P3 在新240中有117/120场更快。平均增加约19秒检测成本，减少约216秒移动成本，得到约205秒净收益。P4 在新240中有88/120场更快；H1平均少花约271秒移动、39秒检测，多花约14秒清除尝试。两题都通过调整后续行动兑现反馈价值。

几何信息单独增加时，新240平均收益仅P3约0.07%、P4约0.09%。先期筛选中的概率贪心排序出现明显退步，低概率截断出现漏清，因此没有加入推荐组合。

|数据|题目|变慢场景数|最大单局变慢/s|
|---|---|---:|---:|
|新240|P3|3/120|634.69|
|新240|P4|32/120|1042.22|
|旧960|P3|15/380|606.81|
|旧960|P4|143/580|1570.36|

以下为每组最明显的退步案例。时间差分解为移动、检测、换频和清除尝试，均为候选减基线，负值表示省时。

|数据/题目|案例|总差/s|移动差/s|检测差/s|换频差/s|清除差/s|
|---|---|---:|---:|---:|---:|---:|
|新240/P3|round-official369_confirmation-p3-0026-3145003ffd869a80|+634.69|+581.69|+40.00|+4.00|+9.00|
|新240/P4|round-official369_confirmation-p4-0040-3edd2bf7767a16f2|+1042.22|+611.22|-25.00|+0.00|+456.00|
|旧960/P3|round-calibrated-p3-0066-df796298f6747443|+606.81|+581.81|+25.00|+0.00|+0.00|
|旧960/P4|round-calibrated-p4-0008-62f4fa3f0d4c5735|+1570.36|+1087.36|+30.00|+12.00|+441.00|

P4仍存在个别较大的退步，主要落在额外移动和清除尝试上。工作概率分布与有限视界是近似决策工具，完整清除由保留的几何覆盖和实际清除反馈保障；平均改善并不表示每局都更快。

## 计算与复现

推荐组合沿用既有全局搜索站点与动态目标服务顺序。P3前瞻发生在当前目标的局部处理内，P4按三种可能反馈逐步重算成本；没有宣称实现无限制的跨频道全局反馈树。

单次规划保留有限候选/节点数，紧急计算上限统一放宽到60秒。共34场触及原5秒保护，均按日志标记补跑；未触及原保护的完整搜索直接复用。最终记录注明原始结果、实际采用结果和日志地址。

本机本轮并行运行中，候选程序平均每局耗时约P3 23秒、P4 43—45秒，高于冻结基线约1—3秒。这里的程序时间是本机观测值；任务提速指虚拟移动/检测/清除时间。

## 运行与文件

统一入口自动按题号选择策略，默认同时运行冻结基线。使用新的输出目录：

```powershell
Set-Location -LiteralPath 'D:\2026WorkExperience\CUMCM2026Problems\B_solution_side_research\all_feedback_experiments_20260912'
D:\st_python\python.exe -X utf8 -u evaluate_selected.py --dataset inputs/new240/cases.json --output runs/retest_new240 --workers 6
```

数据可换成其他JSON、包含场景JSON的ZIP或目录。只跑候选可加 --no-baseline。也可从 selected_policy 导入 make_solver(client, problem) 接入其他评测框架。

- [运行说明与组件解释](D:/2026WorkExperience/CUMCM2026Problems/B_solution_side_research/all_feedback_experiments_20260912/README.md)
- [冻结方案参数](D:/2026WorkExperience/CUMCM2026Problems/B_solution_side_research/all_feedback_experiments_20260912/selection.json)
- [新240逐局表](D:/2026WorkExperience/CUMCM2026Problems/B_solution_side_research/all_feedback_experiments_20260912/reports/final_new240/results.html) / [新240配对差异](D:/2026WorkExperience/CUMCM2026Problems/B_solution_side_research/all_feedback_experiments_20260912/reports/final_new240/DIFFERENCES.md)
- [旧960逐局表](D:/2026WorkExperience/CUMCM2026Problems/B_solution_side_research/all_feedback_experiments_20260912/reports/final_core960/results.html) / [旧960配对差异](D:/2026WorkExperience/CUMCM2026Problems/B_solution_side_research/all_feedback_experiments_20260912/reports/final_core960/DIFFERENCES.md)
- [新240相对最新主线发布版](D:/2026WorkExperience/CUMCM2026Problems/B_solution_side_research/all_feedback_experiments_20260912/reports/final_vs_mainline_new240/DIFFERENCES.md) / [旧960相对最新主线发布版](D:/2026WorkExperience/CUMCM2026Problems/B_solution_side_research/all_feedback_experiments_20260912/reports/final_vs_mainline_core960/DIFFERENCES.md)
- [新240采用记录](D:/2026WorkExperience/CUMCM2026Problems/B_solution_side_research/all_feedback_experiments_20260912/runs/final_new240/provenance.json) / [旧960采用记录](D:/2026WorkExperience/CUMCM2026Problems/B_solution_side_research/all_feedback_experiments_20260912/runs/final_core960/provenance.json)
- [脚本、输入数据与结果表轻量包](D:/2026WorkExperience/CUMCM2026Problems/B_solution_side_research/all_feedback_experiments_20260912/delivery/all_feedback_ready_20260912_141827.zip)

原始请求/决策日志保存在本项目 runs/ 各实验子目录。轻量包提供完整脚本、输入快照、结果表与历史实验摘要。
