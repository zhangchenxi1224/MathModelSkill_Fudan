# 第一问：交会定位区域直径与覆盖圆

本目录整理截至2026-09-12本地已有的第一问解答、几何代码和示例结果。本次仅整理发布，没有重新开展算法审查或实验。

## 先阅读

- [第一问论文正文节选](docs/第一问论文正文节选.md)
- [完整几何算法与证明](docs/geometry_proofs.md)
- [已有示例数据](results/latest/q1_examples.json)
- [示例图](results/latest/q1_geometry.png)

## 模型与结论

把每个测点的示向度及±1°误差写成前向楔形半平面，求全部约束交集。算法区分空集、点、线段、有界多边形和无界区域，不用人为大边界框把无界情形变成有限结果。

若交集为有界凸多边形，直径为所有顶点对距离的最大值；程序用O(n²)枚举计算。无界区域的直径为无穷，空集应报告观测不相容。

**以区域直径为直径的圆，不一定能覆盖区域。**边长39米的等边三角形直径为39米，最小包围圆半径约22.52米，大于19.5米。区域直径小于40米也不足以推出统一20米清除保证；应检查最小包围圆或对应安全清除区域。

题面规定的是返回角误差±1°。几何操作接口中的1.0051°属于额外保守包络，不能理解为新的题面误差要求；解析第一问入口 `intersect_bearings` 默认使用1°。原证明中的舍入讨论保留作实现来源说明。

## 文件分组

|目录|内容|
|---|---|
|`src/bsolver/`|当前几何模块，以及原实验脚本导入所需的知识/选点模块|
|`scripts/research_q12.py`|原始共享实验脚本；第一问函数为 `q1_examples()`|
|`docs/`|证明和第一问论文正文节选|
|`results/latest/`|来自主工程q12_final的第一问数据及已有配图|
|`results/history/`|保留早期q12示例用于追溯|
|`tests/`|既有几何测试文件；本次整理未重跑|
|`sources/`|题面和附件2的已有文本提取|

## 使用

Python 3.10或以上。进入仓库的 `03_Q1` 目录，运行第一问示例函数：

```powershell
python -X utf8 -c "import json; from scripts.research_q12 import q1_examples; print(json.dumps(q1_examples(), ensure_ascii=False, indent=2))"
```

该命令只执行第一问。`research_q12.py` 是保留的原始共享脚本，直接运行其主入口还会运行早期第二问对照；**最新第二问请到 [04_Q2](../04_Q2/README.md)**。

如需对自己的测点编程调用，可把本目录 `src` 加入Python模块路径，使用 `intersect_bearings`、`polygon_diameter`和`minimum_enclosing_circle`。核心计算只依赖标准库。

![第一问示例](results/latest/q1_geometry.png)
