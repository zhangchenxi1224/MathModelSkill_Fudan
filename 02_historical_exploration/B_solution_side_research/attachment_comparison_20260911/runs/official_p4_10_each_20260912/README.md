# P4 官方演练原始数据与结果

2026-09-12实际执行，两版各10局，新案例按区组内随机先后交错运行。均为演练，不含正式测试。

- 附件迭代版：p4_route_rescue。25站，路线重排，L1后按门槛至多增加一次补测。
- 当前强基线：synthesis_iteration_20260911 已部署的 p4_L2_d1200_w1。25站，L2，半径权重1，绕行阈值1200米，16源阶段切换。

## 查看顺序

1. results.html 或 results.md：两张完整逐局结果表。
2. results.json：逐局指标、费用分解、反馈数、尾部时间、停止证据。
3. cases/下按p4-attachment-001或p4-baseline-001等目录定位；assignment.json保存执行次序，session.json对应官方编码。
4. requests.jsonl：每次请求原文、原始响应、动作前后客户端状态及时间。
5. decisions.jsonl：选点依据、定位区域、测量/清除结果、覆盖与清除证据、路线重排。
6. observable_states.jsonl：与决策sequence对应的单调时钟、频道知识状态增量、完整机器人状态、路线变化。首条记录包含全频道初始状态；之后未列出的频道延续上一条状态。
7. final_observable_state.json：最终各频道知识、全部已获得观测和覆盖记录。
8. ready_ui.json、post_exit_ui.json：官方演练开始/结束可见页面；目标总数和定向数量只在结束后进入评估结果。
9. original_logs/：官方原始.jlog，文件名和内容保持原样。
10. source_snapshot/：本次实际运行的两版源代码及采集脚本；source_manifest.json为哈希。

程序时间是solver.run实际耗时，包含详细日志写入；虚拟时间以官方响应为准。两组是不同官方案例，编号相同只表示同一执行区组，不能当作相同隐藏场景做配对比较。
