"""Frozen P4 PRACTICE comparison: separate engineering pilots and fixed validation.

Only ``run`` can dispatch. Prepare/preflight/summarize are entirely offline.
Policy code is the exact local-confirmation adapter, with no hidden-world input.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import statistics
import sys
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
import collect_official_round as collector
import run_p4_refinement as local
from bsolver.round_experiments import canonical, digest

ARMS = ("current", "combined_cover")
STAGES = ("pilot", "validation")
LOCAL_SELECTION_SHA = "5db18de458ee30406e20ffcdb0728653cf7af1e36b3f70471413031b0531b7a4"
LOCAL_FREEZE_SHA = "33d35e5838d7b3ac482f19e0ce67639942974aa016e703d5ecd53c0247a77ee6"
LOCAL_MANIFEST_SHA = "2012d9b665c1852cd3640038169e7b568cb220ef4e0175b5544c8dc285ed9daa"
EXTRA_FILES = ("scripts/run_p4_refined_official.py", "scripts/collect_official_round.py",
               "scripts/round_ui.ps1")
FAILURE_PENALTY = 360000.


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked_digest(value, key):
    body = dict(value)
    expected = body.pop(key, None)
    if not expected or digest(body) != expected:
        raise ValueError(f"Modified {key}")
    return expected


def allocations(per_arm=30, pilot_per_arm=1, seed=2026091101):
    if (type(per_arm) is not int or per_arm < 2 or per_arm % 2
            or type(pilot_per_arm) is not int or pilot_per_arm < 1 or type(seed) is not int):
        raise ValueError("Even positive validation count per arm and positive pilot count required")
    rng, rows = random.Random(seed), []
    for stage in STAGES:
        groups = ([list(ARMS) for _ in range(pilot_per_arm)] if stage == "pilot" else
                  [[*ARMS, *ARMS] for _ in range(per_arm // 2)])
        for block, arms in enumerate(groups, 1):
            rng.shuffle(arms)
            for within, arm in enumerate(arms, 1):
                rows.append({"case_id": f"p4r-{stage}-b{block:02d}-{within:02d}-{arm}",
                    "problem": 4, "arm": arm, "protocol": arm, "stage": stage, "split": stage,
                    "pilot": stage == "pilot", "block": block, "within_block": within,
                    "sequence": len(rows), "environment": "official_practice",
                    "round": "p4_refined_official", "source_truth": None})
    return rows


def prepare(out, *, per_arm=30, pilot_per_arm=1, seed=2026091101,
            local_round=ROOT/"results/p4_refinement"):
    out, local_round = Path(out).resolve(), Path(local_round).resolve()
    if out.exists():
        raise FileExistsError("Preserve previous output: prepare requires a fresh directory")
    rows = allocations(per_arm, pilot_per_arm, seed)
    manifest, frozen, selection = (read(local_round / n) for n in
                                  ("manifest.json", "source_freeze.json", "selection.json"))
    local.validate_freeze(manifest, frozen)
    if (manifest["manifest_sha256"] != LOCAL_MANIFEST_SHA
            or frozen["source_freeze_sha256"] != LOCAL_FREEZE_SHA
            or checked_digest(selection, "selection_sha256") != LOCAL_SELECTION_SHA
            or selection.get("selected_variants") != ["combined_cover", "coverage"]):
        raise ValueError("Only the exact completed, frozen local refinement is permitted")
    current = local.checked_candidate(ROOT/"results/round2/candidate.json")
    if current != manifest["current_spec"]:
        raise ValueError("Current specification differs from round2")
    bundle = {"current_spec": current, "refinement": frozen["refinement"],
        "local_manifest_sha256": LOCAL_MANIFEST_SHA, "local_source_freeze_sha256": LOCAL_FREEZE_SHA,
        "local_selection_sha256": LOCAL_SELECTION_SHA,
        "policy_boundary": "Only current_spec/refinement passed to make_policy; no scenario or source truth."}
    bundle["bundle_sha256"] = digest(bundle)
    plan = {"schema_version": 1, "study": "p4_refined_official", "problem": 4,
        "practice_only": True, "practice_authorized_by_round_plan": True,
        "formal_authorized": False, "dispatch_ready": True,
        "created_utc": collector.utc(), "seed": seed,
        "allocation": "Independent official cases; randomized blocks of two cases per arm; not same-world pairs.",
        "sample_sizes": {"validation_per_arm": per_arm, "pilot_per_arm": pilot_per_arm,
                         "validation_blocks": per_arm//2, "planned_total": len(rows)},
        "arms": list(ARMS), "cases": rows, "bundle_sha256": bundle["bundle_sha256"],
        "analysis": {"primary": "validation only; combined_cover minus current mean virtual time",
            "bootstrap": "independent within arms", "bootstrap_repetitions": 10000,
            "bootstrap_seed": 2026091137, "failure_penalty_s": FAILURE_PENALTY,
            "fixed_sample_no_significance_stopping": True,
            "pilots": "Engineering integrity gate only; excluded from validation efficacy estimates."},
        "failure_policy": "Preserve all launched attempts; pause on interface/integrity failure; no replacement.",
        "pilot_gate": "All planned pilots pass complete/exit/UI count/original log/request/coverage checks; no timing superiority gate."}
    plan["plan_sha256"] = digest(plan)
    out.mkdir(parents=True)
    collector.write_json(out/"plan.json", plan)
    collector.write_json(out/"policy_bundle.json", bundle)
    for case in rows:
        collector.write_json(out/"cases"/case["case_id"]/"assignment.json", case)
    copied = {"local_selection_input.json": local_round/"selection.json",
              "local_source_freeze_input.json": local_round/"source_freeze.json",
              "current_candidate_input.json": ROOT/"results/round2/candidate.json",
              "baseline_freeze.json": ROOT/"results/round1/baseline_freeze.json"}
    for name, path in copied.items():
        (out/name).write_bytes(path.read_bytes())
    files = dict(frozen["source_sha256"])
    files.update({name: sha(ROOT/name) for name in EXTRA_FILES})
    with zipfile.ZipFile(out/"source_snapshot.zip", "x", zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            archive.write(ROOT/name, name)
    official = {"schema_version": 1, "files": files, "plan_sha256": plan["plan_sha256"],
        "plan_file_sha256": sha(out/"plan.json"), "policy_bundle_sha256": sha(out/"policy_bundle.json"),
        "local_selection_sha256": LOCAL_SELECTION_SHA, "local_source_freeze_sha256": LOCAL_FREEZE_SHA,
        "input_files": {name: sha(out/name) for name in copied},
        "source_snapshot_sha256": sha(out/"source_snapshot.zip"),
        "runtime": {"python": sys.version, "executable": sys.executable, "platform": platform.platform()},
        "practice_only": True, "formal_authorized": False}
    official["freeze_sha256"] = digest(official)
    collector.write_json(out/"official_freeze.json", official)
    return preflight(out)


def preflight(out):
    """Read-only offline preflight; never calls the UI, HTTP, or platform."""
    out = Path(out).resolve()
    plan, frozen, bundle = (read(out/name) for name in
                           ("plan.json", "official_freeze.json", "policy_bundle.json"))
    checked_digest(frozen, "freeze_sha256")
    checked_digest(plan, "plan_sha256")
    checked_digest(bundle, "bundle_sha256")
    if (plan.get("problem") != 4 or plan.get("practice_only") is not True
            or plan.get("formal_authorized") is not False or frozen.get("formal_authorized") is not False
            or plan.get("practice_authorized_by_round_plan") is not True or plan.get("dispatch_ready") is not True
            or frozen.get("practice_only") is not True or plan.get("arms") != list(ARMS)):
        raise ValueError("Only authorized P4 practice with the frozen two arms is permitted")
    for name, expected in {"plan.json": frozen["plan_file_sha256"],
        "policy_bundle.json": frozen["policy_bundle_sha256"],
        "source_snapshot.zip": frozen["source_snapshot_sha256"], **frozen["input_files"]}.items():
        if sha(out/name) != expected:
            raise ValueError(f"Frozen artifact changed: {name}")
    if (plan["plan_sha256"] != frozen["plan_sha256"] or bundle["bundle_sha256"] != plan["bundle_sha256"]
            or bundle["local_manifest_sha256"] != LOCAL_MANIFEST_SHA
            or frozen["local_selection_sha256"] != LOCAL_SELECTION_SHA
            or bundle["local_selection_sha256"] != LOCAL_SELECTION_SHA
            or bundle["local_source_freeze_sha256"] != LOCAL_FREEZE_SHA
            or frozen["local_source_freeze_sha256"] != LOCAL_FREEZE_SHA):
        raise ValueError("Frozen provenance mismatch")
    previous = read(out/"local_source_freeze_input.json")
    selection = read(out/"local_selection_input.json")
    if (checked_digest(previous, "source_freeze_sha256") != LOCAL_FREEZE_SHA
            or checked_digest(selection, "selection_sha256") != LOCAL_SELECTION_SHA
            or bundle["refinement"] != previous["refinement"]
            or bundle["current_spec"] != local.checked_candidate(out/"current_candidate_input.json")):
        raise ValueError("Changed local policy input")
    expected_files = dict(previous["source_sha256"])
    expected_files.update({name: sha(ROOT/name) for name in EXTRA_FILES})
    if expected_files != frozen["files"] or local.source_hashes() != previous["source_sha256"]:
        raise ValueError("Frozen execution source changed; create a reviewed new version")
    with zipfile.ZipFile(out/"source_snapshot.zip") as archive:
        if len(archive.namelist()) != len(expected_files) or set(archive.namelist()) != set(expected_files):
            raise ValueError("Source snapshot file set mismatch")
        for name, expected in expected_files.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError(f"Source snapshot mismatch: {name}")
    sizes = plan["sample_sizes"]
    expected_cases = allocations(sizes["validation_per_arm"], sizes["pilot_per_arm"], plan["seed"])
    if expected_cases != plan["cases"] or sizes["planned_total"] != len(expected_cases):
        raise ValueError("Changed randomized allocation or sample size")
    for case in expected_cases:
        if read(out/"cases"/case["case_id"]/"assignment.json") != case:
            raise ValueError(f"Assignment changed: {case['case_id']}")
    collector.verify_freeze(out)
    return {"out": out, "plan": plan, "frozen": frozen, "bundle": bundle}


def run_policy(case, folder, robot_id, base_url, *, client_factory=None):
    """Injected collector boundary. All clients in tests use local transport."""
    from bsolver.protocol import HTTPTransport, RobotClient
    folder = Path(folder)
    checked = preflight(folder.parents[1])
    if case not in checked["plan"]["cases"] or read(folder/"assignment.json") != case:
        raise ValueError("Policy requires a frozen assignment")
    if any((folder/name).exists() for name in ("requests.jsonl", "decisions.jsonl", "result.json")):
        raise RuntimeError("Attempt artifacts exist; never create a replacement client or replay the policy")
    bundle, frozen = checked["bundle"], checked["frozen"]
    client = (client_factory() if client_factory else RobotClient(robot_id=robot_id,
        transport=HTTPTransport(base_url), log_path=folder/"requests.jsonl",
        request_prefix=case["case_id"]+"-"+uuid.uuid4().hex[:8]))
    solver = None
    try:
        solver = local.make_policy(client, case["arm"], {"current_spec": bundle["current_spec"]},
                                  {"refinement": bundle["refinement"]}, folder/"decisions.jsonl")
        result = solver.run()
    except Exception as exc:
        result = {"status": "runner_error", "error": f"{type(exc).__name__}: {exc}",
                  "total_virtual_time_s": client.virtual_time, "problem": 4}
    finally:
        client.close_log()
    s = client.stats
    result.update({key: case[key] for key in ("case_id", "problem", "arm", "protocol", "stage", "split")})
    result.update(environment="official_practice", source_total=None, directional_total=None,
        source_truth=None, case_code=None, clear_fraction=None,
        policy_spec=bundle["current_spec"], baseline_sha256=local.FROZEN_CORE_SHA256,
        official_freeze_sha256=frozen["freeze_sha256"], policy_bundle_sha256=frozen["policy_bundle_sha256"],
        local_selection_sha256=LOCAL_SELECTION_SHA,
        pending_request=client.pending_request, final_client_state=client.state_snapshot(), public_client_stats=s,
        walk_distance_m=s["walk_distance"], switches=s["switches"], measures=s["measures"],
        clear_attempts=s["clear_attempts"], clear_successes=s["successes"], failed_clear_attempts=s["failures"],
        movement_s=s["walk_distance"]/5., switch_s=float(s["switches"]), measurement_s=5.*s["measures"],
        optical_s=3.*s["clear_attempts"], laser_s=2.*s["successes"], failed_clear_cost_s=3.*s["failures"],
        coverage_point_count=len(solver.points) if solver else None,
        coverage_points_sha256=digest(solver.points) if solver else None,
        coverage_variant="refined_directional_triangular" if case["arm"] == "combined_cover" else "original_frozen_triangular",
        coverage_certificate=bundle["refinement"]["coverage_certificate"] if case["arm"] == "combined_cover" else None)
    result["artifact_sha256"] = {name: sha(folder/name) for name in ("requests.jsonl", "decisions.jsonl") if (folder/name).exists()}
    collector.write_json(folder/"result.json", result)
    return result


def jsonlines(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def public_action_cost(position, channel, row):
    """Clear can name another target channel; only measure changes tuning."""
    request, response, path = row["request"], row["response"], row["path"]
    cost = 0.
    if path in ("/measure", "/clear"):
        q = (request["position"]["x"], request["position"]["y"])
        cost = math.dist(position, q)/5
        if path == "/measure":
            cost += 5+int(request["channel"] != channel)
            channel = request["channel"]
        else:
            cost += 5 if response["clear_result"] == "success" else 3
        position = q
    return position, channel, cost


def mechanical_gate(case, folder, checked):
    """Deterministic engineering criteria, with no comparative performance test."""
    folder = Path(folder)
    issues = []
    def require(ok, label):
        if not ok:
            issues.append(label)
    try:
        result, audit, session, ui = (read(folder/name) for name in
            ("result.json", "post_exit_audit.json", "session.json", "post_exit_ui.json"))
        records, decisions = jsonlines(folder/"requests.jsonl"), jsonlines(folder/"decisions.jsonl")
        for doc in (result, audit):
            for key in ("case_id", "problem", "protocol"):
                require(doc.get(key) == case[key], f"{key} metadata mismatch")
        require(result.get("stage") == case["stage"] and result.get("arm") == case["arm"], "stage/arm mismatch")
        require(result.get("official_freeze_sha256") == checked["frozen"]["freeze_sha256"], "result source freeze mismatch")
        require(result.get("policy_bundle_sha256") == checked["frozen"]["policy_bundle_sha256"], "result bundle mismatch")
        require(result.get("status") == audit.get("status") == "complete" and not result.get("error"), "case not complete")
        require(result.get("pending_request") is None and result.get("final_client_state", {}).get("exited") is True,
                "pending action or unconfirmed exit")
        require(result.get("initial_hull_invariant") is True, "initial hull assertion missing")
        require(all(result.get(k) is None for k in ("source_total", "directional_total", "source_truth")), "truth leaked into policy result")
        n, nd, no = (audit.get(k) for k in ("source_total_post_exit", "directional_total_post_exit", "omnidirectional_total_post_exit"))
        require(type(n) is int and 10 <= n <= 16 and type(nd) is int and type(no) is int
                and nd >= 0 and no >= 0 and nd+no == n, "invalid post-exit composition")
        require(audit.get("cleared") == result.get("clear_successes") == n, "public full clear not verified")
        names = collector.names(ui)
        require(session.get("case_code") == audit.get("case_code") and session.get("case_code") in names
                and "测试已结束" in names, "post-exit UI does not match session")
        logname = audit.get("original_log_filename")
        require(isinstance(logname, str) and Path(logname).name == logname
                and logname.startswith("practice-p4-") and session.get("case_code", "?") in logname,
                "missing or wrong original practice log")
        if isinstance(logname, str) and Path(logname).name == logname:
            original = folder/"original_logs"/logname
            require(original.is_file() and original.stat().st_size == audit.get("original_log_bytes")
                    and original.stat().st_size > 0 and sha(original) == audit.get("original_log_sha256"), "original log hash/size mismatch")
        for name in ("requests.jsonl", "decisions.jsonl"):
            require(result.get("artifact_sha256", {}).get(name) == sha(folder/name), f"{name} hash mismatch")
        accepted = [r for r in records if r.get("outcome") == "accepted"]
        require(bool(accepted) and accepted[0]["path"] == "/enter" and accepted[-1]["path"] == "/exit", "enter/exit not confirmed")
        require(all(r.get("outcome") == "accepted" for r in records), "transport/rejected request needs independent review")
        require(len({r["request"]["request_id"] for r in accepted}) == len(accepted), "duplicate accepted request")
        counts = Counter(r["path"] for r in accepted)
        require(counts["/enter"] == counts["/exit"] == 1, "multiple enter/exit")
        successes = [r for r in accepted if r["path"] == "/clear" and r["response"].get("clear_result") == "success"]
        require(len({r["request"]["channel"] for r in successes}) == len(successes) == n, "distinct successful clears mismatch")
        require(counts["/measure"] == result["measures"] and counts["/clear"] == result["clear_attempts"]
                and counts["/clear"]-len(successes) == result["failed_clear_attempts"], "request counts mismatch")
        worst = 0.
        position, channel, previous_t = (0., 0.), 1, 0.
        for row in accepted:
            response = row["response"]
            position, channel, cost = public_action_cost(position, channel, row)
            require(row.get("state_after", {}).get("current_channel") == channel, "confirmed tuning state mismatch")
            residual = response["virtual_time_s"]-previous_t-cost
            worst = max(worst, abs(residual))
            require(abs(residual) <= 2.1e-6, "per-request virtual-time mismatch")
            previous_t = response["virtual_time_s"]
        require(abs(previous_t-result["total_virtual_time_s"]) < 1e-6
                and abs(previous_t-audit["total_virtual_time_s"]) < 1e-6, "final virtual-time mismatch")
        starts = [e for e in decisions if e.get("event") == "start"]
        require(len(starts) == 1, "missing or multiple policy start")
        points = starts[0]["coverage_points"]
        if case["arm"] == "combined_cover":
            expected = checked["bundle"]["refinement"]["refined_route"]
            require(starts[0].get("refined_coverage_certificate") == checked["bundle"]["refinement"]["coverage_certificate"], "refined execution certificate missing")
        else:
            from bsolver.coverage import directional_points, order_route
            expected = order_route(directional_points("triangular"))
        require(digest(points) == digest(expected) == result.get("coverage_points_sha256"), "actual coverage points mismatch")
        require(result.get("coverage_point_count") == len(expected), "coverage count mismatch")
        evidence = result.get("stop_evidence") or {}
        cleared = sorted(r["request"]["channel"] for r in successes)
        require(evidence.get("cleared_channels") == cleared, "stop cleared list mismatch")
        if evidence.get("type") == "known_upper_bound":
            require(len(cleared) == 16, "upper-bound stop before 16 clears")
        elif evidence.get("type") == "per_channel_coverage":
            require(digest(evidence.get("points")) == digest(expected) and evidence.get("required_point_count") == len(expected), "stop points/count mismatch")
            absent = sorted(set(range(1, 21))-set(cleared))
            require(evidence.get("absent_channels") == absent, "stop absence list mismatch")
            for c in absent:
                observed = {digest((r["request"]["position"]["x"], r["request"]["position"]["y"]))
                    for r in accepted if r["path"] == "/measure" and r["request"]["channel"] == c
                    and r["response"].get("measure_result") == "no_signal"}
                require(evidence.get("checked_indices", {}).get(str(c)) == list(range(len(expected)))
                        and all(digest(p) in observed for p in expected), f"unobserved absence coverage for channel {c}")
            if case["arm"] == "combined_cover":
                require(evidence.get("construction") == "refined_directional_triangular"
                        and evidence.get("coverage_points_sha256") == digest(expected), "refined stop construction mismatch")
        else:
            require(False, "unknown stop evidence")
        return {"case_id": case["case_id"], "stage": case["stage"], "arm": case["arm"],
                "passed": not issues, "issues": sorted(set(issues)), "request_records": len(records),
                "accepted_requests": len(accepted), "failed_clear_attempts": counts["/clear"]-len(successes),
                "max_request_cost_residual_s": worst, "checked_utc": collector.utc()}
    except (KeyError, TypeError, ValueError, OSError, IndexError) as exc:
        issues.append(f"Missing/invalid evidence: {type(exc).__name__}: {exc}")
        return {"case_id": case["case_id"], "stage": case["stage"], "arm": case["arm"],
                "passed": False, "issues": sorted(set(issues)), "checked_utc": collector.utc()}


def enforce_finished(case, out, checked):
    gate = mechanical_gate(case, Path(out)/"cases"/case["case_id"], checked)
    collector.write_json(Path(out)/"cases"/case["case_id"]/"mechanical_gate.json", gate)
    if not gate["passed"]:
        raise RuntimeError(f"Integrity pause for {case['case_id']}; attempt retained: {gate['issues']}")
    return gate


def run_frozen_plan(out, robot_id, *, stage, maximum=60, base_url="http://127.0.0.1:2026",
                    collect_module=collector, policy_runner=run_policy):
    if not robot_id or stage not in STAGES or type(maximum) is not int or maximum < 1:
        raise ValueError("robot-id, pilot|validation stage, positive max-new required")
    checked = preflight(out)
    out, locks, count = checked["out"], [], 0
    try:
        for directory in sorted({out, ROOT/"results/round1", ROOT/"results/round2"}, key=str):
            locks.append(collect_module.collection_lock(directory))
        # Never silently skip an already audited failure, even on a later run.
        for case in checked["plan"]["cases"]:
            folder = out/"cases"/case["case_id"]
            if (folder/"post_exit_audit.json").exists():
                enforce_finished(case, out, checked)
            elif case["stage"] != stage and any((folder/n).exists() for n in
                    ("launch_intent.json", "session.json", "requests.jsonl", "result.json")):
                raise RuntimeError("Unresolved attempt in another stage; preserve and inspect")
        if stage == "validation":
            gates = [enforce_finished(c, out, checked) for c in checked["plan"]["cases"] if c["pilot"]]
            collector.write_json(out/"pilot_gate.json", {"passed": True, "gates": gates,
                "criterion": "Mechanical integrity only; no efficacy selection", "checked_utc": collector.utc()})
        pending = [c for c in checked["plan"]["cases"] if c["stage"] == stage
                   and not (out/"cases"/c["case_id"]/"post_exit_audit.json").exists()]
        if pending and not (out/"STOP_AFTER_CASE").exists():
            collect_module.start_bridge()
        collect_module.status(out, "running", stage=stage)
        for case in pending:
            if count >= maximum or (out/"STOP_AFTER_CASE").exists():
                break
            checked = preflight(out)
            folder = out/"cases"/case["case_id"]
            recovering = (folder/"result.json").exists()
            collect_module.collect({"cases": [case]}, out, robot_id, base_url, 1,
                                   adopt=False, policy_runner=policy_runner)
            if not (folder/"post_exit_audit.json").exists():
                if (out/"STOP_AFTER_CASE").exists():
                    break
                raise RuntimeError("Collector returned without audit; preserve attempted case")
            enforce_finished(case, out, checked)
            if not recovering:
                count += 1
        collect_module.status(out, "stopped_normally", stage=stage, new_cases_this_call=count)
    except Exception as exc:
        collect_module.status(out, "needs_attention", stage=stage, error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        collect_module.close_bridge()
        for lock in reversed(locks):
            lock.close()
    return {"new_cases": count, "stage": stage,
            "planned_cases": sum(c["stage"] == stage for c in checked["plan"]["cases"])}


def percentile(values, q):
    values = sorted(values)
    if not values:
        return None
    x = q*(len(values)-1)
    a, b = math.floor(x), math.ceil(x)
    return values[a] if a == b else values[a]*(b-x)+values[b]*(x-a)


def independent_bootstrap(current, combined, *, seed=2026091137, repetitions=10000):
    if not current or not combined:
        return {"difference": None, "ci95": None, "current_n": len(current), "combined_cover_n": len(combined)}
    rng = random.Random(seed)
    draws = []
    for _ in range(repetitions):
        a = statistics.fmean(rng.choices(current, k=len(current)))
        b = statistics.fmean(rng.choices(combined, k=len(combined)))
        draws.append(b-a)
    return {"difference": statistics.fmean(combined)-statistics.fmean(current),
            "ci95": [percentile(draws, .025), percentile(draws, .975)],
            "current_n": len(current), "combined_cover_n": len(combined), "resampling": "independent within arms, no paired official cases"}


def wilson(failures, n):
    if not n:
        return None
    z, p = 1.959963984540054, failures/n
    d = 1+z*z/n
    c = (p+z*z/(2*n))/d
    r = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return [max(0., c-r), min(1., c+r)]


def collect_result_rows(out):
    checked = preflight(out)
    out = checked["out"]
    rows = []
    for case in checked["plan"]["cases"]:
        folder = out/"cases"/case["case_id"]
        result = read(folder/"result.json") if (folder/"result.json").exists() else {}
        audit = read(folder/"post_exit_audit.json") if (folder/"post_exit_audit.json").exists() else {}
        attempted = any((folder/n).exists() for n in ("launch_intent.json", "session.json", "requests.jsonl", "result.json", "post_exit_audit.json", "start_failure.json"))
        gate = mechanical_gate(case, folder, checked) if attempted else {"passed": False, "issues": []}
        n, nd = audit.get("source_total_post_exit"), audit.get("directional_total_post_exit")
        t = result.get("total_virtual_time_s")
        complete = bool(gate["passed"])
        rows.append({**case, "attempted": attempted, "status": result.get("status", "attempt_incomplete" if attempted else "not_started"),
            "verified_full_clear": complete, "source_total_post_exit": n, "directional_total_post_exit": nd,
            "total_virtual_time_s": t, "cleared": result.get("clear_successes"),
            "penalized_loss_s": (t if complete else FAILURE_PENALTY) if attempted else None,
            "components": {k: result[k] for k in ("movement_s", "switch_s", "measurement_s", "optical_s", "laser_s", "failed_clear_cost_s") if k in result},
            "failed_clear_attempts": result.get("failed_clear_attempts"), "fallback_targets": result.get("fallback_targets"),
            "program_real_time_s": result.get("program_real_time_s"), "gate_issues": gate["issues"]})
    return rows


def summarize(out):
    """Prespecified independent bootstrap; engineering pilots never enter validation."""
    rows = collect_result_rows(out)
    report = {"problem": 4, "comparison": "combined_cover minus current; independent official cases",
        "bootstrap_repetitions": 10000, "bootstrap_seed": 2026091137, "failure_penalty_s": FAILURE_PENALTY,
        "fixed_sample_no_significance_stopping": True, "stages": {}, "cases": rows}
    mean = lambda a: statistics.fmean(a) if a else None
    for stage in STAGES:
        block = [r for r in rows if r["stage"] == stage]
        arms = {}
        for arm in ARMS:
            planned = [r for r in block if r["arm"] == arm]
            tried = [r for r in planned if r["attempted"]]
            complete = [r for r in tried if r["verified_full_clear"]]
            times = [r["total_virtual_time_s"] for r in complete]
            failure = len(tried)-len(complete)
            arms[arm] = {"planned": len(planned), "attempted": len(tried), "not_started": len(planned)-len(tried),
                "verified_full_clear": len(complete), "failures_or_unverified": failure,
                "failure_rate": failure/len(tried) if tried else None, "failure_rate_wilson95": wilson(failure, len(tried)),
                "completion_time_mean_s": mean(times), "completion_time_median_s": statistics.median(times) if times else None,
                "completion_time_p90_s": percentile(times, .9), "completion_time_max_s": max(times) if times else None,
                "penalized_loss_mean_s": mean([r["penalized_loss_s"] for r in tried]),
                "component_means_s": {k: mean([r["components"][k] for r in tried if k in r["components"]])
                    for k in ("movement_s", "measurement_s", "switch_s", "optical_s", "laser_s", "failed_clear_cost_s")},
                "composition": {"mean_N": mean([r["source_total_post_exit"] for r in tried if r["source_total_post_exit"] is not None]),
                    "mean_Ndir": mean([r["directional_total_post_exit"] for r in tried if r["directional_total_post_exit"] is not None]),
                    "known_N_cases": sum(r["source_total_post_exit"] is not None for r in tried)}}
        section = {"arms": arms, "evidence_role": "engineering integrity only" if stage == "pilot" else "fixed validation",
                   "complete_case_time_is_conditional": True}
        if stage == "validation":
            differences = {}
            for i, field in enumerate(("total_virtual_time_s", "penalized_loss_s")):
                data = {arm: [r[field] for r in block if r["arm"] == arm and r["attempted"]
                    and (field != "total_virtual_time_s" or r["verified_full_clear"])] for arm in ARMS}
                differences[field] = independent_bootstrap(data[ARMS[0]], data[ARMS[1]], seed=2026091137)
            section["combined_cover_minus_current"] = differences
            section["caution"] = "Independent cases; pointwise CI, no paired inference or significance stopping. Zero observed failures do not establish zero risk; inspect Wilson intervals and failed/unverified attempts."
        report["stages"][stage] = section
    collector.write_json(Path(out)/"validation_summary.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "preflight", "run", "summarize"):
        p = sub.add_parser(name)
        p.add_argument("--out", type=Path, default=ROOT/"results/p4_refined_official")
        if name == "prepare":
            p.add_argument("--per-arm", type=int, default=30)
            p.add_argument("--pilot-per-arm", type=int, default=1)
            p.add_argument("--seed", type=int, default=2026091101)
        if name == "run":
            p.add_argument("--robot-id", required=True)
            p.add_argument("--stage", choices=STAGES, required=True)
            p.add_argument("--max-new", type=int, default=60)
            p.add_argument("--base-url", default="http://127.0.0.1:2026")
    args = parser.parse_args(argv)
    if args.command == "prepare":
        checked = prepare(args.out, per_arm=args.per_arm, pilot_per_arm=args.pilot_per_arm, seed=args.seed)
        print(json.dumps({"prepared": str(checked["out"]), "cases": len(checked["plan"]["cases"]),
            "freeze_sha256": checked["frozen"]["freeze_sha256"]}))
    elif args.command == "preflight":
        checked = preflight(args.out)
        print(json.dumps({"preflight": "passed", "offline": True, "cases": len(checked["plan"]["cases"]),
            "freeze_sha256": checked["frozen"]["freeze_sha256"]}))
    elif args.command == "run":
        print(json.dumps(run_frozen_plan(args.out, args.robot_id, stage=args.stage,
                         maximum=args.max_new, base_url=args.base_url)))
    else:
        report = summarize(args.out)
        print(json.dumps({"summary": str(args.out/"validation_summary.json"),
                          "cases": len(report["cases"]), "stages": list(report["stages"])}))


if __name__ == "__main__":
    main()
