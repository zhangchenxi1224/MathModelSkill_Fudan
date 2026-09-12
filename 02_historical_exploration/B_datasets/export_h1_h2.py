"""Extract the selected existing worlds without regenerating or editing them."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


LOADER = r'''"""Environment/evaluation harness. Do not give scenario truth to a policy."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "runtime"))
from bsolver_frozen.protocol import RobotClient
from bsolver_frozen.simulator import FixedErrorField, LocalSimulator, Source


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def scenario_digest(value):
    return hashlib.sha256(canonical({k: v for k, v in value.items()
                                    if k != "scenario_sha256"}).encode("utf-8")).hexdigest()


def list_cases(*, hypothesis=None, problem=None):
    """Case selection belongs to the evaluation harness, before a run."""
    if hypothesis is not None and hypothesis not in ("H1", "H2"):
        raise ValueError("hypothesis must be H1 or H2")
    if problem is not None and problem not in (3, 4):
        raise ValueError("problem must be 3 or 4")
    rows = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))
    return [r for r in rows if (hypothesis is None or r["hypothesis"] == hypothesis)
            and (problem is None or r["problem"] == problem)]


def load_scenario(case_id):
    """Evaluator-only hidden state; never pass this dictionary to the policy."""
    index = {r["case_id"]: r for r in list_cases()}
    if case_id not in index:
        raise KeyError(case_id)
    value = json.loads((ROOT / index[case_id]["file"]).read_text(encoding="utf-8"))
    if scenario_digest(value) != value["scenario_sha256"]:
        raise ValueError(f"Scenario hash mismatch: {case_id}")
    return value


def make_local_session(case_id, *, log_path=None, max_real_duration_s=1200.0):
    """Return (client, evaluator). Give only client and known problem to policy.

    This is a logical access boundary, not a security sandbox. Evaluators own
    this module and scenario files; policies must not inspect the private
    transport, truth, case seeds, error-field identity, or evaluator summary.
    """
    case = load_scenario(case_id)
    specification = case["error_field"]
    error = FixedErrorField(specification["seed"], specification["mode"],
                            specification["correlation_length_m"])
    environment = LocalSimulator(
        [Source(**source) for source in case["sources"]],
        robot_id="local-team", error_field=error, enforce_case_size=True,
        max_real_duration_s=max_real_duration_s,
        window_duration_s=max_real_duration_s + 300.0,
        max_virtual_duration_s=360000.0)
    client = RobotClient(robot_id="local-team", transport=environment, log_path=log_path)
    return client, environment
'''


DEMO = r'''"""Offline API demo, deliberately NOT a solver or full-clear benchmark."""
import argparse
import json
from dataset import list_cases, make_local_session


def observable_demo(client, problem):
    # This function receives no source count, positions, error seed, or truth.
    results = []
    for channel in range(1, 21):
        response = client.measure(channel, (0.0, 0.0))
        result = {"channel": channel, "measure_result": response["measure_result"]}
        if response["measure_result"] == "direction":
            result["svd_deg"] = response["svd_deg"]
        results.append(result)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hypothesis", choices=["H1", "H2"], default="H1")
    parser.add_argument("--problem", type=int, choices=[3, 4], default=4)
    parser.add_argument("--case-id")
    parser.add_argument("--log", help="Optional new JSONL path outside this dataset")
    args = parser.parse_args()
    candidates = list_cases(hypothesis=args.hypothesis, problem=args.problem)
    row = next((r for r in candidates if r["case_id"] == args.case_id), None) if args.case_id else candidates[0]
    if row is None:
        parser.error("case-id is not in the requested group")
    client, evaluator = make_local_session(row["case_id"], log_path=args.log)
    try:
        client.enter()
        feedback = observable_demo(client, row["problem"])
        client.exit()
        print(json.dumps({"purpose": "offline_api_demo_not_solver_benchmark",
                          "case_id": row["case_id"], "feedback": feedback,
                          "evaluation_only": evaluator.summary()}, ensure_ascii=False, indent=2))
    finally:
        client.close_log()


if __name__ == "__main__":
    main()
