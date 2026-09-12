# B题：参考官方数量组成的 H1 / H2 合成场景集（360局）

这是从已完成本地实验中原样提取的场景，可交给下一轮探索性方案作开发、调参和配对比较。不是官方原始案例，也不是新采集的独立测试集。

## 本次保留的范围

筛选条件：`pool == calibrated` 且 `composition_model == smoothed_joint`，仅保留如下两种误差模型。

|分组|数量组成|固定误差场|问题3|问题4|合计|
|---|---|---|---:|---:|---:|
|H1 / 假设1|参考官方数据的平滑数量／类型联合分布|correlated：平滑空间相关误差，尺度150m|90|90|180|
|H2 / 假设2|同一数量／类型组成模型|extreme：150m网格内固定为±1°的误差|90|90|180|
|合计|||180|180|360|

来源：第一阶段200局、第二阶段80局、第三阶段80局。排除较宽的组成模型、宽合法池和压力池；H2虽然使用极端误差，本来就属于所选校准子集，按用户指定保留。

## 与官方数据的关系

原始校准来源记录为160个官方案例，其中120个整局分入拟合部分。本包继承其 `smoothed_joint` 数量／类型模型。这里的360局都是随后生成的本地隐藏环境，不是那160个官方案例的真值重建。

两种误差机制均保留原标记 `mechanism_status: unresolved`。位置生成、半径生成、朝向生成和具体误差函数仍包含未确定假设；不能声称已经恢复官方生成器。两组各占一半是设计比例，不是官方误差机制的发生概率。

主调参和候选排序可使用本集，但应分别报告 H1/P3、H2/P3、H1/P4、H2/P4。混合均值只能称为本数据集的均值，不能称为官方期望成绩。最终迁移效果仍需新的官方案例验证；本导出不调用官方接口。

## 文件与读取

- `scenarios.jsonl`：360行，每行一个原始场景，保留原始 `scenario_sha256`。
- `cases/*.json`：同样360个场景，逐案例文件，便于指定复现。
- `index.json`：案例列表、问题、H1/H2分组、原阶段和原分区、来源索引。
- `groups/*.json`：H1/H2及问题3/4的现成案例ID列表。
- `manifest.json`、`provenance/`：筛选规则、计数、原清单哈希、校准来源、运行时哈希。
- `runtime/bsolver_frozen/`：原版本地模拟器及协议客户端的字节一致快照，使用独立模块名。
- `dataset.py`：加载器，构造同一隐藏场景和固定误差场。
- `example_local.py`：只测原点20频道的离线接入示例，不是完整求解算法。
- `verify_dataset.py`：哈希、360场景合法性、固定误差和本地接口接入检查。
- `verification.json`：封装前实际执行的数据/接入验证结果；`SHA256SUMS.txt`校验所有交付内容。
- `export_h1_h2.py`：提取脚本；仅在有原工程时用于重新提取。

`scenario_sha256`的算法：移除这个字段，将其余对象按键排序，以UTF-8、无多余空白、`ensure_ascii=False`序列化，再计算SHA256。整文件哈希另见 `SHA256SUMS.txt`。

Python 3.10+，仅标准库，无需GPU或安装第三方依赖。解压后在目录内运行：

```powershell
python -B verify_dataset.py
python -B example_local.py --hypothesis H1 --problem 4
python -B example_local.py --hypothesis H2 --problem 3
```

本机也可将 `python` 替换为 `& 'D:\st_python\python.exe' -X utf8`。

集成自己的方案时，由评估入口完成以下操作：

```python
from dataset import list_cases, make_local_session

row = list_cases(hypothesis="H1", problem=4)[0]
client, evaluator = make_local_session(row["case_id"])
try:
    client.enter()
    my_policy(client, row["problem"])  # 替换为真实策略，只接收公开信息
    if not client.exited:
        client.exit()
    result = evaluator.summary()     # 仅用于当局决策结束后的评分
finally:
    client.close_log()
```

策略可以调用 `client.measure(channel, (x,y))`、`client.clear(channel, (x,y))`，读取真实返回和本地公开状态。没有独立移动接口。只有 `measure_result == direction` 时读取 `svd_deg`。客户端在本例中始终连接本地模拟器，不使用用户名、密码或官方服务。

此加载器提供逻辑上的策略/环境分工，不是阻止恶意读取的安全沙箱。案例JSON内的坐标、半径、朝向、数量、误差种子只供环境与评估器读取；不能把它们、H1/H2标签或案例ID作为当局策略的隐含真值输入。

## 下一轮比较规则

1. 各策略使用同一批原始 `case_id`，通过加载器恢复完全相同的目标与固定误差场。不要重新抽噪声或修改场景来使某个策略获益。
2. 先比较完整清除率和退出正确性；在保证完成的前提下比较总虚拟耗时。失败局必须单列，不能把失败前的短耗时作为完成耗时，也不能只保留成功局而隐去失败。
3. 每个 H/P 组分别统计配对耗时差、移动距离、检测次数、换频道次数、清除尝试/失败次数、现实运行时间；记录策略版本、参数、原场景哈希与逐请求日志。
4. 总虚拟耗时核对 `L_walk/5 + N_switch + 5*N_measure + 3*N_clear_attempt + 2*N_clear_success`。本地环境逐次移动以微秒记账，累计可能存在极小舍入差。题设平均清除耗时另报，不替代整局目标。
5. 保留所有失败和退步案例。选出少量候选后，用未参与开发的新官方案例验证迁移。

原场景中的 `partition` 不变，仅为历史溯源：288个development、72个confirmation。这些案例已进入上一轮研究，整个导出集在下一轮均为开发/回归数据；旧confirmation标签不能当作新的独立验收集。`index.json`显式提供 `next_round_role: development_regression`。

新结果和日志放在独立输出目录，不覆盖本包。该包不包含旧策略的评分表、官方原始日志或官方目标真值；下一轮比较需在这些场景上实际重跑基线和新策略。

## 已执行的导出验证

原清单的嵌入哈希、360个原场景哈希、组别数量、无重复隐藏环境、目标合法性、JSONL/逐案例一致性、运行时快照一致性均由脚本核对。另对360局执行本地接口冒烟检查：固定测点复测、near、清除及频道状态。

冒烟检查由评估器使用已知真值构造探针，仅验证加载和环境接线，不是策略实验，不计为360局完整求解成功。没有新增官方请求，也没有修改原工程数据。
