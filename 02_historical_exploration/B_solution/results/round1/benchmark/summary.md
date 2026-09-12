# 分层本地同场景配对结果

各池、题号、组成模型独立报告；不混成官方平均值。候选只改变局部测量上限。
失败不当作短完成：主指标对失败预注册为360000秒惩罚（这是评价损失，并非补造完成时间），另列完整率差和双成功子集时间。delta=候选−固定L5基线，负值更快。
点态95%配对bootstrap区间仅描述本合成分布，未做多重比较校正；筛选仅用development。confirmation先天留出，不能看完再回调参数。

|池|题号|组成模型|分区|候选|配对n|基线/候选失败|惩罚差均值 s|95% CI s|双成功时间差 s|退步率|
|---|---|---|---|---|---:|---:|---:|---|---:|---:|
|broad|3|broad_joint|all|joint_triangular_l1|2|0/0|-285.21|[-345.38, -225.04]|-285.21|0.00|
|broad|3|broad_joint|all|joint_triangular_l2|2|0/0|-216.48|[-276.22, -156.74]|-216.48|0.00|
|broad|3|broad_joint|all|joint_triangular_l3|2|0/0|-154.56|[-199.94, -109.17]|-154.56|0.00|
|broad|3|broad_joint|development|joint_triangular_l1|2|0/0|-285.21|[-345.38, -225.04]|-285.21|0.00|
|broad|3|broad_joint|development|joint_triangular_l2|2|0/0|-216.48|[-276.22, -156.74]|-216.48|0.00|
|broad|3|broad_joint|development|joint_triangular_l3|2|0/0|-154.56|[-199.94, -109.17]|-154.56|0.00|
|broad|4|broad_joint|all|joint_triangular_l1|2|0/0|-572.40|[-598.91, -545.90]|-572.40|0.00|
|broad|4|broad_joint|all|joint_triangular_l2|2|0/0|-433.15|[-450.79, -415.51]|-433.15|0.00|
|broad|4|broad_joint|all|joint_triangular_l3|2|0/0|-306.68|[-309.03, -304.33]|-306.68|0.00|
|broad|4|broad_joint|development|joint_triangular_l1|2|0/0|-572.40|[-598.91, -545.90]|-572.40|0.00|
|broad|4|broad_joint|development|joint_triangular_l2|2|0/0|-433.15|[-450.79, -415.51]|-433.15|0.00|
|broad|4|broad_joint|development|joint_triangular_l3|2|0/0|-306.68|[-309.03, -304.33]|-306.68|0.00|

噪声模型细分及最坏退步、所有失败见JSON/CSV。shortlist是探索性候选，最多每题2个；正式评测未授权，脚本不包含官方请求能力。
