# P4 覆盖与光学清除迭代：复现说明

本轮是**本地模拟实验**。运行器只构造 `LocalSimulator`，没有官方HTTP执行分支，不需要登录信息，也不会消耗官方测试次数。参考策略是上一轮已做官方演练的P4候选（NoSignal、L1、joint），不是更早的L5基线。

## 文件入口

- 原理：`docs/p4_refinement_principles.md`。
- 覆盖实现与证明：`src/bsolver/refined_coverage.py`、`docs/refined_coverage.md`。
- 光学覆盖实现与证明：`src/bsolver/refined_local.py`、`docs/refined_local.md`。
- 批量运行与预先固定的筛选规则：`scripts/run_p4_refinement.py`。
- 世界、误差场、四臂定义：`results/p4_refinement/manifest.json`。
- 执行代码散列与源码快照：`results/p4_refinement/source_freeze.json`、`source_snapshot.zip`。
- 逐请求、逐决策日志与逐局结果：开发/确认子目录内的各运行文件夹；JSONL以gzip无损保存。
- 逐池、组成和相容机制汇总：各分区的 `descriptive_summary.json`、`paired_summary.json`、`pool_paired_summary.json`。
- 失败运行另列于 `failures.json`，失败在配对主统计中按360000秒计入，不能用提前失败的短时间代替完成时间。

正式设计为200个开发世界（80校准、80广覆盖、40压力），800个独立确认世界（320、320、160）。开发比较current、coverage、local_cover、combined_cover四臂，确认只比较current及开发规则选出的1—2个候选。主设计不包含可选adaptive。另有6个不同种子的工程冒烟世界，不能混入开发或确认的效果统计。

本轮固定误差场在各臂共享。同一位置的误差相同；不同策略走到不同位置，会得到该同一误差函数在不同位置的值。策略没有收到隐藏目标列表，真值只进入模拟器和事后评估器。

## 环境与检查

从 `B_solution` 目录执行。项目使用Python；主运行器及几何算法使用标准库。测试需pytest，绘图需matplotlib。实际运行的解释器、系统和源码SHA写在 `source_freeze.json` 中。

```powershell
python -m pytest tests -q
python scripts/analyze_p4_refinement_evidence.py
python scripts/plot_p4_refinement.py
```

官方诊断脚本只读取上一轮的原件，不产生新官方观测。绘图中的覆盖路线图是静态几何；它不能作为新策略已经在官方运行的证据。

## 重新执行完整本地研究

使用一个尚不存在的目录。不要覆盖已完成的结果。

```powershell
python scripts/run_p4_refinement.py prepare --output results/p4_refinement_replay
python scripts/run_p4_refinement.py freeze --round results/p4_refinement_replay
```

prepare的默认种子和分层数量重现本轮世界；不读取任何当局隐藏真值来做动作决策。freeze固定代码、参数与几何构造。运行器要求一个对应冻结散列的本地执行记录，用来避免无意在不同代码版本上接着跑；这不是官方测试授权。

```powershell
@'
import json
from pathlib import Path
p = Path('results/p4_refinement_replay')
r = json.loads((p/'release_template.json').read_text(encoding='utf-8'))
r.update(authorized=True, partitions=['development'], scope='Local reproduction only')
(p/'release_development.json').write_text(json.dumps(r, indent=2), encoding='utf-8')
'@ | python -
python scripts/run_p4_refinement.py run --round results/p4_refinement_replay --partition development --release results/p4_refinement_replay/release_development.json --workers 8
python scripts/run_p4_refinement.py select --round results/p4_refinement_replay
```

若 `selection.json` 显示没有合格候选，应保留这个结论，不强行放行确认。正常选出候选后：

```powershell
@'
import json
from pathlib import Path
p = Path('results/p4_refinement_replay')
s = json.loads((p/'selection.json').read_text(encoding='utf-8'))
assert s['selected_variants'], 'No eligible candidate; do not force a selection'
r = json.loads((p/'release_template.json').read_text(encoding='utf-8'))
r.update(authorized=True, partitions=['confirmation'], selection_sha256=s['selection_sha256'], scope='Local independent confirmation only')
(p/'release_confirmation.json').write_text(json.dumps(r, indent=2), encoding='utf-8')
'@ | python -
python scripts/run_p4_refinement.py run --round results/p4_refinement_replay --partition confirmation --release results/p4_refinement_replay/release_confirmation.json --selection results/p4_refinement_replay/selection.json --workers 8
```

中断后的同分区恢复可加 `--resume`；已完成记录必须通过世界、代码与日志散列检查。它不会静默覆盖不一致的结果。虚拟时间在相同环境与代码下应可复现，现实程序耗时会随机器、并发和负载变化。

## 解释结果

主结果为完整清除前提下的整局总虚拟时间，按三个池分别报告。配对差值为“新策略−current”，负值表示节省。点态bootstrap区间对应预设的本地设计混合，不代表官方总体，也未作多重比较校正。记录最坏退步和退步比例，不能只展示均值。

最后一次成功清除后的尾部仅供事后诊断。不能通过推迟清除最后一个目标来制造尾部改善，不能让策略使用事后N提前结束。光学兜底目标数也不是单独的优化目标；即便仍转入兜底，只要认证覆盖更短、更少且整局时间下降，就可能是有效改进。

新方案的官方迁移效果需要后续新的官方案例；本轮本地结果不能填入官方演练或正式测试成绩。正式测试仍须遵守原有明确授权要求。
