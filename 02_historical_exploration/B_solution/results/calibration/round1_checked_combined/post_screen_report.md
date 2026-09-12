# 机制证据筛查与冻结交接

原24组统计状态、6个完整组合评估和5个物理参数评估均保持原样。新增证据层排除具体参考模型，剩余2组仍标 unresolved，并非官方生成器验证通过。

|物理机制|证据层结论|进入筛查后的候选池|
|---|---|---|
|radius_max__noise_deterministic__correlation_150|screened_out_by_certified_witness|False|
|radius_min__noise_deterministic__correlation_150|screened_out_by_certified_witness|False|
|radius_mixed__noise_correlated__correlation_150|retained_unresolved|True|
|radius_mixed__noise_deterministic__correlation_150|screened_out_by_replicated_protocol_contrast|False|
|radius_mixed__noise_extreme__correlation_150|retained_unresolved|True|

半径端点由公开正观测/无信号与最终保守位置外包的逻辑矛盾排除。独立位置hash实现由严格1m邻点报告变化在fit和development、smoothed和broad两个已检验组合中的重复大偏差筛出；该代理仍含几何变化，不能改称真实误差。

40局邻点公式审计没有给出corr150或exact±1的确定性反例；无反例不等于已验证。只有smoothed组成下对这两种机制做过同协议重复；broad×它们是随后本地鲁棒性测试中的复合不确定性，不能声称全部跨积已通过复现。

全部筛出但合法的机制仍留在broad/stress压力范围。下一轮独立官方案例承担验收，已经用于筛查的development不再包装为未见测试。

唯一stage1交接文件：D:\2026WorkExperience\CUMCM2026Problems\B_solution\results\round1\mechanisms_selected.json；SHA256：76fd7735a152f6f7c201a0d736945a3847af8eaa7ea780aa8bd341ae6ff7ca1e。
