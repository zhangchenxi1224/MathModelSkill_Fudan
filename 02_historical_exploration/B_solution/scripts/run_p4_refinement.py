"""Local P4 factorial refinement study; explicit frozen release gates execution.

Prepare worlds first, freeze reviewed implementations, run development, freeze a
selection, then release independent confirmation. No official interface exists.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import statistics
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bsolver.geometry import contains
from bsolver.nosignal_sensing import NoSignalConfig, NoSignalSolver
from bsolver.protocol import RobotClient
from bsolver.round_experiments import (CORE_FILES, FROZEN_CORE_SHA256, POOLS,
    assert_frozen_core, bootstrap_mean_ci, canonical, compare_paired, core_digest,
    digest, generate_manifest, seed_for, validate_manifest, write_csv, write_json)
from bsolver.simulator import FixedErrorField, LocalSimulator, Source
from bsolver.strategy import SolverConfig

DEFAULT_ARMS = ("current", "coverage", "local_cover", "combined_cover")
CANDIDATE_SHA256 = "3e305f7f13a9e22d8b37a6e3d5ab3b1321357a3a8d4c418364ca8adae5424816"
DEPENDENCIES = ("scripts/run_p4_refinement.py", "src/bsolver/round_experiments.py",
    "src/bsolver/nosignal_sensing.py", "src/bsolver/refined_coverage.py", "src/bsolver/refined_local.py")
DEFAULT_PREVIOUS = ("results/round1/local/manifest.json", "results/round1/stage2/manifest.json",
                    "results/round1/stage3/manifest.json")
RULE = {
    "completion": "Every candidate run must fully clear and have zero truth-hull violations; missing runs disqualify.",
    "pool_gate": "Candidate-minus-current mean penalized time must be <=0 in each of calibrated/broad/stress.",
    "primary": "Among eligible candidates choose smallest calibrated mean total time; ties use declared arm order.",
    "secondary": "If eligible and not primary, retain coverage as an additional conservative comparator.",
    "maximum_candidates": 2,
    "no_forced_choice": True,
    "confirmation": "Current plus frozen eligible selection only; no new selection from confirmation.",
    "ci": "Pointwise paired bootstrap, 2000 draws; per-pool/composition/mechanism reporting; not multiplicity-adjusted.",
    "population": "Each synthetic design pool reported separately; calibrated mixture is not an official population estimate.",
}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def world_signature(case):
    return digest({k: case[k] for k in ("problem", "sources", "error_field")})


def checked_candidate(path):
    assert_frozen_core()
    path = Path(path)
    if sha(path) != CANDIDATE_SHA256:
        raise ValueError("Current must be the exact frozen round2 candidate")
    value = read(path)
    if value["baseline_core_sha256"] != FROZEN_CORE_SHA256:
        raise ValueError("Candidate core mismatch")
    spec = value["problems"]["4"]
    if (spec["solver_class"] != "nosignal" or spec["solver_config"]["local_measure_limit"] != 1
            or spec["solver_config"]["scheduling"] != "joint"):
        raise ValueError("Expected frozen P4 NoSignal L1 joint")
    for name, expected in value["dependency_hashes"].items():
        target = (ROOT / name).resolve()
        if not target.is_relative_to(ROOT) or sha(target) != expected:
            raise ValueError(f"Candidate dependency changed: {name}")
    return spec


def build_manifest(calibration, mechanisms, candidate_spec, *, development_sizes=(80, 80, 40),
                   confirmation_sizes=(320, 320, 160), development_seed=2026092101,
                   confirmation_seed=2026092201, include_adaptive=False, previous=()):
    if development_seed == confirmation_seed:
        raise ValueError("Development and confirmation need independent seeds")
    for sizes in (development_sizes, confirmation_sizes):
        if len(sizes) != 3 or any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in sizes):
            raise ValueError("Three nonnegative pool counts required")
    old_seeds, old_worlds = set(), set()
    for earlier in previous:
        validate_manifest(earlier)
        old_seeds.update(c["seed"] for c in earlier["scenarios"])
        old_worlds.update(world_signature(c) for c in earlier["scenarios"])
    cases = []
    for partition, sizes, master in (("development", development_sizes, development_seed),
                                     ("confirmation", confirmation_sizes, confirmation_seed)):
        generated = generate_manifest(calibration, sizes=tuple(2*n for n in sizes), master_seed=master,
            calibrated_models=("smoothed_joint", "broad_joint"), mechanism_candidates=mechanisms)
        for original in generated["scenarios"]:
            if original["problem"] != 4:
                continue
            case = dict(original)
            world = world_signature(case)
            if case["seed"] in old_seeds or world in old_worlds:
                raise ValueError("Refinement world overlaps a previous or current partition")
            old_seeds.add(case["seed"])
            old_worlds.add(world)
            case.update(case_id=f"p4-refinement-{partition}-"+case["case_id"], partition=partition)
            case.pop("scenario_sha256")
            case["scenario_sha256"] = digest(case)
            cases.append(case)
    arms = list(DEFAULT_ARMS) + (["local_adaptive"] if include_adaptive else [])
    manifest = {"schema_version": 1, "study": "p4_refinement", "stage": "p4_refinement",
        "baseline": "current", "frozen_core_sha256": FROZEN_CORE_SHA256,
        "candidate_sha256": CANDIDATE_SHA256, "current_spec": candidate_spec,
        "arms": arms, "selection_rule": RULE,
        "seeds": {"development": development_seed, "confirmation": confirmation_seed},
        "partition_pool_sizes": {"development": dict(zip(POOLS, development_sizes)),
                                 "confirmation": dict(zip(POOLS, confirmation_sizes))},
        "distinct_scenarios": len(cases), "planned_development_runs": sum(development_sizes)*len(arms),
        "confirmation_runs": "Independent confirmation worlds times current plus 1-2 frozen selected candidates; none if no eligible candidate.",
        "calibrated_models": ["smoothed_joint", "broad_joint"], "calibration_sha256": digest(calibration),
        "calibration_model_id": calibration.get("model_id"), "mechanism_candidates": mechanisms,
        "previous_manifest_sha256": [p["manifest_sha256"] for p in previous],
        "environment_scope": "Synthetic composition and retained/unresolved mechanism assumptions, not identified official truth.",
        "evidence_role": "local_paired_refinement", "official_actions": False,
        "sources_access": "Simulator and post-run evaluator only; policy receives public RobotClient and frozen config.",
        "scenarios": cases}
    manifest["manifest_sha256"] = digest(manifest)
    validate_manifest(manifest)
    return manifest


def prepare(output, candidate, calibration, mechanisms, *, previous_paths=DEFAULT_PREVIOUS, **kwargs):
    output = Path(output)
    if output.exists():
        raise FileExistsError("Preserve old study: choose a fresh directory")
    spec = checked_candidate(candidate)
    prior = [read(ROOT/p) for p in previous_paths]
    manifest = build_manifest(read(calibration), read(mechanisms), spec, previous=prior, **kwargs)
    output.mkdir(parents=True)
    write_json(output / "manifest.json", manifest)
    for name, path in (("candidate_input.json", candidate), ("calibration_input.json", calibration),
                       ("mechanisms_input.json", mechanisms)):
        (output/name).write_bytes(Path(path).read_bytes())
    for case in manifest["scenarios"]:
        write_json(output / "scenarios" / (case["case_id"]+".json"), case)
    return manifest


def refinement_metadata():
    from bsolver.refined_coverage import refined_directional_route, refined_coverage_certificate
    from bsolver.refined_local import RefinedLocalConfig
    route = refined_directional_route()
    if not route or len(set(map(tuple, route))) != len(route) or any(
            len(p) != 2 or any(not math.isfinite(x) for x in p) for p in route):
        raise ValueError("Refined route must have distinct finite points")
    return {"refined_route": route, "refined_route_sha256": digest(route),
            "coverage_certificate": refined_coverage_certificate(), "refined_config": asdict(RefinedLocalConfig())}


def source_hashes():
    names = list(dict.fromkeys(["src/bsolver/"+n for n in CORE_FILES] + list(DEPENDENCIES)))
    return {name: sha(ROOT/name) for name in names}


def freeze(output):
    output = Path(output)
    manifest = read(output/"manifest.json")
    validate_manifest(manifest)
    checked_candidate(output/"candidate_input.json")
    target = output/"source_freeze.json"
    if target.exists():
        raise FileExistsError("Source freeze already exists")
    metadata = refinement_metadata()
    frozen = {"manifest_sha256": manifest["manifest_sha256"], "source_sha256": source_hashes(),
        "runtime": {"python": sys.version, "executable": sys.executable, "platform": platform.platform()},
        "refinement": metadata, "arm_semantics": {
            "current": "Exact round2 frozen P4 candidate",
            "coverage": "Current solver with only ordered global coverage points replaced",
            "local_cover": "Original global route; RefinedLocalSolver(mode=cover)",
            "combined_cover": "Refined route plus RefinedLocalSolver(mode=cover)",
            "local_adaptive": "Only if preregistered: original global route; RefinedLocalSolver(mode=adaptive)"}}
    frozen["source_freeze_sha256"] = digest(frozen)
    with zipfile.ZipFile(output/"source_snapshot.zip", "x", zipfile.ZIP_DEFLATED) as archive:
        for name in frozen["source_sha256"]:
            archive.write(ROOT/name, name)
    if source_hashes() != frozen["source_sha256"]:
        raise ValueError("Source changed during snapshot; choose a new study version")
    write_json(target, frozen)
    write_json(output/"release_template.json", {"authorized": False,
        "manifest_sha256": manifest["manifest_sha256"], "source_freeze_sha256": frozen["source_freeze_sha256"],
        "partitions": ["development"], "selection_sha256": None,
        "scope": "Local only; set authorized after root confirms reviewed code freeze. Confirmation requires frozen selection SHA."})
    return frozen


def validate_freeze(manifest, frozen):
    validate_manifest(manifest)
    copy = dict(frozen)
    claimed = copy.pop("source_freeze_sha256", None)
    if digest(copy) != claimed or frozen["manifest_sha256"] != manifest["manifest_sha256"]:
        raise ValueError("Modified source freeze or wrong manifest")
    assert_frozen_core()
    if source_hashes() != frozen["source_sha256"]:
        raise ValueError("Frozen source changed; require new output version")


def selected_arms(manifest, frozen, partition, release, selection=None):
    if partition not in ("development", "confirmation"):
        raise ValueError("Unknown partition")
    if (release.get("authorized") is not True or partition not in release.get("partitions", [])
            or release.get("manifest_sha256") != manifest["manifest_sha256"]
            or release.get("source_freeze_sha256") != frozen["source_freeze_sha256"]):
        raise ValueError("Explicit release must match frozen study and partition")
    if partition == "development":
        return manifest["arms"]
    selection = selection or {}
    obj = dict(selection)
    claimed = obj.pop("selection_sha256", None)
    if (digest(obj) != claimed or release.get("selection_sha256") != claimed
            or selection.get("manifest_sha256") != manifest["manifest_sha256"]
            or selection.get("source_freeze_sha256") != frozen["source_freeze_sha256"]):
        raise ValueError("Confirmation requires the frozen development selection")
    chosen = selection.get("selected_variants", [])
    if (not 1 <= len(chosen) <= 2 or len(set(chosen)) != len(chosen)
            or any(v == "current" or v not in manifest["arms"] for v in chosen)):
        raise ValueError("No eligible confirmation candidate or invalid selection")
    return ["current"]+chosen


class RefinedCoverageEvidenceMixin:
    """Correct execution certificates while retaining the frozen core logic."""
    def _complete_evidence(self):
        evidence = super()._complete_evidence()
        if evidence and evidence.get("type") == "per_channel_coverage":
            evidence.update(construction="refined_directional_triangular",
                refined_coverage_certificate=self.refined_coverage_certificate,
                coverage_points_sha256=digest(self.points))
        return evidence

    def _record(self, event, **data):
        info = {"coverage_variant": "refined_directional_triangular",
                "refined_coverage_certificate": self.refined_coverage_certificate,
                "coverage_points_sha256": digest(self.points)}
        if event in ("start", "finish"):
            data.update(info)
        if event == "finish":
            data["result"].update(info)
        super()._record(event, **data)


class RefinedCoverageNoSignalSolver(RefinedCoverageEvidenceMixin, NoSignalSolver):
    pass


def make_policy(client, arm, manifest, frozen, decision_log):
    spec = manifest["current_spec"]
    config = SolverConfig(**spec["solver_config"])
    extra = dict(spec["nosignal_config"])
    extra["radius_samples"] = tuple(extra["radius_samples"])
    kwargs = {"decision_log": decision_log, "nosignal_config": NoSignalConfig(**extra)}
    if arm in ("local_cover", "combined_cover", "local_adaptive"):
        from bsolver.refined_local import RefinedLocalConfig, RefinedLocalSolver
        if arm == "combined_cover":
            class RefinedCoverageLocalSolver(RefinedCoverageEvidenceMixin, RefinedLocalSolver):
                pass
            cls = RefinedCoverageLocalSolver
        else:
            cls = RefinedLocalSolver
        policy = cls(client, config, mode="adaptive" if arm == "local_adaptive" else "cover",
            refined_config=RefinedLocalConfig(**frozen["refinement"]["refined_config"]), **kwargs)
    elif arm in ("current", "coverage"):
        cls = RefinedCoverageNoSignalSolver if arm == "coverage" else NoSignalSolver
        policy = cls(client, config, **kwargs)
    else:
        raise ValueError("Unknown arm")
    if arm in ("coverage", "combined_cover"):
        if any(k.coverage_indices for k in policy.channels.values()) or client.entered:
            raise ValueError("Coverage replacement must precede every observation")
        policy.points = [tuple(p) for p in frozen["refinement"]["refined_route"]]
        policy.refined_coverage_certificate = frozen["refinement"]["coverage_certificate"]
    return policy


def stopping_tail(events, n, total_time, all_cleared):
    successes = [e for e in events if e.get("event") == "clear" and e.get("response", {}).get("clear_result") == "success"]
    if not all_cleared or len(successes) != n:
        return {"last_source_clear_time_s": None, "post_clear_stop_tail_s": None, "post_clear_measurements": None}
    last = successes[-1]
    tail = [e for e in events if e.get("sequence", -1) > last["sequence"]]
    return {"last_source_clear_time_s": last["virtual_time_s"],
            "post_clear_stop_tail_s": max(0., total_time-last["virtual_time_s"]),
            "post_clear_measurements": sum(e.get("event") == "measure" for e in tail)}


def compress_log(path):
    path = Path(path)
    if not path.exists():
        path.write_text("", encoding="utf-8")
    target = path.with_suffix(path.suffix+".gz")
    with path.open("rb") as inp, gzip.open(target, "wb") as out:
        while chunk := inp.read(1024*1024):
            out.write(chunk)
    path.unlink()
    return sha(target)


def run_arm(case, arm, manifest, frozen, directory):
    validate_freeze(manifest, frozen)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    error = case["error_field"]
    env = LocalSimulator([Source(**s) for s in case["sources"]], enforce_case_size=True,
        error_field=FixedErrorField(error["seed"], error["mode"], error["correlation_length_m"]))
    client = RobotClient(robot_id="local-team", transport=env, request_prefix=case["case_id"]+"-"+arm,
                         log_path=directory/"requests.jsonl")
    policy = None
    started = time.monotonic()
    try:
        policy = make_policy(client, arm, manifest, frozen, directory/"decisions.jsonl")
        result = policy.run()
    except Exception as exc:
        result = {"status": "harness_error", "error": f"{type(exc).__name__}: {exc}",
                  "total_virtual_time_s": client.virtual_time, "program_real_time_s": time.monotonic()-started}
    finally:
        client.close_log()
    events = policy.decisions if policy else []
    truth = env.summary()  # External evaluator: never given to policy.
    locations = {s["channel"]: s["position"] for s in case["sources"]}
    violations = []
    for event in events:
        knowledge = event.get("knowledge")
        if knowledge and knowledge["channel"] in locations and not contains(knowledge["hull"], locations[knowledge["channel"]], tol=1e-4):
            violations.append({"sequence": event["sequence"], "channel": knowledge["channel"]})
    stats = client.stats
    complete = result["status"] == "complete" and truth["all_cleared"] and not result.get("error") and not violations
    result.update({k: case[k] for k in ("case_id", "problem", "pool", "composition_model", "mechanism_id",
        "mechanism_status", "partition", "layout", "radius_mode", "error_mode", "n", "n_directed", "scenario_sha256")})
    result.update(variant=arm, evaluation_complete=bool(complete), environment="local_synthetic",
        manifest_sha256=manifest["manifest_sha256"], source_freeze_sha256=frozen["source_freeze_sha256"],
        candidate_sha256=manifest["candidate_sha256"], policy_core_sha256=core_digest(),
        error_field_sha256=digest(error), world_sha256=world_signature(case), environment_summary=truth,
        walk_distance_m=stats["walk_distance"], measures=stats["measures"], switches=stats["switches"],
        clear_attempts=stats["clear_attempts"], clear_successes=stats["successes"],
        failed_clear_attempts=stats["failures"], failed_clear_cost_s=3.*stats["failures"],
        movement_s=stats["walk_distance"]/5., measurement_s=5.*stats["measures"], switch_s=float(stats["switches"]),
        optical_s=3.*stats["clear_attempts"], laser_s=2.*stats["successes"],
        hull_invariant_violations=violations, fallback_targets=(policy.stats.get("fallback_targets") if policy else None),
        failure_penalized_time_s=result["total_virtual_time_s"] if complete else 360000.,
        public_client_stats=stats, evidence_role="local_paired_refinement")
    result.update(stopping_tail(events, case["n"], result["total_virtual_time_s"], truth["all_cleared"]))
    result.update(coverage_point_count=len(policy.points) if policy else None,
        coverage_points_sha256=digest(policy.points) if policy else None,
        coverage_certificate=(frozen["refinement"]["coverage_certificate"] if arm in ("coverage", "combined_cover")
            else {"construction": "original_frozen_triangular", "spacing_m": 950.}),
        coverage_audit_scope="Use actual ordered start/stop evidence points and their SHA; config.coverage retains constructor-compatible legacy label.")
    result["artifact_sha256"] = {name+".gz": compress_log(directory/name) for name in ("requests.jsonl", "decisions.jsonl")}
    result = json.loads(canonical(result))  # Identical return/resume representation, including tuple fields.
    write_json(directory/"result.json", result)
    return result


def run_case(case, arms, manifest, frozen, output, resume):
    arms = list(arms)
    random.Random(seed_for(case["seed"], "p4_refinement_arm_order")).shuffle(arms)
    rows = []
    for arm in arms:
        folder = Path(output)/"runs"/case["pool"]/case["case_id"]/arm
        path = folder/"result.json"
        if path.exists():
            if not resume:
                raise FileExistsError("Existing arm requires resume")
            row = read(path)
            for k, value in {"variant": arm, "manifest_sha256": manifest["manifest_sha256"],
                    "source_freeze_sha256": frozen["source_freeze_sha256"],
                    "scenario_sha256": case["scenario_sha256"]}.items():
                if row.get(k) != value:
                    raise ValueError("Resume provenance mismatch")
            if set(row.get("artifact_sha256", {})) != {"requests.jsonl.gz", "decisions.jsonl.gz"}:
                raise ValueError("Missing saved logs")
            for name, expected in row["artifact_sha256"].items():
                if sha(folder/name) != expected:
                    raise ValueError("Saved log hash changed")
        else:
            row = run_arm(case, arm, manifest, frozen, folder)
        rows.append(row)
    return rows


def quantile(values, q):
    values = sorted(values)
    if not values:
        return None
    p = q*(len(values)-1)
    lo, hi = math.floor(p), math.ceil(p)
    return values[lo]*(hi-p)+values[hi]*(p-lo) if lo != hi else values[lo]


def descriptive_summary(rows):
    groups = defaultdict(list)
    for row in rows:
        for model, mechanism in (("__pool_design__", "__design_mixture__"),
                (row["composition_model"], "__design_mixture__"), (row["composition_model"], row["mechanism_id"])):
            groups[row["partition"], row["pool"], model, mechanism, row["variant"]].append(row)
    report = []
    fields = ("total_virtual_time_s", "walk_distance_m", "measures", "switches", "failed_clear_attempts",
        "failed_clear_cost_s", "fallback_targets", "post_clear_stop_tail_s", "post_clear_measurements",
        "movement_s", "measurement_s", "optical_s", "laser_s", "program_real_time_s")
    for key, group in sorted(groups.items()):
        item = dict(zip(("partition", "pool", "composition_model", "mechanism_id", "variant"), key))
        item.update(n=len(group), complete=sum(r["evaluation_complete"] for r in group),
            failures=sum(not r["evaluation_complete"] for r in group),
            hull_violations=sum(len(r["hull_invariant_violations"]) for r in group),
            penalized_time_mean_s=statistics.fmean(r["failure_penalized_time_s"] for r in group),
            interpretation="Synthetic design; no pooling across pools; time/tail quantiles conditional on completion.")
        for field in fields:
            vals = [r[field] for r in group if r.get(field) is not None and
                    (field not in ("total_virtual_time_s", "post_clear_stop_tail_s") or r["evaluation_complete"])]
            item.update({field+"_n": len(vals), field+"_mean": statistics.fmean(vals) if vals else None,
                         field+"_median": statistics.median(vals) if vals else None,
                         field+"_p90": quantile(vals, .90), field+"_p95": quantile(vals, .95),
                         field+"_max": max(vals) if vals else None})
        report.append(item)
    return report


def summarize(rows, output):
    output = Path(output)
    pairs, grouped = compare_paired(rows, baseline="current")
    index = {(r["case_id"], r["variant"]): r for r in rows}
    for pair in pairs:
        a, b = index[pair["case_id"], "current"], index[pair["case_id"], pair["variant"]]
        for field in ("walk_distance_m", "measures", "failed_clear_attempts", "failed_clear_cost_s",
                      "fallback_targets", "movement_s", "measurement_s", "switch_s", "optical_s", "laser_s", "post_clear_stop_tail_s"):
            pair[field+"_delta"] = b[field]-a[field] if b.get(field) is not None and a.get(field) is not None else None
    descriptive = descriptive_summary(rows)
    pool_groups = defaultdict(list)
    for pair in pairs:
        pool_groups[pair["partition"], pair["pool"], pair["variant"]].append(pair)
    pool_summary = []
    for key, values in sorted(pool_groups.items()):
        penalties = [r["penalized_time_delta_s"] for r in values]
        differences = [r["completed_time_delta_s"] for r in values if r["both_complete"]]
        ci = bootstrap_mean_ci(penalties, seed=seed_for(9347, *key), draws=2000)
        item = dict(zip(("partition", "pool", "variant"), key))
        item.update(n_pairs=len(values), baseline_failures=sum(not r["baseline_complete"] for r in values),
            candidate_failures=sum(not r["candidate_complete"] for r in values),
            penalized_delta_mean_s=statistics.fmean(penalties), penalized_delta_ci95_s=ci,
            completed_delta_mean_s=statistics.fmean(differences) if differences else None,
            completed_delta_p90_s=quantile(differences, .90), completed_delta_p95_s=quantile(differences, .95),
            completed_worst_regression_s=max(differences) if differences else None,
            completed_regression_rate=sum(d > 0 for d in differences)/len(differences) if differences else None,
            interpretation="Pointwise paired CI for this pool's frozen design mixture; not an official population.")
        for field in ("walk_distance_m", "measures", "failed_clear_attempts", "failed_clear_cost_s",
                      "fallback_targets", "movement_s", "measurement_s", "switch_s", "optical_s", "laser_s", "post_clear_stop_tail_s"):
            vals = [r[field+"_delta"] for r in values if r.get(field+"_delta") is not None]
            item[field+"_delta_mean"] = statistics.fmean(vals) if vals else None
        pool_summary.append(item)
    for name, data in (("results", rows), ("paired_cases", pairs), ("paired_summary", grouped),
                       ("pool_paired_summary", pool_summary), ("descriptive_summary", descriptive),
                       ("failures", [r for r in rows if not r["evaluation_complete"]])):
        write_json(output/(name+".json"), data)
        write_csv(output/(name+".csv"), data)
    return {"runs": len(rows), "complete": sum(r["evaluation_complete"] for r in rows),
            "failed": sum(not r["evaluation_complete"] for r in rows),
            "hull_violations": sum(len(r["hull_invariant_violations"]) for r in rows)}


def choose_selection(manifest, frozen, rows):
    expected = {(c["case_id"], a) for c in manifest["scenarios"] if c["partition"] == "development" for a in manifest["arms"]}
    observed = [(r["case_id"], r["variant"]) for r in rows]
    if len(set(observed)) != len(observed) or set(observed) != expected or any(r["partition"] != "development" for r in rows):
        raise ValueError("Selection requires exactly all development arms and no confirmation")
    for row in rows:
        if row.get("manifest_sha256") != manifest["manifest_sha256"] or row.get("source_freeze_sha256") != frozen["source_freeze_sha256"]:
            raise ValueError("Selection input provenance mismatch")
    index = {(r["case_id"], r["variant"]): r for r in rows}
    assessments = []
    for arm in manifest["arms"]:
        if arm == "current":
            continue
        candidate = [r for r in rows if r["variant"] == arm]
        pool_delta = {}
        for pool in POOLS:
            group = [r for r in candidate if r["pool"] == pool]
            pool_delta[pool] = statistics.fmean(r["failure_penalized_time_s"]-index[r["case_id"], "current"]["failure_penalized_time_s"] for r in group) if group else None
        eligible = (bool(candidate) and all(r["evaluation_complete"] and not r["hull_invariant_violations"] for r in candidate)
                    and all(d is not None and d <= 0 for d in pool_delta.values()))
        calibrated = [r["total_virtual_time_s"] for r in candidate if r["pool"] == "calibrated"]
        assessments.append({"variant": arm, "eligible": eligible, "pool_penalized_delta_s": pool_delta,
            "calibrated_mean_s": statistics.fmean(calibrated) if calibrated else None})
    eligible = [r for r in assessments if r["eligible"]]
    eligible.sort(key=lambda r: (r["calibrated_mean_s"], manifest["arms"].index(r["variant"])))
    chosen = [eligible[0]["variant"]] if eligible else []
    if chosen and chosen[0] != "coverage" and any(r["variant"] == "coverage" for r in eligible):
        chosen.append("coverage")
    selection = {"manifest_sha256": manifest["manifest_sha256"], "source_freeze_sha256": frozen["source_freeze_sha256"],
        "development_results_sha256": digest(rows), "rule": manifest["selection_rule"], "assessments": assessments,
        "selected_variants": chosen, "status": "selected_from_development" if chosen else "no_eligible_candidate",
        "confirmation_used": False}
    selection["selection_sha256"] = digest(selection)
    return selection


def execute(output, *, partition, release, selection=None, workers=8, resume=False):
    output = Path(output)
    manifest, frozen = read(output/"manifest.json"), read(output/"source_freeze.json")
    validate_freeze(manifest, frozen)
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be positive")
    arms = selected_arms(manifest, frozen, partition, release, selection)
    cases = [c for c in manifest["scenarios"] if c["partition"] == partition]
    target = output/partition
    if target.exists() and not resume:
        raise FileExistsError("Existing partition requires resume")
    target.mkdir(parents=True, exist_ok=True)
    write_json(target/"release_used.json", release)
    if selection:
        write_json(target/"selection_used.json", selection)
    started, rows, error = time.monotonic(), [], None
    try:
        if workers == 1:
            for i, case in enumerate(cases):
                rows.extend(run_case(case, arms, manifest, frozen, target, resume))
                print(f"{partition}: {i+1}/{len(cases)} cases", flush=True)
        else:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                pending = [pool.submit(run_case, c, arms, manifest, frozen, target, resume) for c in cases]
                for i, future in enumerate(as_completed(pending)):
                    rows.extend(future.result())
                    if (i+1) % 10 == 0 or i+1 == len(cases):
                        print(f"{partition}: {i+1}/{len(cases)} cases", flush=True)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        rows.sort(key=lambda r: (r["pool"], r["case_id"], r["variant"]))
        write_json(target/"results.json", rows)
        write_json(target/"run_metadata.json", {"manifest_sha256": manifest["manifest_sha256"],
            "source_freeze_sha256": frozen["source_freeze_sha256"], "partition": partition, "arms": arms,
            "expected_worlds": len(cases), "expected_runs": len(cases)*len(arms), "recorded_runs": len(rows),
            "wall_time_s": time.monotonic()-started, "workers": workers, "error": error,
            "partial_files_preserved": True, "official_actions": False})
    validate_freeze(manifest, frozen)
    return summarize(rows, target)


def sizes(text):
    parsed = tuple(int(v) for v in text.split(","))
    if len(parsed) != 3 or any(v < 0 for v in parsed):
        raise argparse.ArgumentTypeError("Use three nonnegative comma-separated pool counts")
    return parsed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--candidate", type=Path, default=ROOT/"results/round2/candidate.json")
    p.add_argument("--calibration", type=Path, default=ROOT/"results/calibration/round1/model_frozen.json")
    p.add_argument("--mechanisms", type=Path, default=ROOT/"results/round1/mechanisms_selected.json")
    p.add_argument("--development-sizes", type=sizes, default=(80, 80, 40))
    p.add_argument("--confirmation-sizes", type=sizes, default=(320, 320, 160))
    p.add_argument("--development-seed", type=int, default=2026092101)
    p.add_argument("--confirmation-seed", type=int, default=2026092201)
    p.add_argument("--include-adaptive", action="store_true")
    for name in ("freeze", "run", "select", "summarize"):
        p = commands.add_parser(name)
        p.add_argument("--round", type=Path, required=True)
        if name in ("run", "summarize"):
            p.add_argument("--partition", choices=("development", "confirmation"), required=True)
        if name == "run":
            p.add_argument("--release", type=Path, required=True)
            p.add_argument("--selection", type=Path)
            p.add_argument("--workers", type=int, default=8)
            p.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "prepare":
        result = prepare(args.output, args.candidate, args.calibration, args.mechanisms,
            development_sizes=args.development_sizes, confirmation_sizes=args.confirmation_sizes,
            development_seed=args.development_seed, confirmation_seed=args.confirmation_seed,
            include_adaptive=args.include_adaptive)
        result = {k: result[k] for k in ("manifest_sha256", "distinct_scenarios", "planned_development_runs", "arms")}
    elif args.command == "freeze":
        result = freeze(args.round)
        result = {"source_freeze_sha256": result["source_freeze_sha256"]}
    elif args.command == "run":
        result = execute(args.round, partition=args.partition, release=read(args.release),
            selection=read(args.selection) if args.selection else None, workers=args.workers, resume=args.resume)
    elif args.command == "select":
        path = args.round/"selection.json"
        if path.exists():
            raise FileExistsError("Preserve frozen selection")
        result = choose_selection(read(args.round/"manifest.json"), read(args.round/"source_freeze.json"),
                                  read(args.round/"development/results.json"))
        write_json(path, result)
    else:
        result = summarize(read(args.round/args.partition/"results.json"), args.round/args.partition)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
