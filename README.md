# 数学建模 B 题：第一至第四问解答、算法与实验归档

本仓库整理截至 **2026 年 9 月 12 日** 的 B 题第一至第四问已有解答、代码、实验结果与历史探索。**第一问为测向交会几何；第二问最新为允许无信号的三反馈选点；第三问保留上一轮部署算法，第四问最新采用“双环覆盖＋顺路搜索”。**各题最新入口如下，第二问旧保证接收方案另列历史。


## 按题目阅读

|题目|当前内容|入口|
|---|---|---|
|第一问|交会区域分类、直径计算、覆盖圆反例和几何代码|[03_Q1/README.md](03_Q1/README.md)|
|第二问|最新三反馈选点、8个首次上下文对照、1920条合成配对记录及历史分组|[04_Q2/README.md](04_Q2/README.md)|
|第三、四问|P4双环覆盖＋顺路搜索，P3保留原版；新240条确认与官方20局全清|[当前算法与使用](05_P3_P4_coverage_tail/README.md) · [本轮报告](05_P3_P4_coverage_tail/reports/FINAL_REPORT.md)|

**下载第一、二问全部整理材料：** [Q1/Q2 Release](https://github.com/zhangchenxi1224/MathModelSkill_Fudan/releases/tag/q1-q2-delivery-20260912)，内含第一、二问合并ZIP，以及用户提供的第二问“最新与历史分组”原始ZIP。第一、二问的整理目录也完整保存在网页仓库中，可直接浏览或使用 Code → Download ZIP。

第一问先读[论文正文节选](03_Q1/docs/第一问论文正文节选.md)和[几何证明](03_Q1/docs/geometry_proofs.md)。第二问先读[最终报告PDF](04_Q2/01_最新迭代实验/reports/q2_experiment_report_20260911/第二问实验报告_最终稿.pdf)和[最新结果汇总](04_Q2/01_最新迭代实验/results/final_comparison/RESULTS.md)。第二问最新方案允许并利用无信号反馈；历史保证接收方案的25.73米下界不能直接套用，当前也未宣称连续全局最优。

第一、二问本次更新仅整理已有解答、脚本、输入、结果和报告，未重跑实验或展开工程审查。历史批次保留源码快照，原始研发路径仅用于来源追溯；运行优先使用各题README中的仓库相对路径。

## 第三、四问当前方案：覆盖与收尾优化

**当前入口：[05_P3_P4_coverage_tail](05_P3_P4_coverage_tail/README.md)**。P4 采用 **双环覆盖＋顺路搜索**（`ring_search`）；P3 保留上一轮部署算法（`current`，对应 `opportunity`），未采用本轮测试的站点替代候选。

**结果与原理：** [本轮实验报告](05_P3_P4_coverage_tail/reports/FINAL_REPORT.md) · [第一性原理与方法](05_P3_P4_coverage_tail/METHOD.md) · [可视化表格 HTML](05_P3_P4_coverage_tail/reports/results.html) · [新240条数据](05_P3_P4_coverage_tail/data/fresh_confirmation240.json)

**完整脚本、数据和日志：** [直接下载本轮完整 ZIP（约194 MB）](https://github.com/zhangchenxi1224/MathModelSkill_Fudan/releases/download/p3-p4-coverage-tail-20260912/coverage_tail_iteration_20260912_complete.zip) · [Release 页面](https://github.com/zhangchenxi1224/MathModelSkill_Fudan/releases/tag/p3-p4-coverage-tail-20260912)。这是本地迭代完整包，只有一个ZIP，无需分卷合并，解压后约2.75 GB、3245个文件；后续官方20局另有下方约3.41 MB实验包。

### 本轮实现与结果

总虚拟时间仍按 $T=D/5+5M+S+3A+2C$ 计费。全清时成功清除数固定，因此本轮主要减少搜索移动，并利用已经到达的位置检测剩余未知频道。

|题目|本轮实际采用|含义|
|---|---|---|
|P3|`current`，继承 `opportunity`|新候选未达到预先约定的确认标准，继续使用上一轮部署算法|
|P4|`ring_search`|中心1点、内圈8点、外圈16点的双环覆盖，加上有预算限制的原地顺路搜索|

P4仍为25个搜索站，固定路线从 **24.10 km降至17.73 km，缩短约26.40%**。这是固定搜索站路线；整局还包括定位清除绕行，实际收益见配对实验。

新240条上的最终组合 **240/240全清**。下表为P4相对上一轮部署版本 `count_transit` 的配对提速；主指标是各源数分组等权的平均 $T/N$，与早期1200场景报告的基线及统计口径不同。

|数据组|N10—16等权提速|N10—15|N16|变慢场景|
|---|---:|---:|---:|---:|
|H1：主要参考|**17.33%**|17.56%|15.15%|2|
|H2：敏感性对照|**15.17%**|16.26%|3.50%|4|

P4共6场变慢，最大约1493秒；[逐局配对数据](05_P3_P4_coverage_tail/reports/paired_cases.json)和[退步诊断](05_P3_P4_coverage_tail/reports/worst_regressions.json)均已保留。P3测试候选的收益未达到确认标准，表内P3候选成绩不能当作已经部署的改动。

旧240用于开发；**新240在候选冻结后才运行**，形成480次基线/候选配对运行。其独立性针对本轮冻结候选；若继续据此调参，应转为开发数据。沿用官方记录支持的生成模型，未识别的源位置、接收半径、朝向和误差场仍含仿真假设。

### 最新官方演练：P3、P4各10局

**当前部署版本已完成20局官方演练，20/20全部全清**：P3=`current`，P4=`ring_search`。

|题目|全清|平均总虚拟时间|平均时间/源|10—15源组|16源组|
|---|---:|---:|---:|---:|---:|
|P3|10/10|3363.63秒|**250.87秒**|264.55秒/源（8局）|196.17秒/源（2局）|
|P4|10/10|5882.37秒|**437.68秒**|518.59秒/源（6局）|316.32秒/源（4局）|

[逐局成绩与案例编号](05_P3_P4_coverage_tail/runs/official_coverage_tail_10_each_20260912/report/results.md) · [可视化表格、分组与耗时诊断](05_P3_P4_coverage_tail/runs/official_coverage_tail_10_each_20260912/report/results.html) · [结构化结果](05_P3_P4_coverage_tail/runs/official_coverage_tail_10_each_20260912/report/results.json) · [完整实验包（约3.41 MB）](https://github.com/zhangchenxi1224/MathModelSkill_Fudan/releases/tag/p3-p4-official-20-20260912)

“平均时间/源”先按每局计算T/N，再在对应组内取算术平均；不是本地确认实验中各N等权的指标。本批使用新生成的官方场景，**未与旧算法同场景配对，不能把两批均值差直接解释为算法提速幅度**；这些是官方演练成绩。

完整实验包包含脚本快照、全部请求响应、决策与几何状态，以及20份官方原始日志；本批全部289个文件也已在仓库的 `05_P3_P4_coverage_tail/runs/official_coverage_tail_10_each_20260912` 中展开。下载后打开 `report/results.html` 查看交互表格。

`reports/official_diagnosis.json` 仍是更早一批20局的开发诊断；本次官方结果以以上新目录为准。旧版5局/题演练及1200场景结果保留在历史资料中。

### 运行与文件入口

使用Python 3.10或以上及Shapely；原研发环境为 `D:\st_python\python.exe`。在仓库根目录执行：

```powershell
cd 05_P3_P4_coverage_tail
# 运行实际采用的P4方案；每轮使用新的输出目录
python -B -X utf8 solve.py --problem 4 --index 0 --output runs/my_local_p4

# 同场景比较上一轮部署版本与双环顺路搜索
python -B -X utf8 -u campaign.py --problem 4 --dataset data/fresh_confirmation240.json --arms current,ring_search --workers 6 --output runs/my_paired_p4
```

|文件或目录|作用|
|---|---|
|[`release.json`](05_P3_P4_coverage_tail/release.json)|**当前实际部署选择**；`selection.json`是继承的早期配置，不是本轮发布选择|
|[`selected_policy.py`](05_P3_P4_coverage_tail/selected_policy.py)|按公开题号加载实际选择，接口为 `make_solver(client, problem)`|
|[`tail_policy.py`](05_P3_P4_coverage_tail/tail_policy.py)|本轮覆盖、顺路搜索与候选策略|
|[`METHOD.md`](05_P3_P4_coverage_tail/METHOD.md)|连续覆盖判据、双环几何、顺路搜索与评价方法|
|[`frozen_p3.json`](05_P3_P4_coverage_tail/frozen_p3.json)、[`frozen_p4.json`](05_P3_P4_coverage_tail/frozen_p4.json)|查看新确认集结果之前冻结的候选|
|[`data/`](05_P3_P4_coverage_tail/data/)|新240条、生成依据与校准参考|
|[`reports/`](05_P3_P4_coverage_tail/reports/)|全部候选比较、确认结果、逐局表和退步分析|
|[`runs/`](05_P3_P4_coverage_tail/runs/)|网页保留逐局结果；完整请求和决策日志在本轮ZIP中|
|[`run_selected_official.py`](05_P3_P4_coverage_tail/run_selected_official.py)|本批官方20局使用的入口，保留原研发工程的采集器和制表依赖|

下载完整ZIP并解压后，双击 `reports/results.html` 查看交互表格；GitHub网页只显示HTML源码。历史日志中的原始绝对路径用于追溯。官方运行需要独立模拟器已登录，并满足脚本的原工程路径依赖；查看或下载资料不会自动开始演练。

## 第三、四问早期资料与完整历史

以下为**早期全反馈算法阶段**的资料：P3局部两步前瞻、P4三反馈成本决策，1200个本地场景与P3/P4各5局官方演练。这里的PDF/Word没有改写为本轮“双环覆盖＋顺路搜索”报告；本轮结果请使用上方当前入口。

|内容|入口|
|---|---|
|早期算法、输入与结果|[`01_latest_algorithm/`](01_latest_algorithm/START_HERE.md)（目录名保留，当前已属于历史版本）|
|主线及各侧线历史探索|[`02_historical_exploration/`](02_historical_exploration/HISTORY_INDEX.md)|
|早期模型报告|[PDF](P3_P4_模型与算法实验报告.pdf) · [Word](P3_P4_模型与算法实验报告.docx) · [Markdown](P3_P4_模型与算法实验报告.md)|
|早期图表与生成脚本|[`report_assets/`](report_assets/)|
|早期全部脚本、数据和日志（约4.47 GB，5卷）|[P3/P4 完整历史 Release](https://github.com/zhangchenxi1224/MathModelSkill_Fudan/releases/tag/p3-p4-delivery-20260912)|

旧版完整包仍可在仓库根目录执行 `python restore_delivery.py` 下载合并。**该命令下载的是早期4.47 GB总归档**；本轮约194 MB的新包请使用上方直接下载链接。各Release和原始记录均保留，Code → Download ZIP只包含网页仓库中展开的文件。
