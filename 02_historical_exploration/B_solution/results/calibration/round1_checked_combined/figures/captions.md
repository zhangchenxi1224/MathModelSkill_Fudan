# 同协议机制检查图

指标规则先从 schema 声明，再读取最终检查文件。所有候选按名称排序；未按 matched、screen_flag 或效果大小筛选。

横向森林图区间和纵向分布区间直接复制 model_checks.json 的 difference_ci95，包含全零/全一局级率的边界加宽；没有用较窄的原始 bootstrap 区间替代。差值为本地减官方，官方仅 development。每题、每协议、每实际站点设计分开。

O/L 是该指标实际贡献的整局簇数，可能小于组总局数。I 表示 insufficient_evidence；星号表示至少一侧不足5局。M 只是该单项进入预设实质容限，F 只是探索性筛查信号；均不等同于整模型机制已识别或正式拒绝。所有区间逐项解释，多候选共享官方案例和部分本地场景，图不提供族级显著性控制或独立复制。

完整局耗时与清除失败是条件指标，须结合完成率，不能将失败局短投入解释成效率高。相邻报告角差含真实几何变化，同点角差也不是相对真方位的误差；面积比是算法外包代理。原点率分母为20频道；联合指标分母为事后确认存在的频道，两者不混同。

baseline 穿插清除，可能缺完整清除前联合设计；缺失页明确保留，不补零。survey 官方 development 的小样本限制不会因增加本地局数而消失。全部可见站点数箱和既有六个几何最近站点对均展示，没有挑选差异明显的站点。

可见性与成本还受 N/Ndir 组成混合影响，比较差异不能单独解释成接收半径或噪声的因果作用。源数据保留的 survey_nominal_distance_1250 等字段是相对冻结估计点的分箱下界：[1250,1500) m，不是距真源1250 m；本次预声明主图没有从这些箱中挑选显著结果。

源 JSON 保留所有 candidate_checks，包括图中固定指标之外的检查；模型 ID 的全称、输入/声明/脚本 SHA256 与每页原数值可复核。

## P3 / baseline

- M01: broad_joint__radius_mixed__noise_deterministic；组状态 insufficient_evidence；组记录 O15/L60。
- M02: smoothed_joint__radius_max__noise_deterministic；组状态 insufficient_evidence；组记录 O15/L60。
- M03: smoothed_joint__radius_min__noise_deterministic；组状态 insufficient_evidence；组记录 O15/L60。
- M04: smoothed_joint__radius_mixed__noise_correlated；组状态 insufficient_evidence；组记录 O15/L60。
- M05: smoothed_joint__radius_mixed__noise_deterministic；组状态 insufficient_evidence；组记录 O15/L60。
- M06: smoothed_joint__radius_mixed__noise_extreme；组状态 insufficient_evidence；组记录 O15/L60。

## P3 / survey

- M01: broad_joint__radius_mixed__noise_deterministic；组状态 insufficient_evidence；组记录 O5/L20。
- M02: smoothed_joint__radius_max__noise_deterministic；组状态 insufficient_evidence；组记录 O5/L20。
- M03: smoothed_joint__radius_min__noise_deterministic；组状态 insufficient_evidence；组记录 O5/L20。
- M04: smoothed_joint__radius_mixed__noise_correlated；组状态 insufficient_evidence；组记录 O5/L20。
- M05: smoothed_joint__radius_mixed__noise_deterministic；组状态 insufficient_evidence；组记录 O5/L20。
- M06: smoothed_joint__radius_mixed__noise_extreme；组状态 insufficient_evidence；组记录 O5/L20。

## P4 / baseline

- M01: broad_joint__radius_mixed__noise_deterministic；组状态 insufficient_evidence；组记录 O15/L60。
- M02: smoothed_joint__radius_max__noise_deterministic；组状态 insufficient_evidence；组记录 O15/L60。
- M03: smoothed_joint__radius_min__noise_deterministic；组状态 insufficient_evidence；组记录 O15/L60。
- M04: smoothed_joint__radius_mixed__noise_correlated；组状态 insufficient_evidence；组记录 O15/L60。
- M05: smoothed_joint__radius_mixed__noise_deterministic；组状态 insufficient_evidence；组记录 O15/L60。
- M06: smoothed_joint__radius_mixed__noise_extreme；组状态 insufficient_evidence；组记录 O15/L60。

## P4 / survey

- M01: broad_joint__radius_mixed__noise_deterministic；组状态 insufficient_evidence；组记录 O5/L20。
- M02: smoothed_joint__radius_max__noise_deterministic；组状态 insufficient_evidence；组记录 O5/L20。
- M03: smoothed_joint__radius_min__noise_deterministic；组状态 insufficient_evidence；组记录 O5/L20。
- M04: smoothed_joint__radius_mixed__noise_correlated；组状态 insufficient_evidence；组记录 O5/L20。
- M05: smoothed_joint__radius_mixed__noise_deterministic；组状态 insufficient_evidence；组记录 O5/L20。
- M06: smoothed_joint__radius_mixed__noise_extreme；组状态 insufficient_evidence；组记录 O5/L20。
