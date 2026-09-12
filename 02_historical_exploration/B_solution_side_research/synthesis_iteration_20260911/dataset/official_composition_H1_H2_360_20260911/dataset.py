"""Environment/evaluation harness. Do not give scenario truth to a policy."""
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
