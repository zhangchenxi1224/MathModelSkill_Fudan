"""Local-only candidate replay: frozen boundaries, actual success and failure."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts"))
import run_frozen_candidate as runner


def candidate(tmp_path, *, budget=360000.):
    value = {"problems": {
        "3": {"sensing": "active", "local_measure_limit": 1, "max_virtual_s": budget},
        "4": {"solver_class": "nosignal", "solver_config": {"local_measure_limit": 1, "max_virtual_s": budget}}},
        "dependency_hashes": {"src/bsolver/nosignal_sensing.py": runner.sha256(ROOT/"src/bsolver/nosignal_sensing.py")}}
    path = tmp_path/"candidate.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_candidate_same_normalization_and_existing_hash_anchor(tmp_path):
    path = candidate(tmp_path)
    frozen = runner.check_candidate(path)
    assert frozen["policy_specs"][3]["solver_config"]["local_measure_limit"] == 1
    assert frozen["policy_specs"][4]["solver_class"] == "nosignal"
    (tmp_path/"plan.json").write_text(json.dumps({"candidate_sha256": runner.sha256(path)}))
    assert runner.check_candidate(path)["candidate_hash_anchors"]
    path.write_text(path.read_text()+" ")
    with pytest.raises(ValueError, match="SHA differs"):
        runner.check_candidate(path)


def test_candidate_rejects_dependency_tamper_missing_hash_and_truth(tmp_path):
    path = candidate(tmp_path)
    value = json.loads(path.read_text())
    value["dependency_hashes"]["src/bsolver/nosignal_sensing.py"] = "0"*64
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="dependency changed"):
        runner.check_candidate(path)
    value["dependency_hashes"] = {}
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="requires its frozen"):
        runner.check_candidate(path)
    value["problems"]["4"] = {"sources": [{"channel": 1}]}
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="source/truth"):
        runner.check_candidate(path)


def test_original_core_freeze_is_required(tmp_path, monkeypatch):
    path = candidate(tmp_path)
    calls = []
    def refuse(*args, **kwargs):
        calls.append(args)
        raise ValueError("frozen core changed fixture")
    monkeypatch.setattr(runner, "verify_baseline", refuse)
    with pytest.raises(ValueError, match="frozen core"):
        runner.check_candidate(path)
    assert calls


@pytest.mark.parametrize("overrides", [{"problem": 2}, {"count": 0}, {"seed": -1}, {"seed": True}])
def test_invalid_inputs_do_not_run(tmp_path, overrides):
    args = {"problem": 3, "seed": 19, "count": 1, "output": tmp_path/"out", **overrides}
    with pytest.raises(ValueError):
        runner.execute(candidate(tmp_path), **args)
    assert not (tmp_path/"out").exists()


def test_local_normal_and_nosignal_without_network(tmp_path, monkeypatch):
    import bsolver.protocol as protocol
    class NoHTTP:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Local runner must not construct HTTPTransport")
    monkeypatch.setattr(protocol, "HTTPTransport", NoHTTP)
    path = candidate(tmp_path)
    for problem in (3, 4):
        output = tmp_path/f"p{problem}"
        summary = runner.execute(path, problem=problem, seed=993201, count=1, output=output)
        assert summary["complete"] == 1 and summary["hull_invariant_violations"] == 0
        row = json.loads((output/"results.json").read_text())[0]
        assert row["environment"] == "local_synthetic"
        folder = output/"cases"/row["case_id"]
        assert (folder/"requests.jsonl").stat().st_size > 0
        assert (folder/"decisions.jsonl").stat().st_size > 0
        assert row["policy_spec"]["solver_class"] == ("normal" if problem == 3 else "nosignal")
        with pytest.raises(FileExistsError):
            runner.execute(path, problem=problem, seed=993201, count=1, output=output)
    assert runner.make_case(99, 4, 0) == runner.make_case(99, 4, 0)
    assert runner.make_case(99, 4, 0)["scenario_sha256"] != runner.make_case(99, 4, 1)["scenario_sha256"]


def test_failed_budget_is_retained_and_penalized(tmp_path):
    output = tmp_path/"failure"
    result = runner.execute(candidate(tmp_path, budget=1.), problem=3, seed=73, count=1, output=output)
    assert result["failures"] == 1 and result["complete"] == 0
    row = json.loads((output/"results.json").read_text())[0]
    assert row["failure_penalized_time_s"] == 360000.
    assert row["status"] != "complete"