'''


VERIFY = r'''"""Validate export integrity and local replay. No HTTP or official test calls."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import time
from dataset import ROOT, canonical, list_cases, load_scenario, make_local_session
from bsolver_frozen.simulator import FixedErrorField, Source


def check(condition, message):
    if not condition:
        raise ValueError(message)


def verify(*, skip_package_check=False):
    started = time.monotonic()
    checked_files = 0
    if not skip_package_check:
        sums = ROOT / "SHA256SUMS.txt"
        check(sums.is_file(), "Missing SHA256SUMS.txt")
        for line in sums.read_text(encoding="utf-8").splitlines():
            expected, relative = line.split("  ", 1)
            p = (ROOT / relative).resolve()
            check(p.is_relative_to(ROOT.resolve()), "Invalid checksum path")
            check(hashlib.sha256(p.read_bytes()).hexdigest() == expected, f"File changed: {relative}")
            checked_files += 1
    rows = list_cases()
    check(len(rows) == 360, "Expected 360 cases")
    ids = [r["case_id"] for r in rows]
    check(len(set(ids)) == 360, "Duplicate case ID")
    expected_counts = {(h, p): 90 for h in ("H1", "H2") for p in (3, 4)}
    check(Counter((r["hypothesis"], r["problem"]) for r in rows) == expected_counts, "Wrong group sizes")
    lines = [json.loads(line) for line in (ROOT / "scenarios.jsonl").read_text(encoding="utf-8").splitlines()]
    check(len(lines) == 360, "Wrong JSONL length")
    check([c["case_id"] for c in lines] == ids, "JSONL order differs from index")
    world_hashes, scenario_hashes = set(), set()
    for row, flat in zip(rows, lines):
        case = load_scenario(row["case_id"])
        check(case == flat, "Per-case JSON differs from JSONL")
        check(row["scenario_sha256"] == case["scenario_sha256"], "Index hash mismatch")
        check(case["pool"] == "calibrated" and case["composition_model"] == "smoothed_joint", "Wrong selection")
        check(case["error_mode"] == {"H1": "correlated", "H2": "extreme"}[row["hypothesis"]], "Wrong hypothesis")
        check(case["error_field"]["mode"] == case["error_mode"], "Error mode mismatch")
        check(case["error_field"]["definition"] == "FixedErrorField-v1", "Unknown error field version")
        sources = [Source(**s) for s in case["sources"]]
        check(10 <= len(sources) <= 16 and len(sources) == case["n"], "Invalid source count")
        check(len({s.channel for s in sources}) == len(sources), "Duplicate source channels")
        ndir = sum(s.direction_deg is not None for s in sources)
        check(ndir == case["n_directed"], "Type count mismatch")
        check(case["problem"] == 4 or ndir == 0, "P3 must be omnidirectional")
        spec = case["error_field"]
        field = FixedErrorField(spec["seed"], spec["mode"], spec["correlation_length_m"])
        for channel in (1, 7, 20):
            for p in ((0., 0.), (100., -17.), (-1800., 1800.), (23.4, 50.1)):
                error = field(channel, p)
                check(-1 <= error <= 1 and error == field(channel, p), "Non-fixed or unbounded error")
        world_hashes.add(hashlib.sha256(canonical({"sources": case["sources"], "error_field": spec}).encode("utf-8")).hexdigest())
        scenario_hashes.add(case["scenario_sha256"])
        # Evaluator-only known-truth probes validate environment wiring, NOT a policy.
        source = sources[0]
        angle = math.radians(source.direction_deg or 0.0)
        probe = (source.position[0] + 100 * math.cos(angle), source.position[1] + 100 * math.sin(angle))
        client, evaluator = make_local_session(case["case_id"])
        try:
            check(client.enter()["accepted"], "enter rejected")
            first = client.measure(source.channel, probe)
            repeat = client.measure(source.channel, probe)
            check(first["measure_result"] == repeat["measure_result"] == "direction", "Expected direction")
            check(first["svd_deg"] == repeat["svd_deg"], "Same-location bearing changed")
            near = client.measure(source.channel, source.position)
            check(near["measure_result"] == "near", "Expected near")
            other_channel = source.channel % 20 + 1
            client.measure(other_channel, (0., 0.))
            check(client.clear(source.channel, source.position)["accepted"], "clear rejected")
            check(client.current_channel == other_channel, "clear changed channel")
            check(client.position == source.position, "clear did not move")
            check(evaluator.summary()["cleared_count"] == 1, "Local clear failed")
            check(client.measure(source.channel, source.position)["measure_result"] == "no_signal", "Cleared source emits")
            client.exit()
        finally:
            client.close_log()
    check(len(world_hashes) == len(scenario_hashes) == 360, "Duplicate world or hash")
    for group_path in (ROOT / "groups").glob("*.json"):
        group = json.loads(group_path.read_text(encoding="utf-8"))
        expected = [r["case_id"] for r in rows if r["hypothesis"] == group["hypothesis"] and
                    (group["problem"] is None or r["problem"] == group["problem"])]
        check(group["case_ids"] == expected, "Group membership mismatch")
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["runtime_files_sha256"].items():
        check(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected, "Frozen runtime mismatch")
    return {"status": "passed", "scenario_count": 360, "unique_hidden_worlds": 360,
            "group_counts": {f"{h}_P{p}": n for (h, p), n in expected_counts.items()},
            "scenario_hash_checks": 360, "local_replay_smoke_cases": 360,
            "local_replay_purpose": "evaluator-only interface checks; not a solving benchmark",
            "official_requests": 0, "package_files_verified": checked_files,
            "package_check": "deferred_until_sealing" if skip_package_check else "passed",
            "elapsed_real_s": time.monotonic() - started}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-package-check", action="store_true", help="Only for initial export before sealing")
    args = parser.parse_args()
    print(json.dumps(verify(skip_package_check=args.skip_package_check), ensure_ascii=False, indent=2))
'''


README = '''# B题：参考官方数量组成的 H1 / H2 合成场景集（360局）

这是从已完成本地实验中原样提取的场景，可交给下一轮探索性方案作开发、调参和配对比较。不是官方原始案例，也不是新采集的独立测试集。

## 本次保留的范围

筛选条件：`pool == calibrated` 且 `composition_model == smoothed_joint`，仅保留如下两种误差模型。

|分组|数量组成|固定误差场|问题3|问题4|合计|
|---|---|---|---:|---:|---:|
|H1 / 假设1|参考官方数据的平滑数量／类型联合分布|correlated：平滑空间相关误差，尺度150m|90|90|180|
|H2 / 假设2|同一数量／类型组成模型|extreme：150m网格内固定为±1°的误差|90|90|180|
|合计|||180|180|360|

来源：第一阶段200局、第二阶段80局、第三阶段80局。排除较宽的组成模型、宽合法池和压力池；H2虽然使用极端误差，本来就属于所选校准子集，按用户指定保留。

## 与官方数据的关系

原始校准来源记录为160个官方案例，其中120个整局分入拟合部分。本包继承其 `smoothed_joint` 数量／类型模型。这里的360局都是随后生成的本地隐藏环境，不是那160个官方案例的真值重建。

两种误差机制均保留原标记 `mechanism_status: unresolved`。位置生成、半径生成、朝向生成和具体误差函数仍包含未确定假设；不能声称已经恢复官方生成器。两组各占一半是设计比例，不是官方误差机制的发生概率。

主调参和候选排序可使用本集，但应分别报告 H1/P3、H2/P3、H1/P4、H2/P4。混合均值只能称为本数据集的均值，不能称为官方期望成绩。最终迁移效果仍需新的官方案例验证；本导出不调用官方接口。

## 文件与读取

- `scenarios.jsonl`：360行，每行一个原始场景，保留原始 `scenario_sha256`。
- `cases/*.json`：同样360个场景，逐案例文件，便于指定复现。
- `index.json`：案例列表、问题、H1/H2分组、原阶段和原分区、来源索引。
- `groups/*.json`：H1/H2及问题3/4的现成案例ID列表。
- `manifest.json`、`provenance/`：筛选规则、计数、原清单哈希、校准来源、运行时哈希。
- `runtime/bsolver_frozen/`：原版本地模拟器及协议客户端的字节一致快照，使用独立模块名。
- `dataset.py`：加载器，构造同一隐藏场景和固定误差场。
- `example_local.py`：只测原点20频道的离线接入示例，不是完整求解算法。
- `verify_dataset.py`：哈希、360场景合法性、固定误差和本地接口接入检查。
- `verification.json`：封装前实际执行的数据/接入验证结果；`SHA256SUMS.txt`校验所有交付内容。
- `export_h1_h2.py`：提取脚本；仅在有原工程时用于重新提取。

`scenario_sha256`的算法：移除这个字段，将其余对象按键排序，以UTF-8、无多余空白、`ensure_ascii=False`序列化，再计算SHA256。整文件哈希另见 `SHA256SUMS.txt`。

Python 3.10+，仅标准库，无需GPU或安装第三方依赖。解压后在目录内运行：

```powershell
python -B verify_dataset.py
python -B example_local.py --hypothesis H1 --problem 4
python -B example_local.py --hypothesis H2 --problem 3
```

本机也可将 `python` 替换为 `& 'D:\\st_python\\python.exe' -X utf8`。

集成自己的方案时，由评估入口完成以下操作：

```python
from dataset import list_cases, make_local_session

row = list_cases(hypothesis="H1", problem=4)[0]
client, evaluator = make_local_session(row["case_id"])
try:
    client.enter()
    my_policy(client, row["problem"])  # 替换为真实策略，只接收公开信息
    if not client.exited:
        client.exit()
    result = evaluator.summary()     # 仅用于当局决策结束后的评分
finally:
    client.close_log()
```

策略可以调用 `client.measure(channel, (x,y))`、`client.clear(channel, (x,y))`，读取真实返回和本地公开状态。没有独立移动接口。只有 `measure_result == direction` 时读取 `svd_deg`。客户端在本例中始终连接本地模拟器，不使用用户名、密码或官方服务。

此加载器提供逻辑上的策略/环境分工，不是阻止恶意读取的安全沙箱。案例JSON内的坐标、半径、朝向、数量、误差种子只供环境与评估器读取；不能把它们、H1/H2标签或案例ID作为当局策略的隐含真值输入。

## 下一轮比较规则

1. 各策略使用同一批原始 `case_id`，通过加载器恢复完全相同的目标与固定误差场。不要重新抽噪声或修改场景来使某个策略获益。
2. 先比较完整清除率和退出正确性；在保证完成的前提下比较总虚拟耗时。失败局必须单列，不能把失败前的短耗时作为完成耗时，也不能只保留成功局而隐去失败。
3. 每个 H/P 组分别统计配对耗时差、移动距离、检测次数、换频道次数、清除尝试/失败次数、现实运行时间；记录策略版本、参数、原场景哈希与逐请求日志。
4. 总虚拟耗时核对 `L_walk/5 + N_switch + 5*N_measure + 3*N_clear_attempt + 2*N_clear_success`。本地环境逐次移动以微秒记账，累计可能存在极小舍入差。题设平均清除耗时另报，不替代整局目标。
5. 保留所有失败和退步案例。选出少量候选后，用未参与开发的新官方案例验证迁移。

原场景中的 `partition` 不变，仅为历史溯源：288个development、72个confirmation。这些案例已进入上一轮研究，整个导出集在下一轮均为开发/回归数据；旧confirmation标签不能当作新的独立验收集。`index.json`显式提供 `next_round_role: development_regression`。

新结果和日志放在独立输出目录，不覆盖本包。该包不包含旧策略的评分表、官方原始日志或官方目标真值；下一轮比较需在这些场景上实际重跑基线和新策略。

## 已执行的导出验证

原清单的嵌入哈希、360个原场景哈希、组别数量、无重复隐藏环境、目标合法性、JSONL/逐案例一致性、运行时快照一致性均由脚本核对。另对360局执行本地接口冒烟检查：固定测点复测、near、清除及频道状态。

冒烟检查由评估器使用已知真值构造探针，仅验证加载和环境接线，不是策略实验，不计为360局完整求解成功。没有新增官方请求，也没有修改原工程数据。
'''


HANDOFF = '''# 给探索性方案的交接说明

请先阅读 README.md 并运行 `python -B verify_dataset.py`。

本目录是已经筛选好的360个现有场景，只含参考官方数量／类型组成的平滑联合模型：H1为空间相关误差，H2为网格内固定±1°误差；各180局，每组问题3/4各90局。它不是官方原始数据，误差机制仍未确定。

将本集用于下一轮开发、调参及同场景配对比较。分别报告 H1/P3、H2/P3、H1/P4、H2/P4；不要将人为混合均值解释为官方期望。既有partition只作历史记录，全部案例均已属于开发过程。

用 dataset.py 构造环境，策略仅获取客户端和问题编号。不能读取场景真值、误差种子、H1/H2标签或隐藏环境。保留完整覆盖、几何误差界和清除保证，不通过遗漏困难目标降低耗时。

对相同case_id重新运行冻结基线和探索方案。记录完整清除、总虚拟耗时、移动、检测、换频道、失败清除、现实耗时、代码与参数版本、scenario_sha256和原始动作日志。失败单列且保留。结果另存，不修改此数据集。最终官方迁移验证需使用新的官方案例。
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source, output = args.source_root.resolve(), args.output.resolve()
    archive = output.with_suffix(".zip")
    if output.exists() or archive.exists():
        raise FileExistsError("Refusing to overwrite an existing export or archive")
    records, provenance, originals = [], [], {}
    for stage, folder in [(1, "local"), (2, "stage2"), (3, "stage3")]:
        relative = f"results/round1/{folder}/manifest.json"
        path = source / relative
        value = json.loads(path.read_text(encoding="utf-8"))
        embedded = value["manifest_sha256"]
        if digest({k: v for k, v in value.items() if k != "manifest_sha256"}) != embedded:
            raise ValueError(f"Source manifest digest mismatch: {relative}")
        originals[relative] = file_hash(path)
        selected = [(i, c) for i, c in enumerate(value["scenarios"])
                    if c["pool"] == "calibrated" and c["composition_model"] == "smoothed_joint"
                    and c["error_mode"] in ("correlated", "extreme")]
        header = {k: v for k, v in value.items() if k != "scenarios"}
        provenance.append({"source_stage": stage, "source_relative_path": relative,
                           "source_file_sha256": originals[relative], "source_manifest_sha256": embedded,
                           "original_scenario_count": len(value["scenarios"]),
                           "selected_scenario_count": len(selected),
                           "original_header": header,
                           "note": "Header copy omits scenarios; original manifest digest is a provenance identifier, not the digest of this header."})
        for original_index, case in selected:
            if digest({k: v for k, v in case.items() if k != "scenario_sha256"}) != case["scenario_sha256"]:
                raise ValueError(f"Original scenario digest mismatch: {case['case_id']}")
            records.append((stage, relative, original_index, case))
    if len(records) != 360:
        raise ValueError(f"Expected 360 selected cases, got {len(records)}")
    output.mkdir(parents=True)
    rows = []
    for stage, relative, original_index, case in records:
        case_id = case["case_id"]
        if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in case_id):
            raise ValueError("Unsafe case filename")
        filename = f"cases/{case_id}.json"
        write_json(output / filename, case)
        rows.append({"case_id": case_id, "file": filename, "problem": case["problem"],
                     "hypothesis": "H1" if case["error_mode"] == "correlated" else "H2",
                     "error_mode": case["error_mode"], "composition_model": case["composition_model"],
                     "scenario_sha256": case["scenario_sha256"], "source_stage": stage,
                     "source_manifest": relative, "source_scenario_index_zero_based": original_index,
                     "historical_partition": case["partition"], "next_round_role": "development_regression"})
    write_json(output / "index.json", rows)
    (output / "scenarios.jsonl").write_text("".join(canonical(case) + "\n" for _, _, _, case in records), encoding="utf-8")
    for h in ("H1", "H2"):
        for p in (None, 3, 4):
            label = h if p is None else f"{h}_P{p}"
            write_json(output / "groups" / f"{label}.json", {
                "hypothesis": h, "problem": p,
                "case_ids": [r["case_id"] for r in rows if r["hypothesis"] == h and (p is None or r["problem"] == p)]})
    for entry in provenance:
        write_json(output / "provenance" / f"stage{entry['source_stage']}_manifest_header.json", entry)
    runtime_hashes = {}
    for filename in ("__init__.py", "protocol.py", "simulator.py"):
        original_relative = f"src/bsolver/{filename}"
        original = source / original_relative
        relative = f"runtime/bsolver_frozen/{filename}"
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        originals[original_relative] = file_hash(original)
        runtime_hashes[relative] = file_hash(target)
        if originals[original_relative] != runtime_hashes[relative]:
            raise ValueError("Runtime snapshot mismatch")
    created = datetime.now(timezone.utc).isoformat()
    manifest = {"schema_version": 1, "dataset_id": "B_H1_H2_official_composition_360_v1",
                "created_at_utc": created, "kind": "existing_synthetic_scenario_subset",
                "next_round_role": "development_regression", "scenario_count": 360,
                "filter": {"pool": "calibrated", "composition_model": "smoothed_joint",
                           "error_mode": ["correlated", "extreme"]},
                "hypotheses": {"H1": {"error_mode": "correlated", "n": 180}, "H2": {"error_mode": "extreme", "n": 180}},
                "group_counts": dict(Counter(f"{r['hypothesis']}_P{r['problem']}" for r in rows)),
                "stage_counts": dict(Counter(str(r["source_stage"]) for r in rows)),
                "historical_partition_counts": dict(Counter(r["historical_partition"] for r in rows)),
                "original_source_root": str(source), "source_files_sha256": originals,
                "runtime_files_sha256": runtime_hashes,
                "mechanism_status": "unresolved", "official_requests_during_export": 0,
                "source_scenario_objects_preserved": True, "generator_reexecuted": False,
                "not_official_raw_cases": True, "not_fresh_holdout": True,
                "calibration_provenance": provenance[0]["original_header"]["calibration_provenance"],
                "calibration_model_id": provenance[0]["original_header"]["calibration_model_id"],
                "interpretation": "Only count/type composition is informed by official observations; hidden geometry and both fixed error fields remain synthetic. Group mixing weights are experimental, not official probabilities."}
    write_json(output / "manifest.json", manifest)
    for name, content in {"dataset.py": LOADER, "example_local.py": DEMO, "verify_dataset.py": VERIFY,
                          "README.md": README, "HANDOFF.md": HANDOFF}.items():
        (output / name).write_text(content, encoding="utf-8")
    shutil.copyfile(Path(__file__).resolve(), output / "export_h1_h2.py")
    command = [sys.executable, "-B", "-X", "utf8", str(output / "verify_dataset.py")]
    verification = subprocess.run(command + ["--skip-package-check"], cwd=output, text=True,
                                  encoding="utf-8", capture_output=True, check=True)
    write_json(output / "verification.json", json.loads(verification.stdout))
    for relative, expected in originals.items():
        if file_hash(source / relative) != expected:
            raise ValueError(f"Source changed during export: {relative}")
    sums = "".join(f"{file_hash(p)}  {p.relative_to(output).as_posix()}\n"
                   for p in sorted(output.rglob("*")) if p.is_file())
    (output / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")
    sealed = subprocess.run(command, cwd=output, text=True, encoding="utf-8", capture_output=True, check=True)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as handle:
        for p in sorted(output.rglob("*")):
            if p.is_file():
                handle.write(p, arcname=f"{output.name}/{p.relative_to(output).as_posix()}")
    archive_files = 0
    with zipfile.ZipFile(archive) as handle:
        if handle.testzip() is not None:
            raise ValueError("Archive CRC error")
        for item in handle.infolist():
            relative = Path(item.filename).relative_to(output.name)
            if hashlib.sha256(handle.read(item)).hexdigest() != file_hash(output / relative):
                raise ValueError(f"Archive content mismatch: {relative}")
            archive_files += 1
    receipt = {"created_at_utc": created, "output_directory": str(output), "archive": str(archive),
               "archive_sha256": file_hash(archive), "archive_bytes": archive.stat().st_size,
               "archive_files_verified": archive_files, "source_files_unchanged": True,
               "sealed_package_verification": json.loads(sealed.stdout)}
    write_json(output.parent / f"{output.name}_receipt.json", receipt)
    (output.parent / f"{archive.name}.sha256").write_text(f"{receipt['archive_sha256']}  {archive.name}\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
