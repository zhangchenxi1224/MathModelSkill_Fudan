# 数据驱动实验完成清单

此清单按实际证据更新。新官方round2已完成全部120局及独立审计，采集器正常退出；最终整篇15页PDF及逐页实际视觉QA已完成。新ZIP尚待最后打包核验，不能随PDF完成自动勾选。

## 已完成

- [x] 首轮官方演练160/160，全清160、失败0；每题60基线+20标准调查。独立重放53959条唯一accepted请求，40个调查全站全频道证书通过，0审计问题。证据：`results/round1/integrity_final/overall.json`。
- [x] 120局fit拟合联合组成；40局development单独检查。原模型、参数、证据不足状态及后续筛选决定保留。证据：`results/calibration/round1`、`results/round1/composition_screen_decision.json`。
- [x] 本地同协议检查960次（800+160），全清、0外包真值违规；24统计组整体证据不足，未改写为模型已验证。证据：`results/round1/protocol_checks_primary`、`protocol_checks_broad_reference`、`results/calibration/round1_checked_combined`。
- [x] 首轮160局后验外包和固定隐藏参数符号账本归档，0归档错误；2030条半径见证与40局160近邻点对检查保留，近邻无反例不等于模型正确。证据：`results/round1/posterior_archives`、`results/calibration/radius_extreme_witnesses`、`neighbor_error_witnesses`。
- [x] Stage1：1000世界、4000策略执行，全部全清。发布分析3200开发臂+400冻结L1/L5确认臂；未选L2/L3确认400臂原始归档保留，未用于调参。两题选择L1并通过冻结确认门槛。
- [x] Stage2：400新世界、640开发+120确认=760执行，全部全清；实际种子2026091207。P3保留normal，P4保留NoSignal；小样本跨零区间与退步全部报告。
- [x] Stage3：400新世界、960开发+80确认=1040执行，全部全清；实际种子2026091307。两题保留joint，确认仅可靠性检查，没有虚构调度收益。
- [x] 三阶段共1800不同世界、5800物理执行、0外包真值违规；独立逐动作审计5400执行，明确不含Stage1未选确认400。证据：各阶段 `integrity_development` / `integrity_confirmation`。960机制检查另计，两类主要本地运行总数6760。
- [x] 选择链和最终候选在新官方动作之前冻结：`results/round2/candidate.json`，P3=L1+normal+joint，P4=L1+NoSignal+joint，基线仍为原L5。原10core散列、NoSignal散列及全部选择链散列保留。
- [x] 新官方样本量、独立两臂块随机分配和120个assignment冻结。5%目标下预算功效不足已披露；每题基线/候选各30，不能套配对CI。证据：`results/round1/validation_precision_decision.json`、`results/round2/plan.json` / `validation_freeze.json`。
- [x] 登录过期在新案例或机器人请求之前恢复，事件记录为局前操作中断，0案例、0请求；候选/分配未变。证据：`results/round2/startup_incident.json`。配置与事件不存账号密码。
- [x] 最终候选本地独立入口完成，哈希核验、失败保留、实际L1/NoSignal smoke及输入测试通过；没有由复现入口发送官方请求。说明：`docs/frozen_candidate_local_run.md`。
- [x] 最新完整测试276项通过，33.17秒；原始命令和终端结束文本保存在 `results/round2/test_run_final_delivery.json`。先前273项记录 `test_run_delivery.json` 保留。测试通过不替代PDF实际渲染检查。

## 新官方结果与交付状态

- [x] 新官方round2按原120槽完成，每题基线/候选各30局，全部全清。首8局操作检查通过后执行原序列112局；无效果提前停止或调参，采集器正常exit0。
- [x] 实际120局独立逐请求、计时、停止证据、案例数及原日志SHA审计通过：34861条唯一accepted请求、1542次成功清除、0问题，最大计时差约0.000006988秒。证据：`results/round2/integrity_final/overall.json`。
- [x] 最终公开UI与停止证据补充审计通过：1542源的1149全向、393定向与界面一致；109局完整覆盖停止、11局清除16个不同频道后达到题设数量上界。官方侧未读取隐藏精确位置，不能将日志审计0问题写成外包真值违规0。
- [x] 按题、独立两臂实际总结完成，5000次bootstrap；成功条件下完成时间与失败惩罚区间分别保留，零失败小样本限制明确。证据：`results/round2/validation_summary.json`、`results/pipeline_summary/official_validation_metrics.csv` / `official_validation_cases.csv`。
- [x] 新官方P3/P4两张窄图及PDF渲染QA完成。原9张图和其PDF渲染页与旧已核验版本逐字节一致，完整11页图QA保存在 `results/pipeline_summary/report_figures/visual_qa.json`。
- [x] 根据真实结果更新并冻结 `docs/data_driven_experiment_report.md` 和本清单；区分官方演练、本地机制检查、策略物理执行与已发布分析行数。最终正文SHA256为 `30dcb208b95a6e21e8d94fc21e228f4f3b8d39fea6bee3b7136a7c73cd3599d5`。
- [x] 独立新报告 `output/pdf/B题数据驱动实验报告.pdf` 已由Markdown使用ReportLab生成，共15页；不是LaTeX编译。15页均经Poppler渲染并实际逐页查看，11张窄图、8张表及公式完整。源文/PDF SHA、每页事实与图片保存在 `results/pipeline_summary/report_visual_qa`，路径可随包移植；同名 `.build.json` / `.qa.json` 并存。PDF SHA256为 `db57d78ee087ddd79099c53ae9bdd3df42aa3d565ea49a2703f232c02a3a22ed`。
- [ ] 生成新ZIP并逐文件SHA验证，不覆盖旧论文PDF、旧QA或旧交付包；原始官方加密日志保持原文件名和字节。此项要等实验及报告真正完成后执行。

## 明确保留的边界

- [x] 六次正式测试未执行；不把演练或本地成绩写成正式结果。
- [x] 未唯一识别的半径、朝向、误差机制继续保留为假设；非空外包不等于全部固定隐藏参数联合可行已证。
- [x] 有限样本零失败不证明总体零失败；局部或组合策略平均改善不掩盖实际退步，也不把全部组合收益归因于某一个模块。
- [x] 复现与恢复使用既有manifest、selection、plan和candidate；不一键重建分配，不覆盖冻结输入。

上述新官方结果、正文冻结和整篇PDF逐页QA均已按实际文件更新。包内清单是打包前快照；ZIP是否实际构建并通过逐文件核验，以同名外置 `.manifest.json` 为准。保留ZIP未勾选状态，避免把预定打包当作已完成，也无需为更新包内自述反复重打包。
