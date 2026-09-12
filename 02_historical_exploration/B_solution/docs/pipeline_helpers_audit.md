# 研究材料汇总、打包与独立 PDF 的复核记录

本轮只修改辅助工具，未修改冻结求解器、标准调查、NoSignal 模块或官方执行策略。没有执行最终打包，也没有读取 Stage2 未冻结方案的确认性能。

## 修复与证据口径

`scripts/package_round_pipeline.py` 现在拒绝覆盖已有 ZIP 或同名清单，明确保留旧交付包文件名，使用独占安装和逐文件 SHA256 核对。所需首轮与新官方原始日志、计划、冻结证书、后验归档、机制见证、本地分区结果、源码快照和新 PDF 均在选择范围。计数按实际存在文件记录。

`scripts/summarize_round_pipeline.py` 按计划和实际目录合并枚举官方局次。已启动但缺少退出审计的失败局仍保留；尚未启动单列。缺失的耗时和成本为未知，成功条件下的完成耗时与全部尝试的失败惩罚分开。空 CSV 会清除旧内容。三个本地场景池、题号、构成、分区、策略分别汇总；重复或重叠的局次输入报错。

JSON 中 `local_execution_inventory` 区分 `published_analysis_rows` 与 `physical_strategy_runs`，后者只读 `aggregate_reliability.json` / `run_metadata.json`，不打开未发布性能。Stage1 仅 development 的实际验证为 3200 行分析、4000 局物理执行；若另传已发布 L1/L5 confirmation 的 400 行，则应为 3600 行分析、4000 局物理执行。差额不代表漏跑。

`scripts/plot_official_validation.py` 只对独立两臂给出实际汇总中的差值区间，不套本地配对区间。失败率、未启动数和失败惩罚差值同时可见；成本使用五项都记录完整的实际分母，缺失不补零。单局区间缺失不会崩溃。图的数据与来源 SHA 保存在 `official_validation_source.json`。

## 独立 PDF 渲染

新增 `scripts/build_research_report_pdf.py`，复用现有中文嵌入字体排版器，但隔离构建临时目录、页眉和 QA 文件。输入和新输出路径必须显式给定；旧论文 PDF 和任何已存在的新 PDF 都不能覆盖。先运行 `--check` 可检查语法、字体和宽度；最终稿再执行：

```powershell
& 'C:\Users\Expedition\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -X utf8 scripts/build_research_report_pdf.py --input docs/新研究报告.md --output output/pdf/新研究报告.pdf --render
```

工具生成同名 `.build.json`，记录输入、构建器、PDF 的 SHA256、字体、页数及逐页 PNG 路径。`--render` 仅表示已渲染，仍需逐页视觉核验后单独记录 QA。它不会把渲染成功写成视觉审核通过。最终报告未在此次复核中编译。

## 实际验证

辅助工具与回归诊断共 13 项构造测试通过。实际首轮输入得到 160 局、4 个协议/题号组，Stage1 development 得到 32 个独立分组。两页机制证明 PDF 试编译和模拟官方验证图 PDF 均已逐页目视核查；无溢出、缺字或裁切，缺失数据标注清楚。试编译仅放 `tmp/pdf_report_smoke`，模拟图仅放 `tmp/official_validation_plot_fixture`。

旧论文 PDF、旧构建报告、旧视觉 QA 记录、旧交付 ZIP 的前后 SHA256 完全一致，核验记录为 `tmp/old_paper_hashes_after_aux_qa.json`。最终打包应由主流程在实际实验与报告完成后执行。

## 已冻结 L1 的 development 极值诊断

`results/round1/local/regression_diagnostics` 保存每个池、每题最大有符号差、全部实际失败以及两张实际轨迹图；此批无失败。最差 stress/P4 增加 1279.60 秒：移动增加 900.60 秒，光学尝试增加 519 秒，少测 28 次节省 140 秒；切换与成功清除附加成本不变。光学兜底尝试由 36 次增至 216 次，解释为更早进入、且位置外包更宽的兜底路径。它是 development 的事后极值诊断，不是独立推断或最坏情形界；没有根据确认区极值调整策略。
