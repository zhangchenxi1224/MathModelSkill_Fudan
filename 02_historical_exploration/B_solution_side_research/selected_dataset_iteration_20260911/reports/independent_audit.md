# 最终实验独立审计

审计通过：True；问题数：0。本工具不执行策略、不访问官方接口。

|分区|题|冻结策略|配对数|冠军均值/s|候选均值/s|均值差/s|均值比改善|配对97.5%区间/s|退步数|最大退步/s|
|---|---:|---|---:|---:|---:|---:|---:|---|---:|---:|
|development|3|p3_hull_r1150|180|4411.77|3808.74|-603.03|13.669%|[-667.40, -538.45]|13|430.47|
|development|4|p4_hull_L2_d1200|180|10783.19|9619.92|-1163.27|10.788%|[-1339.09, -996.89]|10|1018.47|
|confirmation|3|p3_hull_r1150|120|4461.87|3780.07|-681.79|15.280%|[-757.12, -606.00]|6|415.28|
|confirmation|4|p4_hull_L2_d1200|120|10875.99|9643.02|-1232.97|11.337%|[-1486.92, -999.11]|11|658.85|
|stress|3|p3_hull_r1150|24|4411.28|3865.49|-545.79|12.373%|[-886.16, -226.46]|6|709.03|
|stress|4|p4_hull_L2_d1200|24|10094.32|8382.80|-1711.52|16.955%|[-2480.32, -1055.77]|1|184.96|

分层均值、T/N、P95、最大值、全部失败、调用和移动成本以及逐局审计均保存在同名JSON。费用从唯一接受请求独立重算；不能用失败短耗时替代360000秒罚时。

独立bootstrap：10000次，主种子202609119731；开发区间仅作描述。

## H1/H2分层及尾部

|分区|题|H|冠军→候选均值/s|均值差/s|冠军→候选T/N|冠军→候选成功局P95/s|失败数（冠军/候选）|
|---|---:|---|---|---:|---|---|---|
|development|3|H1|4397.04→3785.00|-612.04|354.69→303.68|4954.43→4560.30|0/0|
|development|3|H2|4426.51→3832.48|-594.02|337.85→291.43|5087.27→4520.56|0/0|
|development|4|H1|10904.37→9582.21|-1322.15|865.42→764.54|12372.26→10438.57|0/0|
|development|4|H2|10662.00→9657.62|-1004.38|844.14→767.75|12060.50→10557.35|0/0|
|confirmation|3|H1|4414.52→3807.80|-606.72|345.82→297.67|4991.38→4601.84|0/0|
|confirmation|3|H2|4509.21→3752.35|-756.87|347.79→289.81|5137.19→4277.66|0/0|
|confirmation|4|H1|10851.38→9662.74|-1188.64|879.91→788.76|12402.78→10757.04|0/0|
|confirmation|4|H2|10900.59→9623.30|-1277.29|863.00→767.46|12378.90→10681.85|0/0|

## 独立请求成本分解

|分区|题|移动/s（冠军→候选）|测量/s|切频/s|光学尝试/s|激光/s|
|---|---:|---|---|---|---|---|
|development|3|3718.57→3147.83|475.33→469.28|92.93→91.06|98.97→74.60|25.98→25.98|
|development|4|8507.06→7597.67|1621.86→1557.53|301.70→277.21|326.75→161.70|25.81→25.81|
|confirmation|3|3775.17→3126.69|469.33→465.33|91.66→90.28|99.55→71.62|26.15→26.15|
|confirmation|4|8521.70→7541.62|1665.33→1620.12|310.32→290.08|353.12→165.68|25.52→25.52|
|stress|3|3757.66→3244.87|455.83→438.33|88.29→84.54|83.50→71.75|26.00→26.00|
|stress|4|7804.82→6341.13|1692.71→1561.88|316.67→284.79|254.12→169.00|26.00→26.00|

## 边界

- Supplied 360 worlds are reused synthetic development/regression, not new holdout.
- New confirmation is synthetic under retained H1/H2, not fresh official practice.
- Development bootstrap is descriptive after selection; confirmation uses frozen choices.
- The two problem-level confirmation contrasts use 97.5% intervals; H-specific 95% intervals are descriptive.
- Mean ratio reduction differs from mean of paired percentages; both are explicitly named.
- All logs use fixed declared error fields; hidden truth is accessed only by this post-run audit.
- Coverage geometry is supplied by separately verified construction; this audit checks actual stopping ledgers and observations.
