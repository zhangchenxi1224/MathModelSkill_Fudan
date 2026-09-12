# 问题3七点覆盖布局优化（独立支线实验）

本目录不修改 `B_solution`，不调用官方HTTP接口，不消耗正式机会。覆盖证明、实际实验及不利结果均保留。

## 文件

- `PROOF.md`：连续域覆盖、可行环半径、布局族内最短路线与测点数量下界。
- `RESULTS.md`：完整实际结果、开发/测试划分、尾部退步和成本审计。
- `coverage_opt.py`：对原求解器的最小独立扩展，参数 `ring=1300.0` 为本次开发集选中值。
- `test_coverage_opt.py`：8项构造性测试。
- `experiment.py`：本地完整任务对照实验和制图，所有动作通过本地模拟器公开语义执行。
- `results/`：112次运行的场景、请求响应、决策、结果，冻结选择、汇总、3张图和父工程代码快照。

## 环境与目录

本实验需要已经交付的主工程，目录关系为：

```text
CUMCM2026Problems/
  B_solution/src/bsolver/...
  B_solution/results/{tune,test,stress}/baseline/.../case.json
  B_solution_side_research/q3_cover_optimization/  ← 本目录
```

程序从上述相对位置读取父工程。`results/parent_source_snapshot.zip` 保存本次使用的父工程全部Python源文件，哈希在冻结选择文件中逐一记录；重新运行前应核对版本，不要把不同版本的数据混入本次结果。单独复制此支线压缩包不能替代主工程。

实际环境：Python3.13.5；核心与测试只需标准库。制图需Matplotlib和NumPy（本机已安装）；不需要GPU。建议使用 `-B`，避免向主工程写入Python缓存。

## 复现命令

在本目录执行（本机解释器为 `D:\st_python\python.exe`）：

```powershell
python -B -m unittest -v test_coverage_opt
python -B experiment.py development
python -B experiment.py test
python -B experiment.py report
```

前两批实验会重新生成**本支线**同名结果文件；要保留本次原始日志，先复制本目录或使用已交付的结果压缩包。开发运行预先声明四个候选，完成后冻结最低开发均值的合格候选；测试运行检查父工程哈希未改变。没有用测试集反复挑选参数。

运行实际策略的局部接入形式：

```python
from coverage_opt import ContractedCoverageSolver
from bsolver.strategy import SolverConfig

# client由既有协议层创建；本研究实际运行仅使用本地transport。
solver = ContractedCoverageSolver(
    client, SolverConfig(problem=3),
    decision_log="decisions.jsonl", ring=1300.0,
)
result = solver.run()
```

注意：类默认值1125 m表示解析候选；使用开发选中的候选时应显式传1300 m。任何官方正式测试仍需另行授权，本文件中的示例不是正式测试授权。

## 结果速览

12个开发案例×4候选=48次；32个测试案例×2方案=64次。共112次全清、0外包违反。32个测试案例包含12个历史保留案例与20个全新案例。

- 全测试平均总虚拟时间4818.427→4673.022 s，下降3.018%。
- 全新20例下降1.665%，并有尾部退步。
- 全测试23例改善、9例退步，95分位上升2.647%。
- 原布局与1300 m布局均有连续域覆盖证明；本次程序是平均耗时候选优化，没有声称逐局占优。

完整表和所有不利结果见 `RESULTS.md`。
