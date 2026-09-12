# 附件与主线：同场景实验

本目录独立保存本次比较，未修改主工程或原始压缩包，未调用官方接口。

- `report/comparison.md`：完整比较、单项增量、退步案例与复现说明。
- `report/comparison.html`：简洁表格视图。
- `report/comparison.json`：全部汇总数字与分层配对结果。
- `runs/full360/results.json`：全量逐局结果；同目录 `cases` 保存原请求和决策压缩日志。
- `policies.py`：附件规则接入、本轮迭代及部署版工厂；`vendor` 为本次使用的代码副本。
- `attachment/solution`：附件原文及脚本。附件没有原始整局控制器，接入版中的明确实现选择见报告。
- `dataset_input`：用户360场景及对应固定模拟器。H1/H2各180，P3/P4各180；都是开发场景。

运行环境：Python 3.10+，主求解流程仅标准库；本机使用 `D:/st_python/python.exe`。在此目录运行：

```powershell
& 'D:/st_python/python.exe' -B -X utf8 solve_case.py --case-id round-calibrated-p3-0000-a21d558e975c4b8b --policy selected --output runs/my_case_new
```

`selected`读取本轮开发集选出的方案，明确不代表全局最优。全部比较命令见报告；新运行必须使用新输出目录。

原始输入SHA保存在 `input_manifest.json`；每个实验阶段保留参数和策略源码快照。筛选阶段与全量阶段共享场景，不能把阶段结果当作独立确认或官方成绩。
