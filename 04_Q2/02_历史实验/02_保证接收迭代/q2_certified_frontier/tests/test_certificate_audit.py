from fractions import Fraction as F
import importlib.util
from pathlib import Path

import pytest


PATH = Path(__file__).resolve().parents[1]/"certificate_audit.py"
spec = importlib.util.spec_from_file_location("independent_certificate_audit", PATH)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_exact_clipping_retains_boundary_and_expected_intersections():
    square = [(F(0),F(0)), (F(2),F(0)), (F(2),F(2)), (F(0),F(2))]
    clipped = audit.clip(square, F(1), F(1), F(-2))
    assert set(clipped)=={(F(2),F(0)), (F(2),F(2)), (F(0),F(2))}


def test_radius_rounding_is_outward_for_non_square():
    radius = audit.ceil_sqrt_fraction(F(2), 6)
    assert radius*radius>=2
    assert (radius-F(1,10**6))**2<2


def test_physical_witness_is_strictly_legal_and_strong():
    cert = audit.build_lower_certificate()
    assert cert["same_second_report_deg"] == "330.95"
    assert F(cert["radius_lower_m"])>=F("78.53")
    assert all(s["all_rules_valid"] for s in cert["sources"])
    assert all(F(s["first_distance_sq"])<1500**2 for s in cert["sources"])


def test_new_point_has_positive_guaranteed_reception_margin():
    cert = audit.four_disk_safety(audit.NEW_Q)
    assert cert["valid"]
    assert min(F(c["margin_lower_m"]) for c in cert["constraints"])>F("0.019")


@pytest.fixture(scope="module")
def all_bins():
    return audit.build_upper_certificate("0.25")


def test_all_continuous_report_bins_and_circles_are_certified(all_bins):
    verification = audit.verify_upper_certificate(all_bins)
    assert all_bins["total_bins"]==1440
    assert verification["report_bins_cover_full_360_without_gaps"]
    assert F(all_bins["radius_upper_m"])<F("78.53")


def test_circle_verifier_rejects_understated_radius(all_bins):
    import copy
    altered = copy.deepcopy(all_bins)
    record = next(r for r in altered["records"] if not r["empty"] and F(r["radius_upper_m"])>1)
    record["radius_upper_m"]="0.000000"
    with pytest.raises(AssertionError):
        audit.verify_upper_certificate(altered)


def test_verifier_rejects_a_silently_dropped_nonempty_branch(all_bins):
    import copy
    altered = copy.deepcopy(all_bins)
    record = next(r for r in altered["records"] if not r["empty"])
    record["empty"]=True
    with pytest.raises(AssertionError):
        audit.verify_upper_certificate(altered)
