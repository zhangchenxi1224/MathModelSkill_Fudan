> **当前 P3/P4 入口。** 实际选择以 `release.json` 为准：P3=`current`，P4=`ring_search`。后续已完成P3、P4各10局官方演练，20局全部全清；成绩见下方最新官方结果。
>
> [完整脚本、数据与原始日志 ZIP（约194 MB）](https://github.com/zhangchenxi1224/MathModelSkill_Fudan/releases/download/p3-p4-coverage-tail-20260912/coverage_tail_iteration_20260912_complete.zip) · [返回仓库总说明](../README.md)。网页保留源码、输入、逐局结果和报告；全部 `requests.jsonl`、`decisions.jsonl` 在完整ZIP中。

# 覆盖与收尾优化

打开 `reports/results.html` 查看最终选择、新240配对结果、各源数组及所有候选结果。`release.json` 为实际部署选择；未达到新数据确认条件的题目保留上一版基线。

本目录独立于上一版 `log_guided_iteration_20260912`。算法、全部实验日志、新数据和旧基线统计都保留在此处；上一版没有被覆盖。后续已完成P3、P4各10局官方演练，20局全部全清；成绩见下方最新官方结果。

### 最新官方演练：P3、P4各10局

**当前部署版本已完成20局官方演练，20/20全部全清**：P3=`current`，P4=`ring_search`。

|题目|全清|平均总虚拟时间|平均时间/源|10—15源组|16源组|
|---|---:|---:|---:|---:|---:|
|P3|10/10|3363.63秒|**250.87秒**|264.55秒/源（8局）|196.17秒/源（2局）|
|P4|10/10|5882.37秒|**437.68秒**|518.59秒/源（6局）|316.32秒/源（4局）|

[逐局成绩与案例编号](runs/official_coverage_tail_10_each_20260912/report/results.md) · [可视化表格、分组与耗时诊断](runs/official_coverage_tail_10_each_20260912/report/results.html) · [结构化结果](runs/official_coverage_tail_10_each_20260912/report/results.json) · [完整实验包（约3.41 MB）](https://github.com/zhangchenxi1224/MathModelSkill_Fudan/releases/tag/p3-p4-official-20-20260912)

“平均时间/源”先按每局计算T/N，再在对应组内取算术平均；不是本地确认实验中各N等权的指标。本批使用新生成的官方场景，**未与旧算法同场景配对，不能把两批均值差直接解释为算法提速幅度**；这些是官方演练成绩。

完整实验包包含脚本快照、全部请求响应、决策与几何状态，以及20份官方原始日志；本批全部289个文件也已在仓库的 `runs/official_coverage_tail_10_each_20260912` 中展开。下载后打开 `report/results.html` 查看交互表格。

## 方法

P4主要候选为中心1点、内圈8点、外圈16点的双环覆盖；固定搜索路线约17.73km。进一步比较在已确认12—15源时用数量后验选择原地搜索未知频道的机会。P3比较利用原地观测替代固定覆盖站。详细原理见 `METHOD.md`。

## 使用

运行使用 Python 3.10 或以上及 Shapely；原研发环境为 `D:\st_python\python.exe`。以下命令从仓库根目录开始，使用已具备这些依赖的 Python。

```powershell
Set-Location '05_P3_P4_coverage_tail'
# 用最终实际选择运行一个本地P4场景，输出目录请使用新名称
python -B -X utf8 solve.py --problem 4 --index 0 --output runs/my_local_p4

# 对自己的数据配对比较上一版与双环顺路搜索候选
python -B -X utf8 -u campaign.py --problem 4 --dataset data/fresh_confirmation240.json --arms current,ring_search --workers 6 --output runs/my_paired_p4
```

官方入口已经准备好，保留原研发工程路径依赖：`MAIN` 指向原 `B_solution`，`PREVIOUS` 指向 `synthesis_iteration_20260911`。原研发目录可沿用；其他解压位置需先对应这两处依赖。以下命令会实际开始P3、P4各10局演练，需要先在独立模拟器中登录；本批已按该入口完成20局，下面命令用于再次运行，请使用新的输出目录：

```powershell
python -B -X utf8 -u run_selected_official.py --out runs/my_official_10_each
python -B -X utf8 finalize_official.py --out runs/my_official_10_each
```

## 数据与日志

- `data/fresh_confirmation240.json`：本轮新生成的240条，候选冻结后才运行。
- `data/provenance.json`、`data/calibration_reference/`：生成依据与官方拟真模型选择记录。
- `inputs/new240/cases.json`：上一轮240条，本轮作为开发集；未运行旧960。
- `runs/screen_*`：各候选14场/题的分层筛选。
- `runs/full_*`：少量候选120场/题的完整开发比较。
- `runs/confirmation_p3`、`runs/confirmation_p4`：新240上的基线和冻结候选，共480次配对运行。
- 每场目录中的 `requests.jsonl`、`decisions.jsonl`、`result.json`：全部交互、几何状态、决策和结果。
- `reports/candidate_comparisons.json`、`reports/paired_cases.json`：汇总与逐场数据；HTML提供日志链接。
- `frozen_p3.json`、`frozen_p4.json`：查看新数据结果前确定的候选。
- `reports/official_diagnosis.json`：更早一批官方20局的开发诊断；本批新官方结果见 `runs/official_coverage_tail_10_each_20260912/report/`。

“新确认集”的独立性只针对本轮已经冻结的选择。后续若继续用它调参，应将它作为开发数据，不重复宣称独立验证。H1为主要拟真参考，H2为敏感性；未知的物理参数仍保留代表假设。
