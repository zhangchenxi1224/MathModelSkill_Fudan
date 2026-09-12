"""Constructive endpoint exclusions use public disks, not source coordinates."""
import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

from bsolver.posterior_archive import build_archive

PATH = Path(__file__).resolve().parents[1]/"scripts/audit_radius_extremes.py"
SPEC = importlib.util.spec_from_file_location("audit_radius_extremes", PATH)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def action(index, p, outcome, angle=0, clear=False):
    return {"path": "/clear" if clear else "/measure", "outcome": "accepted",
            "request": {"request_id": str(index), "channel": 1, "position": {"x": p[0], "y": p[1]}},
            "response": {"accepted": True, "clear_result" if clear else "measure_result": outcome,
                         **({"svd_deg": angle} if outcome == "direction" else {})}}


def run(rows, problem=3):
    archive = build_archive(rows, problem=problem, case_id="fixture")
    return audit.audit_case(archive, rows, {"case_id": "fixture", "problem": problem, "protocol": "survey", "split": "fit"})


def test_min_endpoint_excluded_from_positive_and_clear_disk():
    # Compatible with a source around (1300, 0) and R=1400; the submitted
    # clearance point is used only as a 20m-disk center.
    result = run([action(1, (0, 0), "direction"), action(2, (1300, 0), "success", clear=True)])
    witnesses = [w for w in result["witnesses"] if w["excludes_point_mass"] == "all_R_1000"]
    assert len(witnesses) == 1
    assert Decimal(witnesses[0]["distance_lower_bound_m"]) > 1279
    assert len(result["channels"][0]["position_outer_polygon"]) > 1
    assert not result["channels"][0]["successful_clearance_disks"][0]["clear_point_is_true_g"]


def test_pre_discovery_negative_excludes_max_only_for_p3():
    # A source around 1100 with R=1050 explains the entire sequence.
    rows = [action(1, (0, 0), "no_signal"), action(2, (1000, 0), "direction"),
            action(3, (1100, 0), "success", clear=True)]
    p3, p4 = run(rows, 3), run(rows, 4)
    assert any(w["excludes_point_mass"] == "all_R_1500" and w["request_id"] == "1" for w in p3["witnesses"])
    assert not any(w["excludes_point_mass"] == "all_R_1500" for w in p4["witnesses"])


def test_post_clear_negative_and_unconfirmed_absence_never_witness():
    rows = [action(1, (1000, 0), "direction"), action(2, (1100, 0), "success", clear=True),
            action(3, (1100, 0), "no_signal")]
    result = run(rows)
    assert result["ignored_post_clear_actions"] == 1
    assert not result["witnesses"]
    assert not run([action(1, (0, 0), "no_signal")])["witnesses"]


def test_guarded_boundaries_and_degenerate_polygons():
    lower, upper = audit.conservative_distance_bounds((0, 0), [(1000, 0)])
    assert lower < 1000 < upper
    lower, upper = audit.conservative_distance_bounds((0, 0), [(1500, 0)])
    assert lower < 1500 < upper
    # A collinear point outside a segment must not be declared inside.
    lower, _ = audit.conservative_distance_bounds((3, 0), [(0, 0), (1, 0), (2, 0)])
    assert lower == Decimal("0.99999")
    lower, _ = audit.conservative_distance_bounds((.5, 0), [(0, 0), (1, 0)])
    assert lower == 0
    lower, upper = audit.conservative_distance_bounds((0, 0), [(-1, -1), (1, -1), (1, 1), (-1, 1)])
    assert lower == 0 and upper > Decimal(2).sqrt()


def test_duplicate_accepted_action_deduplicated_and_split_retained():
    rows = [action(1, (0, 0), "direction"), action(2, (1300, 0), "success", clear=True)]
    result = run([rows[0], rows[0], rows[1]])
    assert len(result["witnesses"]) == 1
    assert result["witnesses"][0]["split"] == "fit"


def test_bad_archive_epsilon_or_empty_set_cannot_certify():
    rows = [action(1, (0, 0), "direction")]
    archive = build_archive(rows, problem=3, case_id="fixture")
    assignment = {"case_id": "fixture", "problem": 3}
    archive["epsilon_deg"] = 1
    with pytest.raises(ValueError, match="epsilon"):
        audit.audit_case(archive, rows, assignment)
    archive["epsilon_deg"] = 1.0051
    archive["channels"]["1"]["position_outer_empty"] = True
    with pytest.raises(ValueError, match="nonempty"):
        audit.audit_case(archive, rows, assignment)
