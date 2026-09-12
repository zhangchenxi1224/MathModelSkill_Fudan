"""Predeclared local paired experiments. No official endpoint is available here.

The policy core is frozen independently of new experiment/collection modules.
Hidden source data is passed only to LocalSimulator and the post-run evaluator.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import time

from .geometry import contains
from .protocol import RobotClient
from .simulator import FixedErrorField, LocalSimulator, Source
from .strategy import Solver, SolverConfig

FROZEN_CORE_SHA256 = "1b673484453104a5d87d4605d30b20826a3b7ec64769fd6ce8d3c8f7a1ad24f6"
CORE_FILES = ["__init__.py", "cli.py", "coverage.py", "experiments.py", "geometry.py",
              "knowledge.py", "protocol.py", "sensing.py", "simulator.py", "strategy.py"]
BASELINE = "joint_triangular_l5"
STAGE1_LIMITS = {BASELINE: 5, "joint_triangular_l1": 1,
                 "joint_triangular_l2": 2, "joint_triangular_l3": 3}
FAILURE_PENALTY_S = 360000.0
POOLS = ("calibrated", "broad", "stress")
ERROR_MODES = ("deterministic", "correlated", "extreme")
RADIUS_MODES = ("min", "max", "mixed")
STRESS_LAYOUTS = ("boundary_outward", "boundary_tangent", "hidden_outward_last",
                  "center_and_boundary", "clustered", "near_lattice_vertices")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def seed_for(seed, *parts):
    return int.from_bytes(hashlib.blake2b(canonical([seed, *parts]).encode(), digest_size=8).digest(), "big")


def core_digest():
    h = hashlib.sha256()
    for name in CORE_FILES:
        h.update(name.encode())
        h.update((Path(__file__).parent / name).read_bytes())
    return h.hexdigest()


def runner_digest():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def assert_frozen_core():
    observed = core_digest()
    if observed != FROZEN_CORE_SHA256:
        raise ValueError(f"Frozen policy core changed: expected {FROZEN_CORE_SHA256}, got {observed}")


def frozen_baseline(problem):
    """Spell out every field; later default changes must not silently alter baseline."""
    return SolverConfig(problem=problem, sensing="active", scheduling="joint", coverage="triangular",
        spacing=None, epsilon_deg=1.0051, clear_radius=19.999, local_measure_limit=5,
        joint_detour_m=800., bin_width_deg=4., radius_weight=2., reserve_real_s=5.,
        max_virtual_s=FAILURE_PENALTY_S, nearest_safe=True, opportunistic_known_measurements=False)


def stage1_configs(problem):
    if problem not in (3, 4):
        raise ValueError("problem must be 3 or 4")
    return {name: replace(frozen_baseline(problem), local_measure_limit=limit)
            for name, limit in STAGE1_LIMITS.items()}


def validate_distribution(rows, problem):
    if not rows:
        raise ValueError(f"No fitted count distribution for P{problem}")
    seen, total, result = set(), 0., []
    for row in rows:
        n, nd, probability = row.get("n"), row.get("n_directed"), row.get("probability")
        if (isinstance(n, bool) or not isinstance(n, int) or not 10 <= n <= 16
                or isinstance(nd, bool) or not isinstance(nd, int) or not 0 <= nd <= n
                or (problem == 3 and nd != 0)):
            raise ValueError("Invalid joint (N,N_directed) support")
        if isinstance(probability, bool) or not isinstance(probability, (int, float)) or not math.isfinite(probability) or probability < 0:
            raise ValueError("Invalid probability")
        if (n, nd) in seen:
            raise ValueError("Duplicate joint support point")
        seen.add((n, nd))
        total += probability
        result.append({"n": n, "n_directed": nd, "probability": float(probability)})
    if not math.isclose(total, 1., rel_tol=0, abs_tol=1e-6):
        raise ValueError(f"Count probabilities must sum to one, got {total}")
    return result


def model_distribution(calibration, problem, model, *, allow_exploratory=False):
    if not calibration or calibration.get("schema_version") != 1:
        raise ValueError("A real calibration model with schema_version=1 is required")
    if calibration.get("model_status") == "exploratory" and not allow_exploratory:
        raise ValueError("Exploratory practice calibration cannot launch the planned round")
    block = calibration.get("problems", {}).get(str(problem), {})
    if block.get("n_fit_known_joint", 0) <= 0:
        raise ValueError(f"P{problem} has no official cases with known joint composition")
    candidates = block.get("composition_candidates", {})
    entry = candidates.get(model)
    if entry is None and model == "smoothed_joint":
        rows = block.get("count_distribution", [])
    elif isinstance(entry, dict):
        if entry.get("status") == "unavailable":
            raise ValueError(f"Calibration model {model} unavailable for P{problem}")
        rows = entry.get("count_distribution", [])
    else:
        raise ValueError(f"Calibration model {model} missing for P{problem}")
    return validate_distribution(rows, problem)


def categorical(rng, rows):
    value = rng.random()
    for row in rows:
        value -= row["probability"]
        if value < 0:
            return row["n"], row["n_directed"]
    return rows[-1]["n"], rows[-1]["n_directed"]


def validate_mechanisms(candidates):
    if candidates is None:
        return None
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("Mechanism candidates must be a nonempty preregistered list")
    result, seen = [], set()
    allowed = {"retained", "matched", "unresolved", "insufficient_evidence", "not_rejected"}
    for raw in candidates:
        item = dict(raw)
        identity = item.get("id")
        if not isinstance(identity, str) or not identity.strip() or identity in seen:
            raise ValueError("Each mechanism needs a distinct nonempty id")
        if item.get("status") not in allowed:
            raise ValueError("Biased/screened/unlabelled mechanisms cannot enter the calibrated pool")
        radius = "mixed" if item.get("radius_mode") == "uniform" else item.get("radius_mode")
        error = "deterministic" if item.get("error_mode") == "hash" else item.get("error_mode")
        scale = item.get("correlation_length", item.get("correlation_length_m", 150.))
        if (radius not in RADIUS_MODES or error not in ERROR_MODES or isinstance(scale, bool)
                or not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0):
            raise ValueError("Invalid mechanism radius/noise/correlation parameters")
        item.update(radius_mode=radius, error_mode=error, correlation_length=float(scale))
        result.append(item)
        seen.add(identity)
    return result


def make_scenario(seed, problem, n, nd, *, pool="broad", model="broad_joint",
                  layout="random", radius_mode="mixed", error_mode="deterministic", index=0,
                  correlation_length=150., partition="development", mechanism_id=None,
                  mechanism_status=None):
    if problem not in (3, 4) or not 10 <= n <= 16 or not 0 <= nd <= n or (problem == 3 and nd):
        raise ValueError("Invalid contest composition")
    if radius_mode not in RADIUS_MODES or error_mode not in ERROR_MODES:
        raise ValueError("Unknown radius/noise stratum")
    rng = random.Random(seed)
    channels = rng.sample(range(1, 21), n)
    directed = set(rng.sample(range(n), nd))
    if layout == "hidden_outward_last" and nd:
        directed.discard(n - 1)
        directed = set(sorted(directed)[:nd - 1]) | {n - 1}
    sources = []
    for i, channel in enumerate(channels):
        angle = rng.uniform(0, 2 * math.pi)
        radius = 1800 * math.sqrt(rng.random())
        if layout in ("boundary_outward", "boundary_tangent", "center_and_boundary"):
            radius, angle = 1800., (2 * math.pi * i / n + (seed % 360) * math.pi / 180)
            if layout == "center_and_boundary" and i == 0:
                radius = 0.
        elif layout in ("clustered", "hidden_outward_last"):
            radius, angle = rng.uniform(0, 350), rng.uniform(0, 2 * math.pi)
            if layout == "hidden_outward_last" and i == n - 1:
                radius, angle = 1800., 0.
        elif layout == "near_lattice_vertices":
            # Include source-at-station and near-degenerate geometry; all are in D.
            p = [(0., 0.), (950., 0.), (-950., 0.), (475., 950 * math.sqrt(3) / 2)][i % 4]
            jitter = 0. if i == 0 else 1e-7 if i == 1 else 4.999 if i == 2 else 5.001
            x, y = p[0] + jitter * math.cos(angle), p[1] + jitter * math.sin(angle)
        elif layout != "random":
            raise ValueError("Unknown layout")
        if layout != "near_lattice_vertices":
            x, y = radius * math.cos(angle), radius * math.sin(angle)
        reception = 1000. if radius_mode == "min" else 1500. if radius_mode == "max" else rng.uniform(1000, 1500)
        direction = None
        if i in directed:
            direction = rng.uniform(0, 360)
            if layout in ("boundary_outward", "hidden_outward_last"):
                direction = math.degrees(angle) % 360
            elif layout == "boundary_tangent":
                direction = (math.degrees(angle) + 90) % 360
        sources.append({"channel": channel, "position": [x, y], "radius": reception, "direction_deg": direction})
    case = {"schema_version": 1, "case_id": f"round-{pool}-p{problem}-{index:04d}-{seed:016x}",
            "seed": seed, "problem": problem, "pool": pool, "composition_model": model,
            "n": n, "n_directed": nd, "layout": layout, "radius_mode": radius_mode,
            "error_mode": error_mode, "partition": partition,
            "mechanism_id": mechanism_id or f"assumption__radius_{radius_mode}__noise_{error_mode}__scale_{correlation_length:g}",
            "mechanism_status": mechanism_status or ("composition_only_assumptions" if pool == "calibrated" else "broad_or_stress_assumption"),
            "error_field": {"seed": seed_for(seed, "fixed_error_field"), "mode": error_mode,
                            "correlation_length_m": float(correlation_length), "definition": "FixedErrorField-v1"},
            "sources": sources}
    # Validate physics before executing any policy.
    for source in sources:
        Source(**source)
    assert sum(s["direction_deg"] is not None for s in sources) == nd
    case["scenario_sha256"] = digest(case)
    return case


def generate_manifest(calibration=None, *, sizes=(400, 400, 200), master_seed=2026091101,
                      calibrated_models=("empirical_joint", "smoothed_joint"), mechanism_candidates=None):
    if len(sizes) != 3 or any(isinstance(n, bool) or not isinstance(n, int) or n < 0 or n % 2 for n in sizes):
        raise ValueError("Pool sizes must be three nonnegative even integers")
    if not calibrated_models or len(set(calibrated_models)) != len(calibrated_models):
        raise ValueError("Calibrated model names must be nonempty and distinct")
    mechanisms = validate_mechanisms(mechanism_candidates)
    distributions = {(p, m): model_distribution(calibration, p, m)
                     for p in (3, 4) for m in calibrated_models} if sizes[0] else {}
    cases = []
    for pool, size in zip(POOLS, sizes):
        for problem in (3, 4):
            legal = [(n, nd) for n in range(10, 17) for nd in (range(n + 1) if problem == 4 else [0])]
            rng_order = random.Random(seed_for(master_seed, pool, problem, "support_order"))
            rng_order.shuffle(legal)
            for i in range(size // 2):
                seed = seed_for(master_seed, pool, problem, i)
                if pool == "calibrated":
                    model = calibrated_models[i % len(calibrated_models)]
                    n, nd = categorical(random.Random(seed_for(seed, "count")), distributions[problem, model])
                else:
                    model = "broad_joint" if pool == "broad" else "stress_design"
                    n, nd = legal[i % len(legal)]
                layout = STRESS_LAYOUTS[i % len(STRESS_LAYOUTS)] if pool == "stress" else "random"
                radius_mode, error_mode = RADIUS_MODES[(i // 3) % 3], ERROR_MODES[i % 3]
                correlation_length = [75., 150., 300.][(i // 9) % 3]
                mechanism = mechanisms[(i // len(calibrated_models)) % len(mechanisms)] if pool == "calibrated" and mechanisms else None
                if mechanism:
                    radius_mode, error_mode = mechanism["radius_mode"], mechanism["error_mode"]
                    correlation_length = mechanism["correlation_length"]
                case = make_scenario(seed, problem, n, nd, pool=pool, model=model, layout=layout,
                    radius_mode=radius_mode, error_mode=error_mode, index=i,
                    correlation_length=correlation_length, mechanism_id=mechanism["id"] if mechanism else None,
                    mechanism_status=mechanism["status"] if mechanism else None)
                cases.append(case)
    # Independent hash ranking avoids aliasing a five-mechanism cycle with a
    # periodic every-fifth-case confirmation rule. Allocation precedes outcomes.
    partition_groups = defaultdict(list)
    for case in cases:
        partition_groups[case["pool"], case["problem"], case["composition_model"]].append(case)
    for group in partition_groups.values():
        ordered = sorted(group, key=lambda c: seed_for(master_seed, "confirmation_allocation", c["case_id"]))
        for rank, case in enumerate(ordered):
            case["partition"] = "confirmation" if rank < len(group) // 5 else "development"
            case.pop("scenario_sha256")
            case["scenario_sha256"] = digest(case)
    if len({c["scenario_sha256"] for c in cases}) != len(cases):
        raise ValueError("Duplicate generated scenarios")
    manifest = {"schema_version": 1, "stage": 1, "master_seed": master_seed, "pool_sizes": dict(zip(POOLS, sizes)),
        "distinct_scenarios": len(cases), "planned_strategy_runs": len(cases) * len(STAGE1_LIMITS),
        "baseline": BASELINE, "frozen_core_sha256": FROZEN_CORE_SHA256,
        "calibration_model_id": calibration.get("model_id") if calibration else None,
        "calibration_model_sha256": digest(calibration) if calibration else None,
        "calibration_model_status": calibration.get("model_status") if calibration else None,
        "calibration_provenance": calibration.get("provenance") if calibration else None,
        "calibrated_models": list(calibrated_models) if sizes[0] else [],
        "mechanism_candidates": mechanisms,
        "calibrated_pool_mechanism_scope": "preregistered_retained_and_unresolved_candidates" if mechanisms else "composition_only_assumptions",
        "partition_rule": "20% independent hash rank within pool/problem/composition; floor for small strata",
        "environment_scope": "composition calibration only; radius, noise, positions and orientations remain synthetic assumptions",
        "official_formal_authorized": False, "scenarios": cases}
    manifest["manifest_sha256"] = digest(manifest)
    return manifest


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def write_csv(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: canonical(v) if isinstance(v, (dict, list)) else v for k, v in row.items()})


def validate_manifest(manifest):
    copy = dict(manifest)
    claimed = copy.pop("manifest_sha256", None)
    if digest(copy) != claimed:
        raise ValueError("Manifest was modified after hashing")
    if manifest["frozen_core_sha256"] != FROZEN_CORE_SHA256:
        raise ValueError("Manifest baseline version mismatch")
    identities = set()
    for case in manifest["scenarios"]:
        obj = dict(case)
        expected = obj.pop("scenario_sha256", None)
        if digest(obj) != expected or case["case_id"] in identities:
            raise ValueError("Modified or duplicate scenario")
        identities.add(case["case_id"])


def run_scenario(case, variant, output=None, logs=True):
    assert_frozen_core()
    config = stage1_configs(case["problem"])[variant]
    directory = Path(output) if output else None
    if directory:
        directory.mkdir(parents=True, exist_ok=True)
    specification = case["error_field"]
    # Independent instance, identical immutable field definition for every arm.
    field = FixedErrorField(specification["seed"], specification["mode"], specification["correlation_length_m"])
    environment = LocalSimulator([Source(**source) for source in case["sources"]], error_field=field,
                                 robot_id="local-team", enforce_case_size=True)
    client = RobotClient(robot_id="local-team", transport=environment,
                         log_path=str(directory / "requests.jsonl") if directory and logs else None)
    policy = Solver(client, config)
    try:
        result = policy.run()
    finally:
        client.close_log()
    truth = environment.summary()
    sources = {s["channel"]: s["position"] for s in case["sources"]}
    violations = []
    for event in policy.decisions:
        knowledge = event.get("knowledge")
        if knowledge and knowledge["channel"] in sources and not contains(knowledge["hull"], sources[knowledge["channel"]], tol=1e-4):
            violations.append({"sequence": event["sequence"], "channel": knowledge["channel"]})
    complete = result["status"] == "complete" and truth["all_cleared"] and not result["error"] and not violations
    result.update({"variant": variant, "case_id": case["case_id"], "scenario_sha256": case["scenario_sha256"],
        "pool": case["pool"], "composition_model": case["composition_model"], "partition": case["partition"],
        "mechanism_id": case["mechanism_id"], "mechanism_status": case["mechanism_status"],
        "layout": case["layout"], "radius_mode": case["radius_mode"], "error_mode": case["error_mode"],
        "n": case["n"], "n_directed": case["n_directed"], "source_total": case["n"],
        "clear_fraction": truth["clearance_ratio"], "environment_summary": truth,
        "hull_invariant_violations": violations, "evaluation_complete": complete,
        "failure_penalized_time_s": result["total_virtual_time_s"] if complete else FAILURE_PENALTY_S,
        "policy_core_sha256": core_digest(), "runner_sha256": runner_digest(),
        "environment": "self_built_fixed_scenario", "error_field_sha256": digest(specification)})
    if directory:
        write_json(directory / "result.json", result)
        if logs:
            with gzip.open(directory / "decisions.jsonl.gz", "wt", encoding="utf-8") as fp:
                for event in policy.decisions:
                    fp.write(canonical(event) + "\n")
            request_path = directory / "requests.jsonl"
            if request_path.exists():
                with gzip.open(directory / "requests.jsonl.gz", "wb") as fp:
                    fp.write(request_path.read_bytes())
                request_path.unlink()  # Only this run's generated, now archived log.
    return result


def _run_case_arms(case, output, resume, logs):
    results = []
    variants = list(STAGE1_LIMITS)
    random.Random(seed_for(case["seed"], "arm_order")).shuffle(variants)
    for variant in variants:
        directory = Path(output) / "runs" / case["pool"] / case["case_id"] / variant
        path = directory / "result.json"
        if path.exists():
            if not resume:
                raise ValueError(f"Existing run requires explicit --resume: {path}")
            row = json.loads(path.read_text(encoding="utf-8"))
            if any(row.get(k) != v for k, v in {"scenario_sha256": case["scenario_sha256"],
                    "variant": variant, "policy_core_sha256": FROZEN_CORE_SHA256,
                    "runner_sha256": runner_digest(), "config": asdict(stage1_configs(case["problem"])[variant])}.items()):
                raise ValueError(f"Incompatible stored result: {path}")
        else:
            row = run_scenario(case, variant, directory, logs)
        results.append(row)
    return results


def run_manifest(manifest, output, *, workers=1, resume=False, logs=True, max_cases=None):
    assert_frozen_core()
    validate_manifest(manifest)
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
    if max_cases is not None and (isinstance(max_cases, bool) or not isinstance(max_cases, int) or max_cases < 1):
        raise ValueError("max_cases must be a positive integer or None")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text(encoding="utf-8"))["manifest_sha256"] != manifest["manifest_sha256"]:
        raise ValueError("Output already contains a different manifest")
    write_json(manifest_path, manifest)
    cases = manifest["scenarios"][:max_cases] if max_cases is not None else manifest["scenarios"]
    started = time.monotonic()
    results = []
    def accept(rows):
        results.extend(rows)
        print(canonical({"completed_strategy_runs": len(results), "planned_strategy_runs": 4 * len(cases),
              "case_id": rows[0]["case_id"], "case_complete_arms": sum(r["evaluation_complete"] for r in rows)}), flush=True)
    if workers == 1:
        for case in cases:
            accept(_run_case_arms(case, output, resume, logs))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            jobs = [pool.submit(_run_case_arms, case, str(output), resume, logs) for case in cases]
            for job in as_completed(jobs):
                accept(job.result())
    results.sort(key=lambda r: (r["pool"], r["case_id"], r["variant"]))
    write_json(output / "results.json", results)
    write_csv(output / "results.csv", [{k: v for k, v in row.items() if k not in ("stop_evidence", "environment_summary")} for row in results])
    timing = {"wall_time_s": time.monotonic() - started, "strategy_runtime_sum_s": sum(r["program_real_time_s"] for r in results),
              "distinct_scenarios_run": len(cases), "strategy_runs": len(results), "workers": workers,
              "partial_manifest": len(cases) != len(manifest["scenarios"]), "logs": "gzip_full" if logs else "none",
              "runner_sha256": runner_digest(), "policy_core_sha256": core_digest()}
    write_json(output / "run_metadata.json", timing)
    return results, timing


def bootstrap_mean_ci(values, *, seed=1701, draws=2000, confidence=.95):
    if not values:
        return [None, None]
    if len(values) == 1:
        return [None, None]  # one case cannot estimate sampling uncertainty
    try:
        import numpy as np
        samples = np.asarray(values, dtype=float)
        rng = np.random.default_rng(seed)
        means = samples[rng.integers(0, len(values), size=(draws, len(values)))].mean(axis=1)
        return np.quantile(means, [(1 - confidence) / 2, (1 + confidence) / 2]).tolist()
    except ImportError:
        rng = random.Random(seed)
        means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(draws))
        return [means[int(draws * (1 - confidence) / 2)], means[min(draws - 1, int(draws * (1 + confidence) / 2))]]


def compare_paired(results, *, bootstrap_draws=2000, baseline=BASELINE):
    index = {}
    for row in results:
        key = row["case_id"], row["variant"]
        if key in index:
            raise ValueError("Duplicate case/arm would distort paired results")
        index[key] = row
    pairs = []
    for row in results:
        if row["variant"] == baseline:
            continue
        base = index.get((row["case_id"], baseline))
        if base is None:
            raise ValueError(f"Missing paired baseline for {row['case_id']}")
        for key in ["scenario_sha256", "error_field_sha256", "policy_core_sha256"]:
            if row[key] != base[key]:
                raise ValueError("Unpaired environments or policy versions")
        both = row["evaluation_complete"] and base["evaluation_complete"]
        pairs.append({k: row[k] for k in ["case_id", "problem", "pool", "composition_model", "partition", "error_mode", "variant"]} | {
            "mechanism_id": row.get("mechanism_id", "unspecified"),
            "mechanism_status": row.get("mechanism_status", "unspecified"),
            "baseline_complete": base["evaluation_complete"], "candidate_complete": row["evaluation_complete"],
            "baseline_variant": baseline,
            "both_complete": both, "completion_delta": int(row["evaluation_complete"]) - int(base["evaluation_complete"]),
            "penalized_time_delta_s": row["failure_penalized_time_s"] - base["failure_penalized_time_s"],
            "completed_time_delta_s": row["total_virtual_time_s"] - base["total_virtual_time_s"] if both else None,
            "completed_reduction_pct": 100 * (base["total_virtual_time_s"] - row["total_virtual_time_s"]) / base["total_virtual_time_s"] if both else None,
            "baseline_total_virtual_time_s": base["total_virtual_time_s"], "candidate_total_virtual_time_s": row["total_virtual_time_s"]})
    groups = defaultdict(list)
    for row in pairs:
        for partition in ("all", row["partition"]):
            for noise in ("all", row["error_mode"]):
                for mechanism in ("__design_mixture__", row["mechanism_id"]):
                    groups[row["pool"], row["problem"], row["composition_model"], mechanism, partition, noise, row["variant"]].append(row)
    summaries = []
    for key, group in sorted(groups.items()):
        pool, problem, model, mechanism, partition, noise, variant = key
        penalties = [r["penalized_time_delta_s"] for r in group]
        differences = [r["completed_time_delta_s"] for r in group if r["both_complete"]]
        ci = bootstrap_mean_ci(penalties, seed=seed_for(1701, *key), draws=bootstrap_draws)
        row = {"pool": pool, "problem": problem, "composition_model": model, "partition": partition,
            "mechanism_id": mechanism,
            "mechanism_status": "design_mixture_only_not_official_population" if mechanism == "__design_mixture__" else group[0]["mechanism_status"],
            "noise_model": noise, "variant": variant, "baseline_variant": baseline, "n_pairs": len(group),
            "baseline_failures": sum(not r["baseline_complete"] for r in group),
            "candidate_failures": sum(not r["candidate_complete"] for r in group),
            "completion_delta_mean": statistics.fmean(r["completion_delta"] for r in group),
            "penalized_time_delta_mean_s": statistics.fmean(penalties), "penalized_delta_ci_low_s": ci[0], "penalized_delta_ci_high_s": ci[1],
            "both_complete_n": len(differences), "completed_time_delta_mean_s": statistics.fmean(differences) if differences else None,
            "completed_time_delta_median_s": statistics.median(differences) if differences else None,
            "completed_regression_rate": sum(d > 0 for d in differences) / len(differences) if differences else None,
            "completed_worst_regression_s": max(differences) if differences else None,
            "mean_completed_reduction_pct": statistics.fmean(r["completed_reduction_pct"] for r in group if r["both_complete"]) if differences else None,
            "ci_scope": "pointwise paired bootstrap; exploratory and not multiplicity-adjusted",
            "precision_warning": "very small stratum" if len(group) < 20 else None}
        completed_ci = bootstrap_mean_ci(differences, seed=seed_for(1702, *key), draws=bootstrap_draws)
        row.update({"completed_delta_ci_low_s": completed_ci[0], "completed_delta_ci_high_s": completed_ci[1]})
        summaries.append(row)
    return pairs, summaries


def screening_shortlist(summaries, maximum=2):
    """At most two exploratory candidates per problem; never authorizes deployment."""
    if maximum not in (1, 2):
        raise ValueError("Screen at most one or two candidates")
    selected = []
    for problem in (3, 4):
        scores = []
        for variant in STAGE1_LIMITS:
            if variant == BASELINE:
                continue
            groups = [r for r in summaries if r["problem"] == problem and r["variant"] == variant
                      and r["partition"] == "development" and r["noise_model"] == "all"
                      and r["mechanism_id"] == "__design_mixture__"]
            mechanism_groups = [r for r in summaries if r["problem"] == problem and r["variant"] == variant
                                and r["partition"] == "development" and r["noise_model"] == "all"
                                and r["mechanism_id"] != "__design_mixture__"]
            if not groups or {r["pool"] for r in groups} != set(POOLS):
                continue
            if any(r["candidate_failures"] > r["baseline_failures"] for r in groups + mechanism_groups):
                continue
            if any(r["penalized_time_delta_mean_s"] >= 0 for r in groups):
                continue
            # Rank by worst group improvement, without pooling different populations.
            scores.append((max(r["penalized_time_delta_mean_s"] for r in groups), variant))
        for score, variant in sorted(scores)[:maximum]:
            selected.append({"problem": problem, "variant": variant, "worst_development_group_delta_s": score,
                             "status": "exploratory_shortlist_only", "official_dispatch_ready": False})
    return selected


def summarize_run(results, output, bootstrap_draws=2000):
    output = Path(output)
    pairs, groups = compare_paired(results, bootstrap_draws=bootstrap_draws)
    shortlist = screening_shortlist(groups)
    write_json(output / "paired_cases.json", pairs)
    write_csv(output / "paired_cases.csv", pairs)
    write_json(output / "paired_summary.json", groups)
    write_csv(output / "paired_summary.csv", groups)
    write_json(output / "screening_shortlist.json", shortlist)
    write_json(output / "failures.json", [r for r in results if not r["evaluation_complete"]])
    lines = ["# 分层本地同场景配对结果", "", "各池、题号、组成模型独立报告；不混成官方平均值。候选只改变局部测量上限。",
        "失败不当作短完成：主指标对失败预注册为360000秒惩罚（这是评价损失，并非补造完成时间），另列完整率差和双成功子集时间。delta=候选−固定L5基线，负值更快。",
        "点态95%配对bootstrap区间仅描述本合成分布，未做多重比较校正；筛选仅用development。confirmation先天留出，不能看完再回调参数。",
        "design_mixture仅表示预注册设计中的加权组合，不能解释为官方总体；每机制独立行完整保留。未给机制列表时calibrated只校准组成，状态为composition_only_assumptions。",
        "", "|池|题号|组成模型|机制|分区|候选|配对n|基线/候选失败|惩罚差均值 s|95% CI s|双成功时间差 s|退步率|",
        "|---|---|---|---|---|---|---:|---:|---:|---|---:|---:|"]
    for r in groups:
        if r["noise_model"] != "all":
            continue
        val = lambda x: "NA" if x is None else f"{x:.2f}"
        lines.append(f"|{r['pool']}|{r['problem']}|{r['composition_model']}|{r['mechanism_id']}|{r['partition']}|{r['variant']}|{r['n_pairs']}|{r['baseline_failures']}/{r['candidate_failures']}|{val(r['penalized_time_delta_mean_s'])}|[{val(r['penalized_delta_ci_low_s'])}, {val(r['penalized_delta_ci_high_s'])}]|{val(r['completed_time_delta_mean_s'])}|{val(r['completed_regression_rate'])}|")
    lines += ["", "噪声模型细分及最坏退步、所有失败见JSON/CSV。shortlist是探索性候选，最多每题2个；正式评测未授权，脚本不包含官方请求能力。", ""]
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    return {"runs": len(results), "paired_rows": len(pairs), "summary_groups": len(groups), "shortlist": shortlist}
