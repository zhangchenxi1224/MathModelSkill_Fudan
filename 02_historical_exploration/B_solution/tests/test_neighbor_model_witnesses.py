"""Exact-error witnesses preserve angular wrapping and numerical allowances."""
import importlib.util
import math
from pathlib import Path

import pytest

from bsolver.posterior_archive import build_archive
from bsolver.simulator import FixedErrorField

PATH = Path(__file__).resolve().parents[1]/"scripts/audit_neighbor_error_models.py"
SPEC = importlib.util.spec_from_file_location("audit_neighbor_error_models", PATH)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)
HULL = [(999.9, -.1), (1000.1, -.1), (1000.1, .1), (999.9, .1)]


def obs(p, angle, index=0):
    return {"position": p, "outcome": "direction", "reported_angle_deg": angle,
            "request_id": str(index), "row_index": index}


def test_constructive_observation_rejects_both_precise_formulas():
    result = audit.assess_pair(HULL, obs((0, 0), 0), obs((0, 1), .5, 1))
    assert result["status"] == "evaluated"
    assert len(result["witnesses"]) == 2
    assert result["true_bearing_change_upper_deg"] < .06


@pytest.mark.parametrize("e1,e2", [(1, 1), (-1, -1), (1, -1), (-1, 1)])
def test_actual_extreme_endpoint_observations_not_falsely_rejected(e1, e2):
    actual_bearing = math.degrees(math.atan2(-1, 1000)) % 360
    result = audit.assess_pair(HULL, obs((0, 0), e1 % 360),
                               obs((0, 1), round((actual_bearing+e2) % 360, 2) % 360, 1))
    assert not any(w["excludes_exact_model"].startswith("extreme") for w in result["witnesses"])


def test_actual_correlated150_readouts_respect_lipschitz_bound():
    for seed in range(8):
        field = FixedErrorField(seed, mode="correlated", correlation_length=150)
        a, b = (0, 0), (0, 1)
        first = round(field(1, a) % 360, 2) % 360
        second = round((math.degrees(math.atan2(-1, 1000))+field(1, b)) % 360, 2) % 360
        result = audit.assess_pair(HULL, obs(a, first), obs(b, second, 1))
        assert not any(w["excludes_exact_model"] == "FixedErrorField_correlated_scale150" for w in result["witnesses"])


def test_wrapping_non_direction_and_too_close_are_conservative():
    result = audit.assess_pair(HULL, obs((0, 0), 359.99), obs((0, 1), .01, 1))
    assert result["reported_circular_delta_deg"] == pytest.approx(.02)
    assert not result["witnesses"]
    near = obs((0, 0), 0)
    near["outcome"] = "near"
    assert audit.assess_pair(HULL, near, obs((0, 1), 0))["status"] == "not_two_direction_readouts"
    assert audit.assess_pair([(0, 0)], obs((0, 0), 0), obs((0, 1), 90))["status"].startswith("outer_hull_too_close")
    assert audit.assess_pair(HULL, obs((0, 0), 0), obs((0, 3), 0))["status"].startswith("outside_predeclared")


def test_fixed_neighbor_ids_and_postclear_are_checked():
    def action(index, p, clear=False):
        return {"path": "/clear" if clear else "/measure", "outcome": "accepted",
                "request": {"request_id": str(index), "channel": 1, "position": {"x": p[0], "y": p[1]}},
                "response": {"accepted": True, **({"clear_result": "success"} if clear else
                              {"measure_result": "direction", "svd_deg": .5 if index == 2 else 0})}}
    rows = [action(1, (0, 0)), action(2, (0, 1)), action(3, (1000, 0), True)]
    archive = build_archive(rows, problem=3, case_id="fixture")
    assignment = {"case_id": "fixture", "problem": 3, "protocol": "survey", "split": "fit"}
    decisions = [{"event": "survey_plan", "plan_sha256": "fixture", "plan": [{"channel": 1,
                 "reference": {"anchor_position": [0, 0]}, "probes": [{"kind": "neighbor", "station_id": "survey:01:13", "plan_index": 13, "position": [0, 1]}]}]},
                 {"event": "measure", "phase": "survey", "station_id": "survey:01:13", "target_channel": 1, "request_id": "2"}]
    result = audit.audit_case(archive, rows, decisions, assignment)
    assert len(result["pairs"]) == 1 and len(result["witnesses"]) == 2
    # A log claiming an already removed target's later feedback is the planned
    # neighbor cannot turn that feedback into a source-error witness.
    decisions[1]["request_id"] = "4"
    removed = {"path": "/measure", "outcome": "accepted", "request": {"request_id": "4", "channel": 1, "position": {"x": 0, "y": 1}},
               "response": {"accepted": True, "measure_result": "no_signal"}}
    with pytest.raises(ValueError, match="active accepted"):
        audit.audit_case(archive, rows+[removed], decisions, assignment)
