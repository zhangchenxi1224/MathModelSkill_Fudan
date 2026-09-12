"""Parallel saved-log audit using the unchanged serial per-run auditor.

Global provenance and case pairing are checked once. Per-run results merge in
the original row order, preserving issue ordering and first-tie residual cases.
No policy, simulator session, or official request is executed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import time
import zipfile

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import audit_p4_refinement as serial

SERIAL_AUDITOR_SHA256 = "c0ee23278d622df20ca257ce67ae628eb61e483555ee013c6fd8def759a57bd3"
_CONTEXT = None
SEMANTIC_FIELDS = ("schema_version", "round_root", "partition", "status", "issue_count", "issue_counts",
    "issues", "distinct_worlds", "expected_worlds", "expected_runs", "counts", "worst_residuals", "case_audits",
    "source_freeze_sha256", "manifest_sha256", "input_results_sha256", "auditor_sha256", "scope",
    "timing_policy", "truth_policy", "limitations")


def _initialize_worker(root, partition, cases, manifest, frozen, old_points):
    global _CONTEXT
    _CONTEXT = (Path(root), partition, cases, manifest, frozen, old_points)


def _audit_worker(row):
    root, partition, cases, manifest, frozen, old_points = _CONTEXT
    audit = serial.Auditor()
    try:
        serial.audit_run(audit, root, partition, row, cases[row["case_id"]], manifest, frozen, old_points)
    except Exception as exc:
        audit.check(False, "audit_run_exception", case_id=row.get("case_id"), variant=row.get("variant"),
                    error=f"{type(exc).__name__}: {exc}")
    return {"issues": audit.issues, "counts": dict(audit.counts), "residuals": audit.residuals,
            "case_reports": audit.case_reports}


def merge_run(audit, partial):
    audit.issues.extend(partial["issues"])
    audit.counts.update(partial["counts"])
    audit.case_reports.extend(partial["case_reports"])
    for name, residual in partial["residuals"].items():
        # Strict > matches serial.Auditor.residual, retaining the first run on ties.
        if name not in audit.residuals or residual["absolute_residual"] > audit.residuals[name]["absolute_residual"]:
            audit.residuals[name] = residual


def compare_serial(report, reference):
    mismatches = [key for key in SEMANTIC_FIELDS if report.get(key) != reference.get(key)]
    return {"status": "identical" if not mismatches else "different", "mismatched_fields": mismatches,
            "compared_fields": list(SEMANTIC_FIELDS),
            "semantic_sha256": serial.digest({key: report.get(key) for key in SEMANTIC_FIELDS}),
            "reference_semantic_sha256": serial.digest({key: reference.get(key) for key in SEMANTIC_FIELDS}),
            "excluded_fields": ["audit_utc", "wall_time_s", "parallel_wrapper_metadata", "serial_equivalence"]}


def audit_round_parallel(root, partition="development", *, workers=8, progress_every=40):
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
    if partition not in ("development", "confirmation"):
        raise ValueError("partition must be development or confirmation")
    if serial.sha(serial.__file__) != SERIAL_AUDITOR_SHA256:
        raise ValueError("Serial audit implementation changed; revalidate wrapper equivalence first")
    started = time.monotonic()
    root = Path(root).resolve()
    audit = serial.Auditor()
    manifest, frozen = serial.read(root/"manifest.json"), serial.read(root/"source_freeze.json")
    rows, metadata = serial.read(root/partition/"results.json"), serial.read(root/partition/"run_metadata.json")

    # Kept equivalent to the serial audit_round global checks; no runtime source
    # or archived log is changed, and these checks do not run once per worker.
    for document, key in ((manifest, "manifest_sha256"), (frozen, "source_freeze_sha256")):
        body = dict(document)
        expected = body.pop(key)
        audit.check(serial.digest(body) == expected, key)
    with zipfile.ZipFile(root/"source_snapshot.zip") as archive:
        for name, expected in frozen["source_sha256"].items():
            audit.check(serial.sha(serial.ROOT/name) == expected, "current_source_hash", file=name)
            audit.check(hashlib.sha256(archive.read(name)).hexdigest() == expected, "snapshot_source_hash", file=name)
    cases = {case["case_id"]: case for case in manifest["scenarios"] if case["partition"] == partition}
    arms = metadata["arms"]
    if partition == "development":
        audit.check(arms == manifest["arms"], "development_arm_set")
    else:
        selection = serial.read(root/partition/"selection_used.json")
        body = dict(selection)
        expected = body.pop("selection_sha256")
        audit.check(serial.digest(body) == expected, "selection_hash")
        audit.check(arms == ["current"]+selection["selected_variants"], "confirmation_arm_set")
    release = serial.read(root/partition/"release_used.json")
    audit.check(release.get("authorized") is True and partition in release.get("partitions", []) and
                release.get("manifest_sha256") == manifest["manifest_sha256"] and
                release.get("source_freeze_sha256") == frozen["source_freeze_sha256"], "release_provenance")
    if partition == "confirmation":
        audit.check(release.get("selection_sha256") == selection["selection_sha256"], "release_selection")
    expected = {(identity, arm) for identity in cases for arm in arms}
    observed = [(row["case_id"], row["variant"]) for row in rows]
    audit.check(len(observed) == len(set(observed)) and set(observed) == expected, "case_arm_cardinality",
                expected_runs=len(expected), observed_runs=len(observed))
    audit.check(metadata.get("recorded_runs") == len(rows) and metadata.get("expected_runs") == len(expected), "metadata_run_count")
    old_points = serial.order_route(serial.directional_points("triangular", 950.))
    paired = defaultdict(list)
    with ProcessPoolExecutor(max_workers=workers, initializer=_initialize_worker,
            initargs=(root, partition, cases, manifest, frozen, old_points)) as pool:
        # Executor.map preserves input order while calculations run in parallel.
        # This also preserves the serial failure ordering and worst-case ties.
        results = pool.map(_audit_worker, rows, chunksize=1)
        for index, (row, partial) in enumerate(zip(rows, results)):
            paired[row["case_id"]].append(row)
            merge_run(audit, partial)
            if progress_every and ((index+1) % progress_every == 0 or index+1 == len(rows)):
                print(f"parallel audit {partition}: {index+1}/{len(rows)} runs; issues={len(audit.issues)}",
                      file=sys.stderr, flush=True)
    for identity, group in paired.items():
        audit.check({r["variant"] for r in group} == set(arms), "same_case_arm_set", case_id=identity)
        for key in ("world_sha256", "error_field_sha256", "scenario_sha256", "policy_core_sha256", "source_freeze_sha256"):
            audit.check(len({r.get(key) for r in group}) == 1, "same_case_identity", case_id=identity, field=key)
    return {"schema_version": 1, "audit_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "round_root": str(root), "partition": partition, "status": "passed" if not audit.issues else "issues_found",
        "issue_count": len(audit.issues), "issue_counts": dict(Counter(i["kind"] for i in audit.issues)), "issues": audit.issues,
        "distinct_worlds": len(paired), "expected_worlds": len(cases), "expected_runs": len(expected),
        "counts": dict(audit.counts), "worst_residuals": audit.residuals, "case_audits": audit.case_reports,
        "source_freeze_sha256": frozen["source_freeze_sha256"], "manifest_sha256": manifest["manifest_sha256"],
        "input_results_sha256": serial.sha(root/partition/"results.json"), "auditor_sha256": serial.sha(serial.__file__),
        "wall_time_s": time.monotonic()-started,
        "scope": "Saved local evidence only. No solver execution, no official actions, no file changes except explicit audit output.",
        "timing_policy": "Exact per-action round(distance/5*1e6) integer microseconds plus switch/measure/clear costs; continuous accounting separately allows 0.5 microsecond per move.",
        "truth_policy": "Source positions consumed only by the external audit evaluator for actual clear geometry and hull inclusion; never passed to strategy.",
        "limitations": "Does not rederive the noise field or compare measured direction values against it; verifies fixed field identity, request/response/decision agreement, and geometric envelope containment.",
        "parallel_wrapper_metadata": {"workers": workers, "wrapper_sha256": serial.sha(__file__),
            "per_run_auditor_sha256": SERIAL_AUDITOR_SHA256, "merge_order": "original results row order",
            "global_checks": "once in parent process", "per_run_state": "independent Auditor instance in worker"}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", type=Path, required=True)
    parser.add_argument("--partition", choices=("development", "confirmation"), required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--progress-every", type=int, default=40)
    parser.add_argument("--compare-serial", type=Path, help="Compare all semantic fields to an existing serial audit JSON")
    args = parser.parse_args(argv)
    if args.output and args.output.exists():
        raise FileExistsError("Preserve earlier audit output")
    report = audit_round_parallel(args.round, args.partition, workers=args.workers, progress_every=args.progress_every)
    if args.compare_serial:
        report["serial_equivalence"] = compare_serial(report, serial.read(args.compare_serial))
        report["serial_equivalence"]["reference_path"] = str(args.compare_serial.resolve())
        report["serial_equivalence"]["reference_sha256"] = serial.sha(args.compare_serial)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    brief = {key: report[key] for key in ("status", "issue_count", "distinct_worlds", "expected_runs", "counts", "worst_residuals", "wall_time_s", "parallel_wrapper_metadata")}
    if "serial_equivalence" in report:
        brief["serial_equivalence"] = report["serial_equivalence"]
    print(json.dumps(brief, ensure_ascii=False, indent=2))
    return 0 if not report["issue_count"] and report.get("serial_equivalence", {}).get("status", "identical") == "identical" else 1


if __name__ == "__main__":
    raise SystemExit(main())
