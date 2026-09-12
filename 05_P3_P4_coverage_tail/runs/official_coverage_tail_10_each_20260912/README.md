# 最新反馈算法官方演练结果

打开 `report/results.html` 查看 P3、P4 各10局的完整表格、诊断统计和日志链接。

- `report/summary.json`：结构化汇总和每局诊断。
- `cases/<案例>/requests.jsonl`：全部请求、响应、时间戳和请求前后状态。
- `cases/<案例>/decisions.jsonl`：全部决策、反馈后几何范围和规划摘要。
- `cases/<案例>/result.json`：算法最终统计；`diagnostics.json`：反馈计数与成本分解。
- `cases/<案例>/ready_ui.json`、`post_exit_ui.json`：官方页面公开结果快照。
- `cases/<案例>/original_logs/`：逐字节保留的官方加密行为日志。
- `source/`：本次策略与运行脚本快照；`selection.json`：实际策略参数。
- `console.log`：整批运行控制台日志。

时间均为秒。主表“平均时间/源”为该局总虚拟时间除以源数；汇总对每局该指标取算术平均。失败列为清除尝试失败次数，不代表该局失败。程序时间含客户端通信。

本次为新生成的官方演练案例，没有同案例旧算法对照，不能直接认定相对旧版的提速幅度。所有案例均列出，不按表现筛选。

再次运行：在原项目目录执行 `D:\st_python\python.exe -B -X utf8 -u run_selected_official.py --out <新的输出目录>`。程序使用模拟器当前已登录队号，也可显式传入 `--robot-id`；只启动演练测试。
