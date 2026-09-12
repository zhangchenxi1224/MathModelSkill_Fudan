# 第二问交付包

本包包含第二问的数学解答、完整证明、可运行代码、已完成结果与独立数值证书。无需原研发目录即可复现；核心计算只依赖 Python 标准库。请将“第二问_交付包”整个文件夹解压后使用。

## 阅读顺序

1. [解答与实验总览](q2_certified_frontier/README.md)：模型、实现、结果和适用范围。
2. [完整数学推导](q2_certified_frontier/docs/theory_independent.md)：首次联合可行集、最大保证接收区、四圆盘闭式、选点目标、响应上界，以及两测20米保证的不可能性证明。
3. [新旧测点独立比较证书](q2_certified_frontier/docs/certificate_comparison.md)：给出合法双源下界和完整360°响应上界。
4. [实验结果复核](q2_certified_frontier/docs/final_audit.md)与[数据来源审计](q2_certified_frontier/docs/data_support.md)。

规范首次观测 s=(0,0)、报告0°下，旧测点最坏定位半径至少78.53米，新测点所有响应的半径至多57.961749米，构成严格改进。一般连续测点空间的全局最优性尚未证明；这个标准情形的最优最坏半径目前界于25.7295668至57.961749米之间。

## 已完成结果

主结果是 `q2_certified_frontier/results/q2_precision_v2/`，包含336个合成世界、4策略、共1,344条记录，均未丢失信号且保留真值。v1及对应源码快照保留作版本追溯，引用结论请使用v2。

|策略|平均移动/米|平均定位半径/米|样本最大半径/米|
|---|---:|---:|---:|
|原fixed|806.23|32.60|78.79|
|原active|437.32|71.27|170.79|
|精度优先|1004.02|26.07|44.85|
|800米预算版|800.00|33.56|75.26|

样本最大值不等于连续最坏值；精度优先方案移动更长，也不同时提高两测进入20米范围的比例。40个官方首次观测来自20局，用于离线模型上界计算，没有新测点的官方实测反馈。

图表可直接阅读：

- [四策略对照PDF](q2_certified_frontier/results/figures/q2_comparison.pdf)
- [候选接收区与测点PDF](q2_certified_frontier/results/figures/q2_reception_lens.pdf)

原始证书位于 `q2_certified_frontier/results/certificate_audit/`，含1440个响应分箱、多边形和包围圆。题面及附件2的文本依据位于 `题目依据/`。

## 解压后运行

建议 Python 3.11 或更新版本；核心计算支持 Python 3.10，本包可选依赖的固定版本要求较新环境。本次交付验证使用 Python 3.13.5。以下命令从解压得到的“第二问_交付包”目录执行，不需要原来的D盘研发路径。

先校验全部交付文件（只需标准库）：

```powershell
python verify_delivery.py
```

再进入工程复现实验，输出目录必须是新目录：

```powershell
cd q2_certified_frontier
python study.py --output results/my_new_run --official-contexts docs/official_q2_contexts.json --angle-step 5 --workers 2
```

也可使用现成Windows启动器：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_local.ps1 -Workers 2 -AngleStep 5
```

启动器自动创建新结果目录，可加 `-Python '解释器完整路径'`。研究记录中的D盘路径用于说明原始研发位置；上述相对路径命令才是本交付包的通用入口。

复算独立证书，不需要第三方库：

```powershell
python certificate_audit.py --output results/my_certificate
```

可选测试与重画图表：

```powershell
python -m pip install -r ../requirements-optional.txt
python -m pytest -q
python plot_results.py --input results/q2_precision_v2 --output results/my_figures
```

已有合适依赖时无需安装。核心实验和证书不调用官方接口，不需要账号、密钥或原始全量日志。包含的40条公开首次上下文足以复现本包的官方首次几何回放；历史数据审计中引用的原始160局完整日志没有附带。

## 文件与版本核验

- `MANIFEST.sha256.json`记录交付文件哈希；`verify_delivery.py`检测缺失或变动。
- 研究的v1/v2各自仍保留原始运行manifest及源码快照。
- `delivery_changes.json`记录交付副本的便携性调整。核心求解代码、数学证明和实验结果保持原文件内容。
- `DELIVERY_VALIDATION.json`记录解压副本的实际验证结果。

交付副本修正了测试中指向隔壁多元探索目录的vendor路径，使其明确使用本包vendor。源码快照是对应历史运行的原始文件，不应为了修改当前实现而改写它们。
