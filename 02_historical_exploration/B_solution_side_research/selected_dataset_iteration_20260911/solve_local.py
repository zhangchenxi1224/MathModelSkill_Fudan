"""Run a frozen selected policy on one supplied synthetic world, entirely offline."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
from pathlib import Path
import sys
import time
import traceback

import experiment


ROOT = Path(__file__).resolve().parent
PORTABLE_RUNTIME = ROOT / "dataset/official_composition_H1_H2_360_20260911/runtime"


def _runtime_valid(runtime, expected):
    required = {"bsolver_frozen/__init__.py", "bsolver_frozen/protocol.py",
                "bsolver_frozen/simulator.py"}
    if not isinstance(expected, dict) or not required <= set(expected):
        raise ValueError("registered simulator file hashes are incomplete")
    root = Path(runtime).resolve()
    for name, digest in expected.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("registered simulator file is missing or escapes runtime")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("registered simulator hash mismatch")
    return True


def load_deployment(selection_path):
    """Verify the immutable selection, its protocol, policy code and simulator."""
    selection_path = Path(selection_path).resolve()
    selection = experiment.read(selection_path)
    protocol = experiment.read(selection_path.parent / "protocol.json")
    if selection.get("selection_sha256") != experiment.digest(
            {k: v for k, v in selection.items() if k != "selection_sha256"}):
        raise ValueError("frozen selection self hash mismatch")
    if selection.get("protocol_sha256") != experiment.digest(protocol):
        raise ValueError("registered protocol hash mismatch")
    if experiment.files() != selection.get("source_files"):
        raise ValueError("current policy runtime differs from frozen source_files")
    # The portable package takes precedence over the historical absolute path.
    runtime = (PORTABLE_RUNTIME if PORTABLE_RUNTIME.is_dir() else
               Path(protocol["dataset_runtime"]))
    runtime = runtime.resolve()
    _runtime_valid(runtime, protocol.get("dataset_runtime_hashes"))
    return selection, protocol, runtime


def load_case(path):
    case = experiment.read(path)
    if not isinstance(case, dict):
        raise ValueError("case must be a JSON object")
    problem = case.get("problem")
    if isinstance(problem, bool) or not isinstance(problem, int) or problem not in (3, 4):
        raise ValueError("case.problem must be integer 3 or 4")
    actual = experiment.digest({k: v for k, v in case.items() if k != "scenario_sha256"})
    if "scenario_sha256" in case and case["scenario_sha256"] != actual:
        raise ValueError("scenario_sha256 mismatch")
    sources = case.get("sources")
    if not isinstance(sources, list) or not 10 <= len(sources) <= 16:
        raise ValueError("case must contain 10 to 16 sources")
    if problem == 3 and any(s.get("direction_deg") is not None for s in sources):
        raise ValueError("problem 3 cannot contain directional sources")
    error = case.get("error_field")
    if not isinstance(error, dict):
        raise ValueError("error_field is required")
    if isinstance(error.get("seed"), bool) or not isinstance(error.get("seed"), int):
        raise ValueError("error_field.seed must be an integer")
    if not isinstance(error.get("mode"), str):
        raise ValueError("error_field.mode is required")
    scale = error.get("correlation_length_m")
    if (isinstance(scale, bool) or not isinstance(scale, (float, int)) or
            not math.isfinite(scale) or scale <= 0):
        raise ValueError("error_field.correlation_length_m must be finite and positive")
    return case, actual


def choose_spec(selection, problem, policy="selected"):
    """The only world attribute used for policy dispatch is the public problem."""
    if policy not in ("selected", "champion"):
        raise ValueError("policy must be selected or champion")
    name = "champion" if policy == "champion" else selection["choices"][str(problem)]
    spec = selection["validation_specs"][name]
    if problem not in spec.get("problems", [3, 4]):
        raise ValueError("frozen policy does not apply to this problem")
    return name, spec


def _load_runtime(runtime):
    sys.path.insert(0, str(ROOT / "vendor"))
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(runtime))
    modules = [importlib.import_module("bsolver_frozen." + name)
               for name in ("protocol", "simulator")]
    # Protect library callers against an earlier import from another runtime.
    for module in modules:
        if not Path(module.__file__).resolve().is_relative_to(runtime):
            raise ValueError("a different frozen simulator is already imported; use a fresh process")
    return modules


def solve_case(case_path, output, *, selection_path=None, policy="selected"):
    """Create a new result directory; never connect to an official/HTTP service."""
    destination = Path(output).resolve()
    if destination.exists():
        raise FileExistsError("output already exists; choose a new directory")
    selection_path = selection_path or ROOT / "campaign/frozen_selection.json"
    selection, protocol, runtime = load_deployment(selection_path)
    case, scenario_hash = load_case(case_path)
    name, spec = choose_spec(selection, case["problem"], policy)
    protocol_module, simulator_module = _load_runtime(runtime)
    from bsolver.geometry import contains
    from methods.solver import make_solver

    definition = case["error_field"]
    # Hidden truth constructs the environment only; the factory receives only
    # client, public problem number, and the already frozen specification.
    environment = simulator_module.LocalSimulator(
        [simulator_module.Source(**source) for source in case["sources"]],
        robot_id="local-team", enforce_case_size=True,
        error_field=simulator_module.FixedErrorField(definition["seed"], definition["mode"],
                                                    definition["correlation_length_m"]))
    destination.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    client = None
    try:
        client = protocol_module.RobotClient("local-team", transport=environment,
                                            log_path=destination / "requests.jsonl")
        solver = make_solver(client, case["problem"], spec,
                             decision_log=destination / "decisions.jsonl")
        result = solver.run()
        truth = environment.summary()
        truth_by_channel = {s["channel"]: s["position"] for s in case["sources"]}
        violations = []
        for event in solver.decisions:
            state = event.get("knowledge")
            if (state and state["channel"] in truth_by_channel and
                    not contains(state["hull"], truth_by_channel[state["channel"]], tol=1e-4)):
                violations.append([event["sequence"], state["channel"]])
        complete = (result["status"] == "complete" and truth["all_cleared"] and
                    not violations and bool(result["stop_evidence"]) and
                    abs(result["timing_residual_s"]) < .001)
        result.update(audit_complete=complete, clear_fraction=truth["clearance_ratio"],
                      environment_summary=truth, hull_invariant_violations=violations)
    except Exception as exc:
        result = {"status": "harness_failed", "error": f"{type(exc).__name__}: {exc}",
                  "traceback": traceback.format_exc(), "audit_complete": False,
                  "total_virtual_time_s": None, "hull_invariant_violations": [],
                  "program_real_time_s": time.monotonic() - started}
    finally:
        if client is not None:
            client.close_log()
    try:
        source_valid = (experiment.files() == selection["source_files"] and
                        _runtime_valid(runtime, protocol["dataset_runtime_hashes"]))
    except (OSError, ValueError):
        source_valid = False
    if not source_valid:
        result.update(status="invalid_source", audit_complete=False,
                      error="policy or simulator changed during execution")
    count = len(case["sources"])
    total = result.get("total_virtual_time_s")
    result.update(policy=policy, variant=name, spec=spec, problem=case["problem"],
                  case_id=case.get("case_id", "external-" + scenario_hash[:16]),
                  scenario_sha256=scenario_hash, supplied_scenario_hash="scenario_sha256" in case,
                  world_sha256=experiment.world_digest(case), source_total=count,
                  total_time_per_source_s=total / count if total is not None else None,
                  selection_sha256=selection["selection_sha256"],
                  protocol_sha256=selection["protocol_sha256"],
                  source_files=selection["source_files"], source_valid=source_valid,
                  dataset_runtime_hashes=protocol["dataset_runtime_hashes"],
                  cli_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  environment="frozen_local_simulator", official_actions=0)
    experiment.write(destination / "result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, help="JSON world with problem, sources and error_field")
    parser.add_argument("--output", required=True, help="New output directory; existing directories are refused")
    parser.add_argument("--selection", default=str(ROOT / "campaign/frozen_selection.json"),
                        help="Frozen selection; protocol.json must be beside it")
    parser.add_argument("--policy", choices=("selected", "champion"), default="selected")
    args = parser.parse_args(argv)
    try:
        result = solve_case(args.case, args.output, selection_path=args.selection, policy=args.policy)
    except Exception as exc:
        print(json.dumps({"status": "refused", "audit_complete": False,
                          "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"audit_complete": result["audit_complete"], "policy": result["variant"],
                      "problem": result["problem"], "source_total": result["source_total"],
                      "T_s": result["total_virtual_time_s"], "T_per_N_s": result["total_time_per_source_s"],
                      "source_valid": result["source_valid"],
                      "result": str(Path(args.output).resolve() / "result.json")}, ensure_ascii=False))
    return 0 if result["audit_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
