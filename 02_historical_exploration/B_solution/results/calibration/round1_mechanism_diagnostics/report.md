# 机制筛查后的解释性诊断

输入为已提取公开特征。官方 fit 与 development 分开；原正式统计状态不改变。这里的半径是相对冻结估计点的名义探针距离，不是真实源距。

1250 箱指 1250≤名义距离<1500，可能混入锚点复测/邻点。下面只用 kind=radial 且名义1450m的精确探针作对照；bin_1250_measurement_design.json 保留箱内类型、名义值及源距离保守区间范围。

|模型|题|官方划分|指标|官方局数/均值|本地局数/均值|本地减官方区间|
|---|---:|---|---|---|---|---|
|broad_joint__radius_mixed__noise_deterministic|3|fit|radial_1100_visibility|15/0.85|20/0.7125|[-0.25744791666666667, 0.0]|
|broad_joint__radius_mixed__noise_deterministic|3|fit|radial_1450_visibility|15/0.21666666666666667|20/0.06875|[-0.30109375, 0.02291666666666667]|
|broad_joint__radius_mixed__noise_deterministic|3|fit|neighbor_1m_absolute_report_delta_deg|15/0.07683333333333214|20/0.7166250000000005|[0.550856250000003, 0.7342083333333376]|
|broad_joint__radius_mixed__noise_deterministic|3|development|radial_1100_visibility|5/0.475|20/0.7125|[-0.04999999999999993, 0.51875]|
|broad_joint__radius_mixed__noise_deterministic|3|development|radial_1450_visibility|5/0.025|20/0.06875|[-0.043750000000000004, 0.14703124999999986]|
|broad_joint__radius_mixed__noise_deterministic|3|development|neighbor_1m_absolute_report_delta_deg|5/0.06200000000000046|20/0.7166250000000005|[0.5601781249999983, 0.75763125]|
|smoothed_joint__radius_max__noise_deterministic|3|fit|radial_1100_visibility|15/0.85|20/1.0|[-0.14675967970462533, 0.25635416666666666]|
|smoothed_joint__radius_max__noise_deterministic|3|fit|radial_1450_visibility|15/0.21666666666666667|20/0.99375|[0.64375, 0.9074479166666665]|
|smoothed_joint__radius_max__noise_deterministic|3|fit|neighbor_1m_absolute_report_delta_deg|15/0.07683333333333214|20/0.7171249999999993|[0.5405791666666715, 0.7622322916666674]|
|smoothed_joint__radius_max__noise_deterministic|3|development|radial_1100_visibility|5/0.475|20/1.0|[0.05324032029537462, 0.825]|
|smoothed_joint__radius_max__noise_deterministic|3|development|radial_1450_visibility|5/0.025|20/0.99375|[0.9187500000000001, 1.0]|
|smoothed_joint__radius_max__noise_deterministic|3|development|neighbor_1m_absolute_report_delta_deg|5/0.06200000000000046|20/0.7171249999999993|[0.5395531249999995, 0.7855437500000017]|
|smoothed_joint__radius_min__noise_deterministic|3|fit|radial_1100_visibility|15/0.85|20/0.15|[-0.80625, -0.58125]|
|smoothed_joint__radius_min__noise_deterministic|3|fit|radial_1450_visibility|15/0.21666666666666667|20/0.00625|[-0.3438541666666667, -0.07697916666666677]|
|smoothed_joint__radius_min__noise_deterministic|3|fit|neighbor_1m_absolute_report_delta_deg|15/0.07683333333333214|20/0.6781250000000018|[0.5039145833333324, 0.6983489583333362]|
|smoothed_joint__radius_min__noise_deterministic|3|development|radial_1100_visibility|5/0.475|20/0.15|[-0.56875, -0.05]|
|smoothed_joint__radius_min__noise_deterministic|3|development|radial_1450_visibility|5/0.025|20/0.00625|[-0.075, 0.0125]|
|smoothed_joint__radius_min__noise_deterministic|3|development|neighbor_1m_absolute_report_delta_deg|5/0.06200000000000046|20/0.6781250000000018|[0.5220468750000031, 0.7222500000000062]|
|smoothed_joint__radius_mixed__noise_correlated|3|fit|radial_1100_visibility|15/0.85|20/0.7375|[-0.2416666666666667, 0.022916666666666696]|
|smoothed_joint__radius_mixed__noise_correlated|3|fit|radial_1450_visibility|15/0.21666666666666667|20/0.0625|[-0.30833333333333335, 0.007447916666666533]|
|smoothed_joint__radius_mixed__noise_correlated|3|fit|neighbor_1m_absolute_report_delta_deg|15/0.07683333333333214|20/0.07125000000000448|[-0.035351041666661705, 0.025625000000004707]|
|smoothed_joint__radius_mixed__noise_correlated|3|development|radial_1100_visibility|5/0.475|20/0.7375|[-0.009531249999999956, 0.5437500000000001]|
|smoothed_joint__radius_mixed__noise_correlated|3|development|radial_1450_visibility|5/0.025|20/0.0625|[-0.043750000000000004, 0.14078124999999986]|
|smoothed_joint__radius_mixed__noise_correlated|3|development|neighbor_1m_absolute_report_delta_deg|5/0.06200000000000046|20/0.07125000000000448|[-0.01557812499999412, 0.03463750000000672]|
|smoothed_joint__radius_mixed__noise_deterministic|3|fit|radial_1100_visibility|15/0.85|20/0.725|[-0.24791666666666667, 0.006250000000000036]|
|smoothed_joint__radius_mixed__noise_deterministic|3|fit|radial_1450_visibility|15/0.21666666666666667|20/0.0625|[-0.30833333333333335, 0.007447916666666533]|
|smoothed_joint__radius_mixed__noise_deterministic|3|fit|neighbor_1m_absolute_report_delta_deg|15/0.07683333333333214|20/0.7263749999999991|[0.563680208333333, 0.7370312499999989]|
|smoothed_joint__radius_mixed__noise_deterministic|3|development|radial_1100_visibility|5/0.475|20/0.725|[-0.022031250000000023, 0.53125]|
|smoothed_joint__radius_mixed__noise_deterministic|3|development|radial_1450_visibility|5/0.025|20/0.0625|[-0.043750000000000004, 0.14078124999999986]|
|smoothed_joint__radius_mixed__noise_deterministic|3|development|neighbor_1m_absolute_report_delta_deg|5/0.06200000000000046|20/0.7263749999999991|[0.5735406249999992, 0.7611437500000017]|
|smoothed_joint__radius_mixed__noise_extreme|3|fit|radial_1100_visibility|15/0.85|20/0.725|[-0.24484375000000003, 0.010416666666666689]|
|smoothed_joint__radius_mixed__noise_extreme|3|fit|radial_1450_visibility|15/0.21666666666666667|20/0.0625|[-0.30833333333333335, 0.007447916666666533]|
|smoothed_joint__radius_mixed__noise_extreme|3|fit|neighbor_1m_absolute_report_delta_deg|15/0.07683333333333214|20/0.09537500000000029|[-0.028545833333332417, 0.07515312500000192]|
|smoothed_joint__radius_mixed__noise_extreme|3|development|radial_1100_visibility|5/0.475|20/0.725|[-0.018749999999999933, 0.5315624999999997]|
|smoothed_joint__radius_mixed__noise_extreme|3|development|radial_1450_visibility|5/0.025|20/0.0625|[-0.043750000000000004, 0.14078124999999986]|
|smoothed_joint__radius_mixed__noise_extreme|3|development|neighbor_1m_absolute_report_delta_deg|5/0.06200000000000046|20/0.09537500000000029|[-0.006006250000005271, 0.085828125000005]|
|broad_joint__radius_mixed__noise_deterministic|4|fit|neighbor_1m_absolute_report_delta_deg|15/0.05116666666666513|20/0.5810000000000006|[0.41170208333333574, 0.6415625000000031]|
|broad_joint__radius_mixed__noise_deterministic|4|development|neighbor_1m_absolute_report_delta_deg|5/0.04150000000000063|20/0.5810000000000006|[0.4311062500000011, 0.677509375000003]|
|smoothed_joint__radius_max__noise_deterministic|4|fit|neighbor_1m_absolute_report_delta_deg|15/0.05116666666666513|20/0.6662500000000027|[0.5154145833333389, 0.7155395833333372]|
|smoothed_joint__radius_max__noise_deterministic|4|development|neighbor_1m_absolute_report_delta_deg|5/0.04150000000000063|20/0.6662500000000027|[0.5280656250000079, 0.7399000000000022]|
|smoothed_joint__radius_min__noise_deterministic|4|fit|neighbor_1m_absolute_report_delta_deg|15/0.05116666666666513|20/0.6517500000000009|[0.5192552083333379, 0.6821833333333355]|
|smoothed_joint__radius_min__noise_deterministic|4|development|neighbor_1m_absolute_report_delta_deg|5/0.04150000000000063|20/0.6517500000000009|[0.5287375000000001, 0.6945062499999991]|
|smoothed_joint__radius_mixed__noise_correlated|4|fit|neighbor_1m_absolute_report_delta_deg|15/0.05116666666666513|20/0.05512500000000102|[-0.006688541666663848, 0.01562916666667045]|
|smoothed_joint__radius_mixed__noise_correlated|4|development|neighbor_1m_absolute_report_delta_deg|5/0.04150000000000063|20/0.05512500000000102|[0.0017374999999975774, 0.025625000000004512]|
|smoothed_joint__radius_mixed__noise_deterministic|4|fit|neighbor_1m_absolute_report_delta_deg|15/0.05116666666666513|20/0.6853750000000008|[0.5400812500000011, 0.7298552083333346]|
|smoothed_joint__radius_mixed__noise_deterministic|4|development|neighbor_1m_absolute_report_delta_deg|5/0.04150000000000063|20/0.6853750000000008|[0.5579281249999991, 0.7561093749999981]|
|smoothed_joint__radius_mixed__noise_extreme|4|fit|neighbor_1m_absolute_report_delta_deg|15/0.05116666666666513|20/0.054875000000003026|[-0.006896874999997947, 0.015313541666669908]|
|smoothed_joint__radius_mixed__noise_extreme|4|development|neighbor_1m_absolute_report_delta_deg|5/0.04150000000000063|20/0.054875000000003026|[0.0012499999999998216, 0.025315625000001164]|

所有区间都是整局层面的解释性诊断；本次家族未作同时显著性校正。重复巨大差异可为模型筛查提供解释，但不声称恢复真实R分布或误差公式。确定性排除以独立几何见证为准。失败清除数只作补充，不用于反调模型使耗时吻合。
