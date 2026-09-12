> **当前 P3/P4 入口。** 实际选择以 `release.json` 为准：P3=`current`，P4=`ring_search`。本轮没有新增官方演练。
>
> [完整脚本、数据与原始日志 ZIP（约194 MB）](https://github.com/zhangchenxi1224/MathModelSkill_Fudan/releases/download/p3-p4-coverage-tail-20260912/coverage_tail_iteration_20260912_complete.zip) · [返回仓库总说明](../README.md)。网页保留源码、输入、逐局结果和报告；全部 `requests.jsonl`、`decisions.jsonl` 在完整ZIP中。

# 覆盖与收尾优化

打开 `reports/results.html` 查看最终选择、新240配对结果、各源数组及所有候选结果。`release.json` 为实际部署选择；未达到新数据确认条件的题目保留上一版基线。

本目录独立于上一版 `log_guided_iteration_20260912`。算法、全部实验日志、新数据和旧基线统计都保留在此处；上一版没有被覆盖。本轮没有新增官方演练。

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

官方入口已经准备好，保留原研发工程路径依赖：`MAIN` 指向原 `B_solution`，`PREVIOUS` 指向 `synthesis_iteration_20260911`。原研发目录可沿用；其他解压位置需先对应这两处依赖。以下命令会实际开始P3、P4各10局演练，需要先在独立模拟器中登录；本轮优化没有执行这些命令：

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
- `reports/official_diagnosis.json`：最近官方20局的诊断；原始官方日志仍在上一版目录。

“新确认集”的独立性只针对本轮已经冻结的选择。后续若继续用它调参，应将它作为开发数据，不重复宣称独立验证。H1为主要拟真参考，H2为敏感性；未知的物理参数仍保留代表假设。
