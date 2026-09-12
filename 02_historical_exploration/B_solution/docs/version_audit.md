# 版本审计与最终源回归

原192条自建实验使用 `b3cdd5b3089c9ea55f14a791b74702bfbc5777b8a6e8e8c8d19d3caf3f12b4bd`；保留原始结果及散列，不改写为新版本。
最终源使用 `1b673484453104a5d87d4605d30b20826a3b7ec64769fd6ce8d3c8f7a1ad24f6`。两个版本均存于 `results/code_snapshots/<hash>.zip`。

最终修补仅涉及 protocol.py 增加请求前 state_before 日志，以及 sensing.py 拒绝非正/非有限的分箱宽度、负/非有限的角容差，或扩张角达到90°的参数。默认 bin_width=4°、epsilon=1.0051°不触及新增拒绝条件；具体逐行差异见 `results/final_regression/source_changes.diff`。

抽取既有 test 的首个P3、P4案例，各使用原六种变体重跑，共12次。未用结果调整参数；这12次是版本回归，不并入原192条的模型效果样本。

核验采用解析JSON后的精确相等：计数、总虚拟时间、独立核算时间、路程、清除比例、停止证据、外部环境统计，以及完整动作顺序/坐标/响应/动作虚拟时间。排除请求ID、真实时间戳、本地runtime和新增日志元数据。

|题号|变体|请求数|全部结果字段相同|动作序列相同|总虚拟时间 s|
|---|---|---:|---|---|---:|
|P3|baseline|125|True|True|5690.780398|
|P3|active|126|True|True|4930.861015|
|P3|nearest_safe|126|True|True|4919.332041|
|P3|immediate|144|True|True|5785.001045|
|P3|joint_square|141|True|True|4075.547070|
|P3|joint_triangular|141|True|True|4075.547070|
|P4|baseline|613|True|True|13185.597786|
|P4|active|661|True|True|12731.650608|
|P4|nearest_safe|661|True|True|12726.060204|
|P4|immediate|642|True|True|13694.838015|
|P4|joint_square|634|True|True|11984.954925|
|P4|joint_triangular|520|True|True|11377.490873|

结果：12次结果字段全部相同=True；12条完整动作序列全部相同=True。全部新请求日志含state_before。
该回归支持这12个默认合法场景的行为未改变，不宣称穷尽全部场景，也不把旧官方练习记录改标成最终源结果。原官方练习应按其自己的源散列和日志解释。

机器可读证据：`results/final_regression/version_audit.json`；原始12次记录：该目录下各变体/案例；重现脚本：`results/final_regression/audit.py`。
