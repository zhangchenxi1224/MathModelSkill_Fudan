"""Validate export integrity and local replay. No HTTP or official test calls."""
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
