# 最终候选CLI交付冒烟验证

实际使用docs/frozen_candidate_local_run.md所述本地CLI接口，真实candidate.json未修改。--count指局数，因此为覆盖每题源数10/16的四个案例，先按生成器首个随机抽样选新seed，再各执行--count 1；未按策略耗时筛选seed。

```powershell
& 'D:\st_python\python.exe' scripts/run_frozen_candidate.py --candidate results/round2/candidate.json --problem 3 --seed 202609190101 --count 1 --output results/round2/candidate_local_smoke/p3_n10_seed202609190101
& 'D:\st_python\python.exe' scripts/run_frozen_candidate.py --candidate results/round2/candidate.json --problem 3 --seed 202609190201 --count 1 --output results/round2/candidate_local_smoke/p3_n16_seed202609190201
& 'D:\st_python\python.exe' scripts/run_frozen_candidate.py --candidate results/round2/candidate.json --problem 4 --seed 202609190307 --count 1 --output results/round2/candidate_local_smoke/p4_n10_seed202609190307
& 'D:\st_python\python.exe' scripts/run_frozen_candidate.py --candidate results/round2/candidate.json --problem 4 --seed 202609190400 --count 1 --output results/round2/candidate_local_smoke/p4_n16_seed202609190400
```

命令均成功退出，四案例全清，独立复核外包真值零违规。四份请求/决策日志完整，进入/退出、最终清除数和请求唯一性均核对；候选副本、CLI、normalizer、依赖及原核心哈希均匹配。

|问题|N|Ndir|master seed|虚拟秒|请求行|外包快照核验数|
|---|---:|---:|---:|---:|---:|---:|
|3|10|0|202609190101|3624.378783|117|45|
|3|16|0|202609190201|4563.836665|122|92|
|4|10|4|202609190307|10183.006064|478|166|
|4|16|8|202609190400|10794.192670|343|217|

这四次仅为交付CLI工程验证，单列保留，不并入主要6760次实验，不作为新调参/确认样本，也不是官方成绩。完整核验字段见smoke_audit.json。
