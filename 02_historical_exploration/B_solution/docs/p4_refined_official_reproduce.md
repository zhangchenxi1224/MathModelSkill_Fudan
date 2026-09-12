# P4 新组合官方演练：执行与结果复核

> 用户随后将范围收窄为“只测试最新方案并按表格汇报”。实际执行以 `results/p4_refined_official/scope_update.json` 为准：仅30局 `combined_cover`，原候选与工程pilot槽位取消且未启动。原62槽计划与冻结文件原样保留，不据此宣称做了对照。下方两臂流程属于原先准备方案，未按该范围执行。

实际运行入口：`python scripts/run_latest_p4_practice.py --robot-id <队号>`；只读结果表入口：`python scripts/report_latest_p4_practice.py`。结果在 `results/p4_refined_official/report/latest_results.html`、`.md`、`.json`。

本轮用户明确要求在官方演练运行已冻结的新方案。范围仅为问题4演练：原候选 `current` 与新组合 `combined_cover`。正式测试不在运行器范围内。队号由命令行提供；源码不写入队号或密码。

## 运行版本

沿用上一轮 `results/p4_refinement/source_freeze.json` 固定的核心、NoSignal、refined_coverage、refined_local 与 make_policy。两臂均为追加一次测量、joint调度、800米阈值；新臂仅增加25站覆盖与当前可行区域光学覆盖。下一轮讨论的处理顺序、自适应测量尚未加入。

新增执行壳 `scripts/run_p4_refined_official.py` 使用现有官方演练UI生命周期和四个HTTP接口。策略只接收 RobotClient 和公开配置。事后总数、定向数、原始日志文件名在退出后从官方可见界面提取，未进入当局策略。

## 新演练执行

以下 `run` 会启动新的官方演练案例；报告与审计命令不会。重新实验请使用一个新的输出目录，勿覆盖本轮已保存证据。默认服务为 `http://127.0.0.1:2026`。

```powershell
python scripts/run_p4_refined_official.py prepare --out results/p4_refined_official --per-arm 30 --pilot-per-arm 1 --seed 2026091101
python scripts/run_p4_refined_official.py preflight --out results/p4_refined_official
python scripts/run_p4_refined_official.py run --out results/p4_refined_official --robot-id <队号> --stage pilot --max-new 2
python scripts/run_p4_refined_official.py run --out results/p4_refined_official --robot-id <队号> --stage validation --max-new 60
```

2局工程检查各臂1局，单独报告；主验证各臂30局，以15个区组、每组两臂各2局随机交错。完整性与接入检查通过才扩展；不得根据中途效应调整样本量。启动后出错的案例保留，不替换。运行器保留冻结散列、执行锁与未完成会话保护；如果某次请求的执行状态未确认，不能直接换新案例来绕过。

## 已保存结果的独立复核

```powershell
python scripts/audit_p4_refined_official.py --round results/p4_refined_official --output results/p4_refined_official/integrity
python scripts/report_p4_refined_official.py --round results/p4_refined_official
python scripts/plot_p4_refined_official.py --round results/p4_refined_official
```

原始逐局目录为 `results/p4_refined_official/cases/<case_id>`，含分配、启动意图、官方案例编码、HTTP请求/响应、策略决策、策略结果、退出后的可见界面与公开总数、同名官方加密行为日志。官方日志只按原文件名逐字节复制并核验SHA，不解密或改写。原始文件可能含本机实际队号，作为用户私有证据保存；不要把它们未经处理直接当作匿名竞赛支撑材料。

独立审计核对实际移动、频道、计时、重复请求、成功清除数、每频道负观测与覆盖证书。对新臂核对真实25个测点和构造散列，对旧臂核对原31个点。它不通过模拟器隐藏目标文件检查真值；官方可见事后总数只用于确认清除完整性。

统计采用独立案例比较，主指标为全清时整局总虚拟耗时。失败或未验证局按预设360000秒惩罚另计，同时报告失败率；不能以退出前短耗时代表完成时间。每局T/N、移动、检测、清除失败和尾部仅为解释性指标。工程案例与此前官方案例不混入新主验证。

报告独立分臂bootstrap固定10000次，种子2026091137；每臂零失败仍给出二项比例区间。每臂30局不支持强断言总体零失败或已精确识别最慢5%性能。官方不能复位同一案例，不能将顺序相邻两局画成相同隐藏环境下的配对试验。
