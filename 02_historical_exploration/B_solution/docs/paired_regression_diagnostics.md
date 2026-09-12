# 已选方案的回归解释工具

`scripts/diagnose_paired_regressions.py` 只读实际本地日志，不运行策略，不改冻结源代码。必须先有每题已冻结选择，包含 `selection_basis: "development_only"`、`manifest_sha256`，以及 `selected_variants` 或 `selected_local_limits`（键为`"3"`和`"4"`）。在主代理冻结选择以前只准备脚本和合成单测，不运行真实development结果。

示例：

```powershell
python scripts/diagnose_paired_regressions.py --local results/round1/local --selection results/round1/selected_local_limits.json --output results/round1/local/regression_diagnostics --plots
```

工具硬编码只打开 `local/development/results.json`；不打开 `local/results.json` 或confirmation目录。它为每个pool×题选出已选方案相对原L5的最大有符号惩罚成本差，即便该组全部改进也保留改进最小的局。另保留所有实际development失败，包括未选方案，不把失败案例删掉。若选择L5本身，该题没有非平凡已选配对，但实际失败仍保留。

每对验证场景、误差场及策略核心散列一致，再读取两臂真实 `requests.jsonl.gz` 和 `decisions.jsonl.gz`。只对accepted且唯一的请求计费，按移动每5 m/s、测量5 s、换频道1 s、光学3 s、成功激光2 s复算；移动与原模拟器一样按微秒取整。清除的目标频道从knowledge读取，不误用接收机当前频道。逐动作与总耗时不一致会保留为错误，不能编造解释。

输出逐局JSON含全部动作、组件差、首次动作分歧、no_signal理由、光学兜底尝试与失败、按目的频道归账的动作成本以及源SHA256。失败惩罚调整与实际已耗时分开，能将总惩罚差精确还原；失败短投入不是高效率。按频道分解属于会计归类，不能解释为该频道的因果效应。

最多两张轨迹PNG按已选方案的最大development回归确定。默认仅画实际路径及反馈，图明确标记“事后极值案例”，不附新CI、不声称泛化证据。可选 `--selected-scenes` 接受仅含明确选中、已评估development局的 `case_id -> scene` JSON；场景ID、partition及scenario SHA必须一致，真源星号只作离线解释，绝不进入策略。不直接读取完整manifest中的其他场景。

合成构造测试覆盖全组改进仍保留最差、未选方案失败、确认集拒绝、配对不一致、清除频道歧义、微秒计费与真实LocalSimulator短流程。
