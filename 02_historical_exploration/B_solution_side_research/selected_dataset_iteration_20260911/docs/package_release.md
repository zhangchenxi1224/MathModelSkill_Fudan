# 轻量证据交付包

最终结果与独立审计完成后，由主执行者运行：

```powershell
python package_release.py --output '..\selected_dataset_best_20260911.zip'
```

输出 ZIP 与同名 `.zip.sha256` 均必须尚不存在。运行器检查 `RESULTS.md`、`campaign/frozen_selection.json`、`reports/validation_verdict.json`、`reports/independent_audit.json` 存在，并要求 `validation_verdict.json` 的 `passed` 为布尔值 `true`。独立审计必须同时满足 `audit_passed` 为布尔值 `true`、`issue_count` 为整数 `0`；缺字段、失败或尚有问题均拒绝打包。指定实验阶段仍存在 `RUNNING.lock` 或 `INVALID.json` 时拒绝打包。

交付内容包括根目录 Python 文件、README/RESULTS、可选 pytest.ini、methods/vendor 的 Python 源码，以及 configs/campaign/inputs/dataset/docs/tests/reports 内容。runs 仅纳入 round1、round2、round3、full、confirmation、stress 各阶段的 plan/results/summary 与 source/runtime 快照，不带 cases 中的逐场轨迹。自动排除缓存、字节码和 RUNNING/INVALID 目录。符号链接、目录联接和逃逸工程根目录的路径会被拒绝。

包内路径保持相对工程根目录，`solve_local.py` 可继续使用随包的 dataset/runtime。包内 `release_manifest.json` 记录每个有效载荷文件的来源相对路径、字节数和 SHA256；清单不包含自身摘要。写完后重新打开 ZIP，逐文件读取并核对摘要、大小与文件集合，再生成整个 ZIP 的校验文件。终端输出文件数、有效载荷大小、ZIP 大小和 SHA256。

本脚本不上传文件，也不执行求解器或官方接口。fixture 测试只创建临时的模拟目录和小型 ZIP，不读取真实确认/压力成绩；真实打包必须等最终材料齐备后显式执行。
