# 阶段2单因素同场景配对

因素：`normal_vs_nosignal_measurement_selection`；基线：`normal`。各题的L与其余配置都已冻结。
失败惩罚360000秒；失败未当成短完成。按池/题/组成/噪声分别报告，区间是点态探索性配对bootstrap。
新的独立场景种子不复用之前确认集。development与confirmation分开运行、分开存放；确认只比较冻结选择与基线。

design_mixture是设计组合，不是官方总体，所有机制层单独保留。

|池|题|模型|机制|分区|候选|配对n|失败 基/候|惩罚时间差均值s|95% CI|双成功退步率|
|---|---|---|---|---|---|---:|---:|---:|---|---:|
|broad|4|broad_joint|__design_mixture__|confirmation|nosignal|16|0/0|-246.79|[-523.86,10.16]|0.31|
|broad|4|broad_joint|assumption__radius_max__noise_extreme__scale_150|confirmation|nosignal|2|0/0|481.42|[430.64,532.19]|1.00|
|broad|4|broad_joint|assumption__radius_max__noise_extreme__scale_300|confirmation|nosignal|1|0/0|576.58|[NA,NA]|1.00|
|broad|4|broad_joint|assumption__radius_max__noise_extreme__scale_75|confirmation|nosignal|1|0/0|-1218.02|[NA,NA]|0.00|
|broad|4|broad_joint|assumption__radius_min__noise_correlated__scale_300|confirmation|nosignal|2|0/0|-548.03|[-1158.33,62.28]|0.50|
|broad|4|broad_joint|assumption__radius_min__noise_correlated__scale_75|confirmation|nosignal|1|0/0|-184.83|[NA,NA]|0.00|
|broad|4|broad_joint|assumption__radius_min__noise_deterministic__scale_150|confirmation|nosignal|1|0/0|-700.82|[NA,NA]|0.00|
|broad|4|broad_joint|assumption__radius_min__noise_deterministic__scale_75|confirmation|nosignal|1|0/0|-348.50|[NA,NA]|0.00|
|broad|4|broad_joint|assumption__radius_min__noise_extreme__scale_150|confirmation|nosignal|1|0/0|-76.98|[NA,NA]|0.00|
|broad|4|broad_joint|assumption__radius_mixed__noise_correlated__scale_150|confirmation|nosignal|1|0/0|-1113.24|[NA,NA]|0.00|
|broad|4|broad_joint|assumption__radius_mixed__noise_deterministic__scale_150|confirmation|nosignal|1|0/0|-443.35|[NA,NA]|0.00|
|broad|4|broad_joint|assumption__radius_mixed__noise_deterministic__scale_75|confirmation|nosignal|2|0/0|-3.13|[-182.86,176.61]|0.50|
|broad|4|broad_joint|assumption__radius_mixed__noise_extreme__scale_150|confirmation|nosignal|1|0/0|-194.34|[NA,NA]|0.00|
|broad|4|broad_joint|assumption__radius_mixed__noise_extreme__scale_300|confirmation|nosignal|1|0/0|-105.70|[NA,NA]|0.00|
|calibrated|4|broad_joint|__design_mixture__|confirmation|nosignal|8|0/0|-7.38|[-313.61,266.53]|0.50|
|calibrated|4|broad_joint|mixed_correlated150_after_observable_screen|confirmation|nosignal|6|0/0|-17.23|[-428.69,331.30]|0.50|
|calibrated|4|broad_joint|mixed_extreme150_after_observable_screen|confirmation|nosignal|2|0/0|22.18|[-103.68,148.03]|0.50|
|calibrated|4|smoothed_joint|__design_mixture__|confirmation|nosignal|8|0/0|-131.20|[-459.27,197.75]|0.50|
|calibrated|4|smoothed_joint|mixed_correlated150_after_observable_screen|confirmation|nosignal|5|0/0|-183.98|[-667.56,279.77]|0.40|
|calibrated|4|smoothed_joint|mixed_extreme150_after_observable_screen|confirmation|nosignal|3|0/0|-43.22|[-462.13,268.83]|0.67|
|stress|4|stress_design|__design_mixture__|confirmation|nosignal|8|0/0|-620.35|[-1314.73,-10.59]|0.38|
|stress|4|stress_design|assumption__radius_max__noise_correlated__scale_75|confirmation|nosignal|1|0/0|-219.72|[NA,NA]|0.00|
|stress|4|stress_design|assumption__radius_max__noise_deterministic__scale_150|confirmation|nosignal|1|0/0|-192.35|[NA,NA]|0.00|
|stress|4|stress_design|assumption__radius_min__noise_deterministic__scale_150|confirmation|nosignal|2|0/0|-182.72|[-382.56,17.11]|0.50|
|stress|4|stress_design|assumption__radius_min__noise_deterministic__scale_75|confirmation|nosignal|1|0/0|188.79|[NA,NA]|1.00|
|stress|4|stress_design|assumption__radius_min__noise_extreme__scale_150|confirmation|nosignal|2|0/0|-1100.35|[-2434.79,234.08]|0.50|
|stress|4|stress_design|assumption__radius_mixed__noise_extreme__scale_75|confirmation|nosignal|1|0/0|-2173.33|[NA,NA]|0.00|

选择基线自身时确认区可只有一臂，此时没有候选配对比较，不补造改进。正式测试未授权；本脚本不含官方接口。
