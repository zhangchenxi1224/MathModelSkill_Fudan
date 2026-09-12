# 观测约束归档：可识别到区域，未识别部分原样保留

`src/bsolver/posterior_archive.py` 是离线归档器。这里的 posterior archive 是“后验可行集合”的文件名约定，**没有拟合概率后验，也没有输出精确位置、接收半径或发射朝向估计值**。模块只接收公共请求日志以及可选的事后公开总数 N/Ndir，不接收模拟器真值，不访问 HTTP 或 GUI。

```python
from bsolver.posterior_archive import archive_requests, build_archive

archive = archive_requests(
    "case/requests.jsonl", "archive/case/posterior_archive.json",
    problem=4, case_id="r1-p4-survey-001",
    post_exit_counts={"N": 13, "Ndir": 4},  # 仅当已从事后公开界面获得
)
# 内存接口：build_archive(request_rows, problem=4, case_id=..., post_exit_counts=...)
```

批处理入口自动从 `assignment.json`、`result.json` 或 `post_exit_audit.json` 读取问题编号；计数只读后者的 N/Ndir，兼容官方采集器的 `source_total_post_exit` / `directional_total_post_exit` 字段。未知/null 保持未知，不默认问题 4 的定向数为 0：

```powershell
& 'D:\st_python\python.exe' scripts/archive_posteriors.py --input results/round1/cases --output results/round1/posterior_archives
```

每局输出 `<output>/<case_id>/posterior_archive.json`，另输出索引。没有公开问题编号时可传 `--problem 3` 或 `--problem 4`。脚本不会读取 `case.json`，不会修改原请求日志；解析失败的案例会进入索引错误项，不能静默删去。

## 1. 证据边界

复用 `calibration.unique_accepted_actions`，只将接受响应按 request_id 去重一次；拒绝、超时不构成新观测。重复 ID 的冲突、缺少 ID、未被客户端确认的响应等均进入审计 issues。多个不同的接受 `/enter` 不能合并成一局。每频道保留全部去重动作、原始响应、顺序、请求 ID 和源在动作前是否已经清除。

正测量或成功清除才能确认该频道原先存在目标。只有无信号的频道仍记 `not_confirmed`；归档器不自行从动作缺失推断 absent。未包含确认 `/exit` 的日志可保存为部分证据，但 `exit_confirmed=false`，不能据此宣称整局完成。

## 2. 保守位置外包

初始区域是 1800 m 圆的外接 128 边形。每条有效 direction 与保守角楔和测点 1500 m 圆外包相交；每条 near 与测点 5 m 圆外包相交。成功 clear 与清除点 20 m 圆外包相交。

清除成功说明目标在这个 20 m 圆内，**不说明目标恰在清除点**。无信号及失败清除产生的孔洞/析取保留在完整符号账本中，不把它们误作半平面从凸包删除。外包为空能暴露矛盾；外包非空仅是必要条件，`joint_feasibility=not_certified`，不等于存在一组共享 g、R、类型、u 满足所有观测。

光学清除只依赖距离，不给出射频可见性或朝向条件。成功清除后的无信号、失败清除另列 `post_removal_actions`；它们属于移除后的场景，不能回头限制原源的接收半径或发射方向。

## 3. 共享接收半径

同一源只有一个固定的 R∈[1000,1500]。给定候选位置 g，全部正射频观测给出

\[
R\ge L(g)=\max\{1000,\max_{p\in P_+}\|g-p\|\}.
\]

若凸外包为 H，则输出数值保守界

\[
\max\{1000,\max_{p\in P_+}\operatorname{dist}(p,H)\}
\le L(g)\le
\max\{1000,\max_{p\in P_+}\max_{v\in\operatorname{Vert}(H)}\|v-p\|\}.
\]

代码对下界减、上界加小数值裕量。左边使用交换 min/max 的放宽，**不声称是 L(g) 的精确最小值**；右边是函数值的外界，不是 R 的上界。R 仍可大于 L(g)，无条件上界仍是 1500 m。

问题 3 中每条无信号给出严格上界 R<|g−p|。问题 4 中无信号也可能来自背向，不能无条件用它缩短 R。输出保留 g 依赖的表达式和所有测点，不将某个中心估计的距离代入当作真接收半径。

## 4. 固定方向、类型和全局计数

问题 4 每频道保留全向/定向两种分支。定向分支只有一个共享单位向量 u；全部正观测给出

\[
(p-g)\cdot u\ge0,
\]

这是给定 g 时关于 u 的半平面；给定 u 时也可改写为关于 g 的半平面。每条无信号保留

\[
\|p-g\|>R\quad\lor\quad
(\text{定向且 }(p-g)\cdot u<0).
\]

同一 g/R/u 必须同时满足所有项，不能逐观测重新挑选方向。原点 near 加上周围四个 100 m 处无信号就是反例：凸外包依然非空，四个背向要求却不可能由同一个 u 满足。

事后公开 N/Ndir 只增加 `sum existence_f=N` 和 `sum existence_f*directional_f=Ndir` 的全局耦合约束。知道两个频道中有一个定向源，通常无法知道究竟是哪一个；归档不把总数强行分配到个体标签。

## 5. 核验

`tests/test_posterior_archive.py` 初次实际运行 **13 项全部通过**（0.66 s）。包括 clear20≠真坐标、Q4无信号≠超接收半径、严格负边界与共享 R、Ndir不能分配频道类型、非空凸包不代表共享 u 可行、移除后观测隔离、失败清除孔洞、幂等去重与冲突、R下界的保守性、本地 Q4 完整调查后真源仍同时满足归档和原知识账本、CLI不读取隐藏文件、禁止混合案例。

`compatible_candidate` 只在离线评价中检查一组固定候选状态，不做优化，也不证明存在解。真实参数只由测试评价器用于核验，生产策略不会调用该函数接收真值。
