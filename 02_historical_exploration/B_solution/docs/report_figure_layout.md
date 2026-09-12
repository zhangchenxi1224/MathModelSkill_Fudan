# 新研究报告的 A4 窄图

`scripts/plot_report_figures.py` 仅读实际已发布数据，生成9张独立窄图，原宽图保留。图宽6.6英寸，最小标签10pt；按现有正文497.28pt宽、405pt最大图高等比缩放后，9图的预计最小文字为10.23pt。输出均有PNG、矢量PDF、实际source rows及输入SHA；`visual_qa.json` 记录实际9页PDF渲染检查与包内相对路径。

Stage1只画已冻结L1−L5，每题四设计组，开发与已发布确认分别两色，nD/nC分别标注。没有打开未使用的L2/L3确认性能。Stage2只画实际P4比较，P3保留原选择器；Stage3每题完整呈现8项开发调度比较，joint保留，不造自身确认差值。区间取原归档的失败惩罚耗时差点态配对bootstrap。

主报告位于 `docs` 时，可依原章节将宽图替换为：

```markdown
![首轮各协议实际完成时间](../results/pipeline_summary/report_figures/round1_completion_time.png)
![首轮各协议实际动作成本](../results/pipeline_summary/report_figures/round1_action_costs.png)
![问题3全部首轮案例的描述性组成](../results/pipeline_summary/report_figures/round1_p3_composition.png)
![问题4全部首轮案例的描述性联合组成](../results/pipeline_summary/report_figures/round1_p4_composition.png)
![问题3冻结L1对L5的开发与确认比较](../results/pipeline_summary/report_figures/stage1_p3_l1_l5.png)
![问题4冻结L1对L5的开发与确认比较](../results/pipeline_summary/report_figures/stage1_p4_l1_l5.png)
![问题4无信号选点的开发与确认比较](../results/pipeline_summary/report_figures/stage2_p4_nosignal.png)
![问题3调度开发比较与保留joint](../results/pipeline_summary/report_figures/stage3_p3_scheduling.png)
![问题4调度开发比较与保留joint](../results/pipeline_summary/report_figures/stage3_p4_scheduling.png)
```

复现已有图：

```powershell
& 'D:\st_python\python.exe' scripts/plot_report_figures.py
```

新官方120局全部取得最终审计、真实 `validation_summary.json` 生成后，已实际执行 `--validation results/round2`，在相同目录生成 `round2_p3_validation`、`round2_p4_validation`。每题独立两臂各30局，图中显示失败惩罚独立区间、全清比例和实际成本分母。没有生成空结果或中途成绩图；入口遇到未执行案例或缺终局审计会拒绝绘制新官方图。

```markdown
![问题3新官方独立两臂验证](../results/pipeline_summary/report_figures/round2_p3_validation.png)
![问题4新官方独立两臂验证](../results/pipeline_summary/report_figures/round2_p4_validation.png)
```

现有11张PDF都经Poppler渲染核验，文字、样本数、误差区间与正负方向完整，无裁切或遮挡。追加新官方图时重新生成的前9张PNG及PDF渲染页，与原已核验版本逐字节一致，先前QA依据没有丢失。单图QA与最终整篇报告QA分别保留，后者也已完成15页实际检查。

## 整篇 PDF 的预检与最终 QA 顺序

新官方120局仍在运行时，仅对正文SHA256 `77ef22c1557d467eb57b7c3296f2fc5ff8b05f558431f0fb0f1587e95b6c23d4` 快照执行了 `build_research_report_pdf.py --check`，没有创建PDF或Canvas，也没有读取中途双臂收益。61块、69个排版对象通过检查；9图尺寸完整，前7图高286—339pt，最后两图486×405pt；5表均497.28pt宽，最大223pt高，最多2行单元，字号8.2—8.8pt，无宽度溢出。预检记录在 `tmp/data_driven_pdf_preflight.json` 与 `tmp/data_driven_table_preflight.json`。这些度量不能代替最终页级视觉审核。

待全部120局终局审计、实际独立两臂总结和正文冻结之后，按以下顺序处理：

1. 核对冻结正文及输入图的SHA，生成两张实际官方窄图并逐张检查其PDF渲染；不给缺失结果补图。
2. 对更新后的完整正文再次运行 `--check`。检查5表及新增官方表是否超宽，图注与图能否放在同一页，阶段标题不能孤立在页底。若出现问题，仅调整排版辅助，不改统计值。
3. 按PDF技能先记录实际authoring操作，再用bundled Python、嵌入字体和 `--render` 生成独立新报告。旧论文、旧PDF、旧构建和视觉QA保持不变。迭代时使用独立临时版本，不能覆盖已有交付。
4. 用Poppler逐页渲染，实际检查每一页：中文和公式字形、表头重复、数值断行、图字号、图注归属、标题/段落孤行、边界裁切与页码。文本提取和宽度预检只能补充，不能替代目视检查。
5. 最终通过的PDF、同名build记录及QA摘要记录源文与PDF SHA。每页图片与逐页检查记录复制到 `results/pipeline_summary/report_visual_qa`，记录包内相对路径；不依赖本机绝对tmp路径。完成后再允许最终打包脚本收录。

上述早期预检仍保留为历史记录。最终冻结正文SHA256为 `30dcb208b95a6e21e8d94fc21e228f4f3b8d39fea6bee3b7136a7c73cd3599d5`；新的完整预检包括11图、8表，未检测到宽度溢出。已经用Markdown/ReportLab生成 `output/pdf/B题数据驱动实验报告.pdf`，PDF SHA256为 `db57d78ee087ddd79099c53ae9bdd3df42aa3d565ea49a2703f232c02a3a22ed`，不是LaTeX编译。

最终15页均已用Poppler以110dpi渲染并逐页通过实际 `view_image` 检查，root另实际复核第12、15页。表格未被截断，所有图与图注同页，公式、统计区间与276项测试记录清楚。第10、13页的下方余白用于保留下一张完整图，并未以压小字体填满页面。所有最终页图、每页观察事实、静态预检与辅助文本提取保存在 `results/pipeline_summary/report_visual_qa`；同名 `.qa.json` 指向可移植记录。新ZIP的最终状态以其外置 `.manifest.json` 为准。
