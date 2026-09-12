"""Deployment integrity and independent fixture tests; no campaign results read."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import experiment
import solve_local


def save(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def seal(selection):
    selection["selection_sha256"] = experiment.digest(
        {k: v for k, v in selection.items() if k != "selection_sha256"})
    return selection


@pytest.fixture
def deployment(tmp_path):
    runtime = solve_local.PORTABLE_RUNTIME
    hashes = {str(p.relative_to(runtime)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted((runtime / "bsolver_frozen").glob("*.py"))}
    protocol = {"dataset_runtime": "missing/historical/absolute/runtime", "dataset_runtime_hashes": hashes}
    selection = seal({"choices": {"3": "candidate3", "4": "champion"},
                      "validation_specs": {"champion": {"implementation": "champion"},
                                           "candidate3": {"limit": 0, "problems": [3]}},
                      "source_files": experiment.files(), "protocol_sha256": experiment.digest(protocol)})
    save(tmp_path / "protocol.json", protocol)
    save(tmp_path / "selection.json", selection)
    return tmp_path / "selection.json", selection, protocol


@pytest.fixture
def case(tmp_path):
    # Deliberately different case seed and error seed verify exact field replay.
    value = {"problem": 3, "seed": 99111, "case_id": "cli-independent-fixture",
             "hypothesis": "arbitrary-unseen-label",
             "sources": [{"channel": channel, "position": [0., 0.], "radius": 1000.,
                          "direction_deg": None} for channel in range(1, 11)],
             "error_field": {"seed": 812734, "mode": "correlated", "correlation_length_m": 73.5}}
    path = tmp_path / "case.json"
    save(path, value)
    return path, value


def test_portable_runtime_and_problem_only_dispatch(deployment):
    path, _, _ = deployment
    selection, _, runtime = solve_local.load_deployment(path)
    assert runtime == solve_local.PORTABLE_RUNTIME.resolve()
    assert solve_local.choose_spec(selection, 3)[0] == "candidate3"
    assert solve_local.choose_spec(selection, 4)[0] == "champion"
    assert solve_local.choose_spec(selection, 3, "champion")[0] == "champion"


@pytest.mark.parametrize("tamper", ["selection", "protocol", "source", "runtime"])
def test_deployment_tampering_refused(deployment, tamper):
    path, selection, protocol = deployment
    if tamper == "selection":
        selection["choices"]["3"] = "champion"
    elif tamper == "protocol":
        protocol["unregistered"] = True
    elif tamper == "source":
        selection["source_files"]["methods/solver.py"] = "0" * 64
        seal(selection)
    else:
        protocol["dataset_runtime_hashes"]["bsolver_frozen/simulator.py"] = "0" * 64
        selection["protocol_sha256"] = experiment.digest(protocol)
        seal(selection)
    save(path, selection)
    save(path.parent / "protocol.json", protocol)
    with pytest.raises(ValueError):
        solve_local.load_deployment(path)


def test_optional_scenario_hash_and_tamper(case):
    path, value = case
    _, digest = solve_local.load_case(path)
    value["scenario_sha256"] = digest
    save(path, value)
    assert solve_local.load_case(path)[1] == digest
    value["error_field"]["seed"] += 1
    save(path, value)
    with pytest.raises(ValueError, match="scenario_sha256"):
        solve_local.load_case(path)


@pytest.mark.parametrize("problem", [True, 2, 5, "3", 3.0])
def test_invalid_problem_refused(case, problem):
    path, value = case
    value["problem"] = problem
    save(path, value)
    with pytest.raises(ValueError, match="problem"):
        solve_local.load_case(path)


def test_existing_output_is_never_touched(deployment, case, tmp_path):
    output = tmp_path / "already-here"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError):
        solve_local.solve_case(case[0], output, selection_path=deployment[0])
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_cli_fixture_completes_and_champion_comparison(deployment, case, tmp_path):
    for policy in ("selected", "champion"):
        output = tmp_path / policy
        proc = subprocess.run([sys.executable, str(ROOT / "solve_local.py"), "--case", str(case[0]),
                               "--output", str(output), "--selection", str(deployment[0]),
                               "--policy", policy], capture_output=True, text=True, timeout=45)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        report = json.loads(proc.stdout)
        result = experiment.read(output / "result.json")
        assert report["audit_complete"] and result["source_valid"]
        assert result["clear_successes"] == 10 and result["official_actions"] == 0
        assert result["hull_invariant_violations"] == []
        assert result["total_time_per_source_s"] == result["total_virtual_time_s"] / 10
        assert result["variant"] == ("candidate3" if policy == "selected" else "champion")
        assert (output / "requests.jsonl").is_file() and (output / "decisions.jsonl").is_file()


def test_exact_error_field_arguments_and_factory_boundary(deployment, case, tmp_path, monkeypatch):
    selection, protocol, runtime = solve_local.load_deployment(deployment[0])
    _, simulator = solve_local._load_runtime(runtime)
    real_field = simulator.FixedErrorField
    captured = {}

    def field(seed, mode, scale):
        captured["error"] = (seed, mode, scale)
        return real_field(seed, mode, scale)

    from methods import solver as solver_module
    real_factory = solver_module.make_solver

    def factory(client, problem, spec, decision_log=None):
        captured["factory"] = (problem, dict(spec))
        assert "sources" not in spec and "error_field" not in spec and "hypothesis" not in spec
        return real_factory(client, problem, spec, decision_log)

    monkeypatch.setattr(simulator, "FixedErrorField", field)
    monkeypatch.setattr(solver_module, "make_solver", factory)
    result = solve_local.solve_case(case[0], tmp_path / "exact", selection_path=deployment[0])
    assert result["audit_complete"]
    assert captured["error"] == (812734, "correlated", 73.5)
    assert captured["factory"] == (3, selection["validation_specs"]["candidate3"])


def test_cli_failure_is_nonzero_and_creates_no_output(deployment, case, tmp_path):
    value = case[1]
    value["scenario_sha256"] = "bad-hash"
    save(case[0], value)
    output = tmp_path / "refused"
    proc = subprocess.run([sys.executable, str(ROOT / "solve_local.py"), "--case", str(case[0]),
                           "--output", str(output), "--selection", str(deployment[0])],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode != 0
    assert "scenario_sha256" in proc.stderr
    assert not output.exists()


def test_post_run_source_drift_invalidates_an_otherwise_complete_run(deployment, case, tmp_path, monkeypatch):
    original = experiment.files
    checks = []

    def source_files():
        current = original()
        checks.append(True)
        if len(checks) > 1:
            current["methods/solver.py"] = "0" * 64
        return current

    monkeypatch.setattr(experiment, "files", source_files)
    result = solve_local.solve_case(case[0], tmp_path / "drift", selection_path=deployment[0])
    assert result["clear_successes"] == 10
    assert result["status"] == "invalid_source"
    assert not result["audit_complete"] and not result["source_valid"]
