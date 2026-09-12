"""Read-only audit of saved local P4 refinement runs; never executes a policy.

Checks frozen files/archive, same-world arms, unique accepted requests, exact
quantized action timing, clear geometry, action/decision correspondence, actual
coverage ledgers/certificates, and evaluator-only truth-hull containment.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import datetime as dt
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
from bsolver.coverage import directional_points, order_route
from bsolver.geometry import contains


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def world_signature(case):
    return digest({key: case[key] for key in ("problem", "sources", "error_field")})


class Auditor:
    def __init__(self):
        self.issues = []
        self.counts = Counter()
        self.residuals = {}
        self.case_reports = []

    def check(self, condition, kind, **details):
        if not condition:
            self.issues.append({"kind": kind, **details})
        return bool(condition)

    def residual(self, kind, value, limit, **details):
        value = abs(value)
        if kind not in self.residuals or value > self.residuals[kind]["absolute_residual"]:
            self.residuals[kind] = {"absolute_residual": value, "tolerance": limit, **details}
        self.check(math.isfinite(value) and value <= limit, kind, absolute_residual=value, tolerance=limit, **details)


def audit_run(audit, root, partition, row, case, manifest, frozen, old_points):
    before_issues = len(audit.issues)
    tag = {"case_id": row["case_id"], "variant": row["variant"]}
    check = lambda condition, kind, **detail: audit.check(condition, kind, **tag, **detail)
    residual = lambda kind, value, limit, **detail: audit.residual(kind, value, limit, **tag, **detail)
    folder = root/partition/"runs"/row["pool"]/row["case_id"]/row["variant"]
    check(read(folder/"result.json") == row, "result_aggregate_mismatch")
    check(row["manifest_sha256"] == manifest["manifest_sha256"] and
          row["source_freeze_sha256"] == frozen["source_freeze_sha256"], "run_provenance")
    body = dict(case)
    body.pop("scenario_sha256", None)
    check(digest(body) == case["scenario_sha256"] == row["scenario_sha256"], "scenario_hash")
    check(digest(case["error_field"]) == row["error_field_sha256"], "error_field_hash")
    check(world_signature(case) == row["world_sha256"], "world_hash")
    check(row["partition"] == partition and row["problem"] == case["problem"] == 4, "partition_problem")
    check(read(root/"scenarios"/(case["case_id"]+".json")) == case, "scenario_copy")

    expected_logs = {"requests.jsonl.gz", "decisions.jsonl.gz"}
    check(set(row.get("artifact_sha256", {})) == expected_logs, "required_logs")
    logs = {}
    for name in sorted(expected_logs):
        check(sha(folder/name) == row.get("artifact_sha256", {}).get(name), "log_hash", artifact=name)
        with gzip.open(folder/name, "rt", encoding="utf-8") as stream:
            logs[name] = [json.loads(line) for line in stream]
    requests, events = logs["requests.jsonl.gz"], logs["decisions.jsonl.gz"]
    complete_claim = row.get("evaluation_complete") is True
    if complete_claim:
        check(bool(requests) and requests[0]["path"] == "/enter" and requests[-1]["path"] == "/exit", "complete_session_endpoints")
    sources = {s["channel"]: s for s in case["sources"]}
    success_channels, seen_requests = set(), set()
    position, channel, virtual_us = (0., 0.), 1, 0
    counters, walk, actions = Counter(), 0., []
    for index, entry in enumerate(requests):
        payload, response = entry["request"], entry.get("response", {})
        state_before, state_after = entry["state_before"], entry.get("state_after")
        path = entry["path"]
        # This local harness has no injected losses. Unexpected duplicates or
        # rejections require review instead of being silently collapsed.
        check(payload["request_id"] not in seen_requests, "duplicate_request_id", request_index=index)
        seen_requests.add(payload["request_id"])
        if not check(response.get("accepted") is True and state_after is not None,
                     "nonaccepted_or_unconfirmed_request", request_index=index):
            continue
        check(path in ("/enter", "/measure", "/clear", "/exit"), "unknown_action", request_index=index)
        check((state_before["position"]["x"], state_before["position"]["y"]) == position and
              state_before["current_channel"] == channel and
              set(state_before["cleared_channels"]) == success_channels, "state_before", request_index=index)
        residual("state_before_time_s", state_before["virtual_time_s"]-virtual_us/1e6, 1e-8, request_index=index)
        if path in ("/measure", "/clear"):
            target = payload["position"]["x"], payload["position"]["y"]
            distance = math.dist(position, target)
            walk += distance
            virtual_us += round(distance/5*1_000_000)
            position = target
            if path == "/measure":
                switch = int(channel != payload["channel"])
                virtual_us += (5+switch)*1_000_000
                counters["measures"] += 1
                counters["switches"] += switch
                channel = payload["channel"]
            else:
                source = sources.get(payload["channel"])
                success = (source is not None and payload["channel"] not in success_channels
                           and math.dist(target, source["position"]) <= 20.)
                check(success == (response["clear_result"] == "success"), "clear_geometry", request_index=index)
                counters["clear_attempts"] += 1
                counters["clear_successes"] += int(success)
                counters["failed_clear_attempts"] += int(not success)
                virtual_us += (5 if success else 3)*1_000_000
                if success:
                    success_channels.add(payload["channel"])
            actions.append(entry)
        residual("response_time_s", response["virtual_time_s"]-virtual_us/1e6, 1e-8, request_index=index)
        residual("state_after_time_s", state_after["virtual_time_s"]-virtual_us/1e6, 1e-8, request_index=index)
        check((state_after["position"]["x"], state_after["position"]["y"]) == position and
              state_after["current_channel"] == channel and
              set(state_after["cleared_channels"]) == success_channels, "state_after", request_index=index)
    for key in ("measures", "switches", "clear_attempts", "clear_successes", "failed_clear_attempts"):
        check(row[key] == counters[key], "counter_mismatch", field=key)
    residual("walk_distance_m", row["walk_distance_m"]-walk, 1e-8)
    check(row["failed_clear_cost_s"] == 3*counters["failed_clear_attempts"], "failed_clear_cost")
    residual("result_time_s", row["total_virtual_time_s"]-virtual_us/1e6, 1e-8)
    # Unrounded analytical accounting has an explicitly separate tolerance.
    continuous = walk/5+counters["switches"]+5*counters["measures"]+3*counters["clear_attempts"]+2*counters["clear_successes"]
    residual("continuous_accounting_s", row["total_virtual_time_s"]-continuous, .5e-6*len(actions)+1e-8)
    if complete_claim:
        check(len(success_channels) == case["n"] and row["status"] == "complete" and not row.get("error"), "claimed_full_clear")
    check(row["failure_penalized_time_s"] == (row["total_virtual_time_s"] if complete_claim else 360000.), "failure_penalty")
    audit.counts.update(counters)
    audit.counts["requests"] += len(requests)
    audit.counts["runs"] += 1
    audit.counts["execution_complete"] += int(complete_claim)
    audit.counts["execution_failed_or_unverified"] += int(not complete_claim)

    new_coverage = row["variant"] in ("coverage", "combined_cover")
    point_count, hull_count = None, 0
    if events:
        check(events[0]["event"] == "start", "decision_start")
        if complete_claim:
            check(events[-1]["event"] == "finish", "decision_finish")
        points = events[0].get("coverage_points", [])
        expected = frozen["refinement"]["refined_route"] if new_coverage else old_points
        point_count = len(points)
        check(canonical(points) == canonical(expected) and point_count == (25 if new_coverage else 31), "actual_coverage_route")
        check(row["coverage_points_sha256"] == digest(points) and row["coverage_point_count"] == point_count, "coverage_route_hash")
        lookup = {tuple(point): index for index, point in enumerate(points)}
        coverage = defaultdict(set)
        action_index, last_success_time = 0, None
        for sequence, event in enumerate(events):
            check(event["sequence"] == sequence, "decision_sequence", event_sequence=sequence)
            if event["event"] not in ("measure", "clear"):
                continue
            if not check(action_index < len(actions), "extra_decision_action", event_sequence=sequence):
                continue
            action = actions[action_index]
            action_index += 1
            payload = action["request"]
            submitted = payload["position"]["x"], payload["position"]["y"]
            check(action["path"] == "/"+event["event"] and tuple(event["position"]) == submitted and
                  event["response"] == action["response"], "decision_action_correspondence", event_sequence=sequence)
            if event["event"] == "measure" and event.get("reason") == "global_coverage":
                check(submitted in lookup, "nonstation_global_measure", event_sequence=sequence)
                coverage[payload["channel"]].add(lookup.get(submitted, -1))
            knowledge = event.get("knowledge")
            check(knowledge is not None and knowledge["channel"] == payload["channel"] and
                  set(knowledge["coverage_indices"]) == coverage[payload["channel"]], "coverage_ledger", event_sequence=sequence)
            if knowledge and knowledge["channel"] in sources:
                hull_count += 1
                check(contains(knowledge["hull"], sources[knowledge["channel"]]["position"], tol=1e-4),
                      "truth_hull_containment", event_sequence=sequence)
            if event["event"] == "clear" and event["response"]["clear_result"] == "success":
                last_success_time = event["virtual_time_s"]
        audit.counts["hull_snapshots"] += hull_count
        check(action_index == len(actions), "unmatched_request_action")
        stop = row.get("stop_evidence")
        if stop:
            audit.counts["stop_"+stop["type"]] += 1
            if stop["type"] == "per_channel_coverage":
                check(stop["required_point_count"] == point_count and canonical(stop["points"]) == canonical(points), "stop_coverage_route")
                for absent in stop["absent_channels"]:
                    check(coverage[absent] == set(range(point_count)) == set(stop["checked_indices"][str(absent)]),
                          "stop_coverage_indices", absent_channel=absent)
                check(stop["construction"] == ("refined_directional_triangular" if new_coverage else "triangular"), "stop_construction")
                if new_coverage:
                    check(stop["refined_coverage_certificate"] == frozen["refinement"]["coverage_certificate"] and
                          stop["coverage_points_sha256"] == digest(points), "stop_refined_certificate")
            else:
                check(stop["type"] == "known_upper_bound" and len(success_channels) == 16, "known_upper_bound_stop")
        elif complete_claim:
            check(False, "missing_completion_evidence")
        if new_coverage:
            check(events[0].get("coverage_variant") == "refined_directional_triangular" and
                  events[0].get("refined_coverage_certificate") == frozen["refinement"]["coverage_certificate"], "start_refined_certificate")
            if events[-1]["event"] == "finish":
                check(events[-1].get("coverage_variant") == "refined_directional_triangular", "finish_coverage_variant")
        if len(success_channels) == case["n"]:
            check(row["last_source_clear_time_s"] == last_success_time, "last_clear_time")
            if last_success_time is not None:
                residual("stop_tail_s", row["post_clear_stop_tail_s"]-(row["total_virtual_time_s"]-last_success_time), 1e-8)
        else:
            check(row["post_clear_stop_tail_s"] is None, "incomplete_stop_tail_must_be_unknown")
    elif complete_claim:
        check(False, "missing_decisions")
    audit.case_reports.append({**tag, "issues": len(audit.issues)-before_issues, "execution_complete": complete_claim,
        "requests": len(requests), "coverage_points": point_count, "hull_snapshots": hull_count,
        "clear_successes": counters["clear_successes"], "failed_clear_attempts": counters["failed_clear_attempts"]})


def audit_round(root, partition="development", progress_every=20):
    started = time.monotonic()
    root = Path(root).resolve()
    a = Auditor()
    manifest, frozen = read(root/"manifest.json"), read(root/"source_freeze.json")
    rows, metadata = read(root/partition/"results.json"), read(root/partition/"run_metadata.json")
    for document, key in ((manifest, "manifest_sha256"), (frozen, "source_freeze_sha256")):
        body = dict(document)
        expected = body.pop(key)
        a.check(digest(body) == expected, key)
    with zipfile.ZipFile(root/"source_snapshot.zip") as archive:
        for name, expected in frozen["source_sha256"].items():
            a.check(sha(ROOT/name) == expected, "current_source_hash", file=name)
            a.check(hashlib.sha256(archive.read(name)).hexdigest() == expected, "snapshot_source_hash", file=name)
    cases = {case["case_id"]: case for case in manifest["scenarios"] if case["partition"] == partition}
    arms = metadata["arms"]
    if partition == "development":
        a.check(arms == manifest["arms"], "development_arm_set")
    else:
        selection = read(root/partition/"selection_used.json")
        body = dict(selection)
        expected = body.pop("selection_sha256")
        a.check(digest(body) == expected, "selection_hash")
        a.check(arms == ["current"]+selection["selected_variants"], "confirmation_arm_set")
    release = read(root/partition/"release_used.json")
    a.check(release.get("authorized") is True and partition in release.get("partitions", []) and
            release.get("manifest_sha256") == manifest["manifest_sha256"] and
            release.get("source_freeze_sha256") == frozen["source_freeze_sha256"], "release_provenance")
    if partition == "confirmation":
        a.check(release.get("selection_sha256") == selection["selection_sha256"], "release_selection")
    expected = {(identity, arm) for identity in cases for arm in arms}
    observed = [(row["case_id"], row["variant"]) for row in rows]
    a.check(len(observed) == len(set(observed)) and set(observed) == expected, "case_arm_cardinality",
            expected_runs=len(expected), observed_runs=len(observed))
    a.check(metadata.get("recorded_runs") == len(rows) and metadata.get("expected_runs") == len(expected), "metadata_run_count")
    old_points = order_route(directional_points("triangular", 950.))
    paired = defaultdict(list)
    for index, row in enumerate(rows):
        paired[row["case_id"]].append(row)
        try:
            audit_run(a, root, partition, row, cases[row["case_id"]], manifest, frozen, old_points)
        except Exception as exc:
            a.check(False, "audit_run_exception", case_id=row.get("case_id"), variant=row.get("variant"),
                    error=f"{type(exc).__name__}: {exc}")
        if progress_every and ((index+1) % progress_every == 0 or index+1 == len(rows)):
            print(f"audit {partition}: {index+1}/{len(rows)} runs; issues={len(a.issues)}", file=sys.stderr, flush=True)
    for identity, group in paired.items():
        a.check({r["variant"] for r in group} == set(arms), "same_case_arm_set", case_id=identity)
        for key in ("world_sha256", "error_field_sha256", "scenario_sha256", "policy_core_sha256", "source_freeze_sha256"):
            a.check(len({r.get(key) for r in group}) == 1, "same_case_identity", case_id=identity, field=key)
    return {"schema_version": 1, "audit_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "round_root": str(root), "partition": partition, "status": "passed" if not a.issues else "issues_found",
        "issue_count": len(a.issues), "issue_counts": dict(Counter(i["kind"] for i in a.issues)), "issues": a.issues,
        "distinct_worlds": len(paired), "expected_worlds": len(cases), "expected_runs": len(expected),
        "counts": dict(a.counts), "worst_residuals": a.residuals, "case_audits": a.case_reports,
        "source_freeze_sha256": frozen["source_freeze_sha256"], "manifest_sha256": manifest["manifest_sha256"],
        "input_results_sha256": sha(root/partition/"results.json"), "auditor_sha256": sha(__file__),
        "wall_time_s": time.monotonic()-started,
        "scope": "Saved local evidence only. No solver execution, no official actions, no file changes except explicit audit output.",
        "timing_policy": "Exact per-action round(distance/5*1e6) integer microseconds plus switch/measure/clear costs; continuous accounting separately allows 0.5 microsecond per move.",
        "truth_policy": "Source positions consumed only by the external audit evaluator for actual clear geometry and hull inclusion; never passed to strategy.",
        "limitations": "Does not rederive the noise field or compare measured direction values against it; verifies fixed field identity, request/response/decision agreement, and geometric envelope containment."}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--round", type=Path, required=True)
    p.add_argument("--partition", choices=("development", "confirmation"), required=True)
    p.add_argument("--output", type=Path)
    p.add_argument("--progress-every", type=int, default=20)
    args = p.parse_args(argv)
    report = audit_round(args.round, args.partition, args.progress_every)
    if args.output:
        if args.output.exists():
            raise FileExistsError("Preserve prior audit: choose a new output file")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("status", "issue_count", "distinct_worlds", "expected_runs", "counts", "worst_residuals", "wall_time_s")}, ensure_ascii=False, indent=2))
    return 0 if report["issue_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
