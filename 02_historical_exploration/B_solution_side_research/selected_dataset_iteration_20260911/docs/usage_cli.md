# 单场景本地运行

在工程目录打开 PowerShell。需已有 `campaign/frozen_selection.json` 与同目录 `protocol.json`；运行器按公开题号 3 / 4 读取冻结选择，不使用 H1/H2、案例编号、源数或真值来选择策略。

```powershell
& 'D:\st_python\python.exe' .\solve_local.py --case 'D:\my_cases\new_case.json' --output '.\results\new_case_selected'
& 'D:\st_python\python.exe' .\solve_local.py --case 'D:\my_cases\new_case.json' --output '.\results\new_case_champion' --policy champion
```

两条命令用同一场景分别测试冻结候选和既有 champion。输出目录必须尚不存在，空目录也会被拒绝；续跑应另选新目录。需要其他已冻结选择时可用 `--selection path\frozen_selection.json`，对应 `protocol.json` 必须位于其同目录。

`--case` 接受本数据集的单个案例 JSON，也接受后续新建场景。必要字段为：

- `problem`：整数 3 或 4；
- `sources`：10–16 个不同频道的源，每个含 `channel`、`position`、`radius`，可含 `direction_deg`；第三问仅允许全向源；
- `error_field`：包含整数 `seed`、合法 `mode`、正数 `correlation_length_m`。误差场严格按这三个值重建，不能用顶层 `seed` 替代；
- 可选 `case_id` 和 `scenario_sha256`。新场景没有摘要也可运行；提供摘要则必须匹配去掉 `scenario_sha256` 字段后的规范 JSON SHA256。

误差模式来自冻结模拟器：`deterministic`、`correlated`、`extreme`、`zero`、`positive`、`negative`。这些是本地假设，不能视作官方误差分布已知事实。

执行前验证选择自身摘要、注册协议摘要、当前策略源码与冻结 `source_files` 完全一致、模拟器源码符合注册摘要。打包移动工程后，优先使用工程内 `dataset/official_composition_H1_H2_360_20260911/runtime`，只有此目录不存在才回退到协议记录的历史路径。执行后再次核验源码与模拟器。若修改了算法代码，需要走新的开发与冻结流程；运行器不会悄悄接受漂移代码。

结果目录包含 `result.json`、`requests.jsonl`、`decisions.jsonl`。终端显示是否审计全清 `audit_complete`、虚拟总时间 `T_s` 和每源时间 `T_per_N_s`。全清审计同时要求求解器完成、环境确认全部清除、有退出证据、全轨迹外包零违规以及时间复算一致；清除不足不会得到成功返回码。返回码 0 表示审计成功，1 表示运行完成但审计失败，2 表示输入或部署校验拒绝。

全部动作通过本地冻结模拟器执行，不发送 HTTP 请求，不调用官方测试接口。场景真值只用于构造环境和执行后的审计，策略工厂只接收客户端、公开题号和冻结策略参数。
