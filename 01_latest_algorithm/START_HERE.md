> **历史版本说明：** 本目录保留早期全反馈算法与1200场景结果。当前P3/P4入口已更新至 [双环覆盖与顺路搜索](../05_P3_P4_coverage_tail/README.md)，本目录名为兼容历史链接继续保留。

# 当前算法入口

当前选择：P3 dp_geometric，P4 three_feedback_fast。`selection.json`为采用配置。

完整数据和所有逐步日志均在本目录，另有根目录HTML提供1200个最终记录的相对路径索引。

本地运行：`python -X utf8 evaluate_selected.py --dataset inputs/new240/cases.json --output runs/my_replay --workers 6`。

P3/P4各5局官方结果：`runs/official_practice_5_each_20260912_1430/report/results.html`。

历史原README保留；全包最新阅读入口为上一级的README_交付说明.md和独立实验报告。
