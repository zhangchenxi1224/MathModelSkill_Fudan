"""Independent Q2 comparison certificate; uses only the Python standard library.

No imports from study.py, q2_geometry.py, or the vendored solution are allowed.
Polygon clipping and final circle containment use exact rational arithmetic.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, localcontext, ROUND_CEILING
from fractions import Fraction as F
import json
import math
from pathlib import Path


PI = Decimal("3.14159265358979323846264338327950288419716939937510582097494459230781640628620899862803482534211706798214808651")
COEFFICIENT_ERROR = F(1, 10**58)
HALFPLANE_OUTWARD = F(1, 10**40)
ANGLE_GUARD_DEG = Decimal("0.000001")
EPSILON_DEG = Decimal("1.0051")
OLD_Q = (F(700), F(400))
NEW_Q = (F("822.4454480692101"), F("575.8825025043233"))


def decimal_of(value: F, places: int = 40) -> str:
    with localcontext() as ctx:
        ctx.prec = max(100, places + 30)
        return format((Decimal(value.numerator) / Decimal(value.denominator)).quantize(Decimal(1).scaleb(-places)), "f")


def sin_cos_deg(angle: Decimal) -> tuple[Decimal, Decimal]:
    """80-digit Taylor evaluation, rounded to 65 decimal places.

    Reduction to [-pi, pi], term cutoff 1e-88. The 1e-58 coefficient
    allowance used elsewhere is substantially larger than all rounding and
    truncation errors; see docs/certificate_comparison.md.
    """
    with localcontext() as ctx:
        ctx.prec = 80
        reduced = (angle % Decimal(360) + Decimal(360)) % Decimal(360)
        if reduced > Decimal(180):
            reduced -= Decimal(360)
        x = reduced * PI / Decimal(180)
        x2 = x*x
        sine = term_s = x
        cosine = term_c = Decimal(1)
        for n in range(1, 160):
            term_s *= -x2 / Decimal((2*n)*(2*n+1))
            term_c *= -x2 / Decimal((2*n-1)*(2*n))
            sine += term_s
            cosine += term_c
            if max(abs(term_s), abs(term_c)) < Decimal("1e-88"):
                break
        else:
            raise ArithmeticError("Taylor expansion did not converge")
        unit = Decimal("1e-65")
        return sine.quantize(unit), cosine.quantize(unit)


def ceil_sqrt_fraction(value: F, decimal_places: int = 6) -> F:
    assert value >= 0
    scale = 10**decimal_places
    scaled = value * scale*scale
    root = math.isqrt(scaled.numerator // scaled.denominator)
    if root*root*scaled.denominator < scaled.numerator:
        root += 1
    return F(root, scale)


def dist_sq(p, q):
    return (p[0]-q[0])**2 + (p[1]-q[1])**2


def clip(poly, a: F, b: F, c: F):
    """Exact Sutherland-Hodgman clipping to a*x+b*y+c >= 0."""
    if not poly:
        return []
    result = []
    previous = poly[-1]
    previous_value = a*previous[0]+b*previous[1]+c
    for current in poly:
        value = a*current[0]+b*current[1]+c
        if (previous_value >= 0) != (value >= 0):
            t = previous_value/(previous_value-value)
            result.append((previous[0]+t*(current[0]-previous[0]), previous[1]+t*(current[1]-previous[1])))
        if value >= 0:
            result.append(current)
        previous, previous_value = current, value
    unique = []
    for p in result:
        if not unique or p != unique[-1]:
            unique.append(p)
    if len(unique)>1 and unique[0] == unique[-1]:
        unique.pop()
    return unique


def wedge_clip(poly, q, low: Decimal, high: Decimal):
    # Direction interval is unwrapped and has width < 180 degrees.
    s1, c1 = map(F, sin_cos_deg(low))
    s2, c2 = map(F, sin_cos_deg(high))
    # cross(u_low, p-q)>=0; cross(p-q, u_high)>=0.
    a1, b1 = -s1, c1
    a2, b2 = s2, -c2
    poly = clip(poly, a1, b1, -a1*q[0]-b1*q[1]+HALFPLANE_OUTWARD)
    return clip(poly, a2, b2, -a2*q[0]-b2*q[1]+HALFPLANE_OUTWARD)


def initial_triangle():
    with localcontext() as ctx:
        ctx.prec = 80
        sine, cosine = sin_cos_deg(EPSILON_DEG + ANGLE_GUARD_DEG)
        height = (Decimal(1500)*sine/cosine).quantize(Decimal("1e-40"), rounding=ROUND_CEILING)
    return [(F(0), F(0)), (F(1500), F(height)), (F(1500), -F(height))]


def bbox_circle(poly):
    # A simple independently chosen center suffices; no MEC algorithm is needed.
    center = tuple(F(decimal_of((min(p[k] for p in poly)+max(p[k] for p in poly))/2, 20)) for k in range(2))
    radius = ceil_sqrt_fraction(max(dist_sq(p, center) for p in poly))
    assert all(dist_sq(p, center) <= radius*radius for p in poly)
    return center, radius


def angular_certificate(point, observer, report: str):
    delta = (point[0]-observer[0], point[1]-observer[1])
    report_decimal = Decimal(report)
    # Strict membership in the physical +/-1-degree wedge, with coefficient
    # approximation accounted for on both linear inequalities.
    sl, cl = map(F, sin_cos_deg(report_decimal-1))
    su, cu = map(F, sin_cos_deg(report_decimal+1))
    error = COEFFICIENT_ERROR*(abs(delta[0])+abs(delta[1]))
    margins = [cl*delta[1]-sl*delta[0]-error, su*delta[0]-cu*delta[1]-error]
    return {"report_deg": report, "halfplane_slack_lower_m": [decimal_of(v, 35) for v in margins], "valid": all(v>0 for v in margins)}


def source_at(radius: str, angle: str):
    with localcontext() as ctx:
        ctx.prec = 80
        sine, cosine = sin_cos_deg(Decimal(angle))
        return tuple(F((Decimal(radius)*v).quantize(Decimal("1e-45"))) for v in (cosine, sine))


def build_lower_certificate():
    # 0.25m search of the nearer radial coordinate; true legality is checked
    # independently below, after choosing a two-decimal shared report.
    far = source_at("1499.99999", "-0.9999")
    af = math.degrees(math.atan2(float(far[1]-OLD_Q[1]), float(far[0]-OLD_Q[0])))
    chosen = None
    for k in range(4000, 6000):
        radius = Decimal(k)/4
        near = source_at(str(radius), "0.9999")
        an = math.degrees(math.atan2(float(near[1]-OLD_Q[1]), float(near[0]-OLD_Q[0])))
        lo, hi = max(an, af)-1, min(an, af)+1
        report_value = math.ceil(lo*100+1e-9)/100
        if report_value > hi:
            continue
        report = f"{report_value % 360:.2f}"
        checks = [angular_certificate(p, OLD_Q, report) for p in [near, far]]
        if all(c["valid"] for c in checks):
            separation_sq = dist_sq(near, far)
            if chosen is None or separation_sq>chosen[0]:
                chosen = (separation_sq, near, str(radius), report)
    if chosen is None:
        raise AssertionError("No legal pair found")
    separation_sq, near, near_radius, report = chosen
    sources = []
    for p, nominal_radius, angle in [(near, near_radius, "0.9999"), (far, "1499.99999", "-0.9999")]:
        first_d2, second_d2 = dist_sq(p, (F(0), F(0))), dist_sq(p, OLD_Q)
        first_angle = angular_certificate(p, (F(0), F(0)), "0.00")
        second_angle = angular_certificate(p, OLD_Q, report)
        valid = 25 < first_d2 <= 1500**2 and 25 < second_d2 <= 1500**2 and first_d2 <= 1800**2 and first_angle["valid"] and second_angle["valid"]
        sources.append({"position": [decimal_of(v, 45) for v in p], "constructed_radius_m": nominal_radius, "constructed_angle_deg": angle, "reception_radius_m": "1500", "first_distance_sq": str(first_d2), "second_distance_sq": str(second_d2), "first_angle": first_angle, "second_angle": second_angle, "all_rules_valid": valid})
    # floor(100 * sqrt(separation_sq)/2), entirely rational/integer.
    scaled = separation_sq*2500
    lower = F(math.isqrt(scaled.numerator//scaled.denominator), 100)
    assert (2*lower)**2 <= separation_sq
    assert all(s["all_rules_valid"] for s in sources)
    return {"old_point": ["700", "400"], "same_first_report_deg": "0.00", "same_second_report_deg": report, "sources": sources, "source_separation_sq_exact": str(separation_sq), "radius_lower_m": decimal_of(lower, 2), "radius_lower_rounding": "downward to 0.01m, exact integer square-root check", "valid": True}


def four_disk_safety(q):
    constraints = []
    for r in [5, 1000]:
        for sign in [-1, 1]:
            sine, cosine = map(F, sin_cos_deg(Decimal(sign)*EPSILON_DEG))
            center = (r*cosine, r*sine)
            # Center error <= 2*r*COEFFICIENT_ERROR in Euclidean norm.
            distance_upper = ceil_sqrt_fraction(dist_sq(q, center), 20) + 2*r*COEFFICIENT_ERROR
            slack = F(1000)-distance_upper
            constraints.append({"center_radius_m": r, "angle_sign": sign, "distance_upper_m": decimal_of(distance_upper, 30), "margin_lower_m": decimal_of(slack, 25), "valid": slack>0})
    return {"epsilon_deg": str(EPSILON_DEG), "constraints": constraints, "valid": all(c["valid"] for c in constraints)}


def build_upper_certificate(bin_width: str = "0.25"):
    width = F(bin_width)
    count = F(360)/width
    if count.denominator != 1 or width <= 0 or width > 10:
        raise ValueError("bin width must divide 360 exactly and be in (0,10]")
    count = count.numerator
    triangle = initial_triangle()
    records = []
    max_radius = F(5)  # Conservative inclusion of the near branch.
    max_index = None
    for i in range(count):
        start, end = i*width, (i+1)*width
        low = Decimal(decimal_of(start, 15))-EPSILON_DEG-ANGLE_GUARD_DEG
        high = Decimal(decimal_of(end, 15))+EPSILON_DEG+ANGLE_GUARD_DEG
        poly = wedge_clip(triangle, NEW_Q, low, high)
        record = {"index": i, "report_interval_deg": [decimal_of(start, 15), decimal_of(end, 15)], "empty": not poly}
        if poly:
            center, radius = bbox_circle(poly)
            record.update({"expanded_angle_interval_deg": [str(low), str(high)], "vertices_xy_fraction": [[str(v) for v in p] for p in poly], "center_xy_m": [decimal_of(v, 20) for v in center], "radius_upper_m": decimal_of(radius, 6), "all_vertices_exactly_covered": all(dist_sq(p, center)<=radius*radius for p in poly)})
            if radius>max_radius:
                max_radius, max_index = radius, i
        records.append(record)
    return {"new_point": [decimal_of(v, 20) for v in NEW_Q], "physical_error_deg": "1", "base_outer_error_deg": str(EPSILON_DEG), "extra_angular_guard_deg": str(ANGLE_GUARD_DEG), "initial_triangle_xy_fraction": [[str(v) for v in p] for p in triangle], "coefficient_error_allowance": str(COEFFICIENT_ERROR), "halfplane_outward_offset_m": str(HALFPLANE_OUTWARD), "bin_width_deg": str(width), "total_bins": count, "nonempty_bins": sum(not r["empty"] for r in records), "near_radius_upper_m": "5", "worst_bin_index": max_index, "radius_upper_m": decimal_of(max_radius, 6), "circle_method": "bounding-box center with exact rational distance certification; not assumed minimal", "records": records}


def verify_upper_certificate(cert):
    width = F(cert["bin_width_deg"])
    records = cert["records"]
    triangle = [tuple(map(F, p)) for p in cert["initial_triangle_xy_fraction"]]
    assert triangle==initial_triangle()
    assert tuple(map(F, cert["new_point"]))==NEW_Q
    assert len(records)==cert["total_bins"]
    assert len(records)*width==360
    maximum = F(cert["near_radius_upper_m"])
    for i, rec in enumerate(records):
        assert rec["index"] == i
        assert list(map(F, rec["report_interval_deg"])) == [i*width, (i+1)*width]
        low = Decimal(decimal_of(i*width, 15))-EPSILON_DEG-ANGLE_GUARD_DEG
        high = Decimal(decimal_of((i+1)*width, 15))+EPSILON_DEG+ANGLE_GUARD_DEG
        recomputed_poly = wedge_clip(triangle, NEW_Q, low, high)
        assert rec["empty"] == (not recomputed_poly)
        if rec["empty"]:
            continue
        poly = [tuple(map(F, p)) for p in rec["vertices_xy_fraction"]]
        assert poly==recomputed_poly
        center = tuple(map(F, rec["center_xy_m"]))
        radius = F(rec["radius_upper_m"])
        assert poly and radius>=0
        assert all(dist_sq(p, center)<=radius*radius for p in poly)
        maximum = max(maximum, radius)
    assert maximum==F(cert["radius_upper_m"])
    return {"report_bins_cover_full_360_without_gaps": True, "saved_polygons_and_empty_bins_match_exact_recomputation": True, "all_saved_vertices_exactly_inside_saved_circles": True, "saved_global_upper_matches_bin_maximum_and_near": True, "verified_bins": len(records)}


def run(output: Path, bin_width="0.25"):
    output.mkdir(parents=True, exist_ok=True)
    lower = build_lower_certificate()
    upper = build_upper_certificate(bin_width)
    verification = verify_upper_certificate(upper)
    old_safety, new_safety = four_disk_safety(OLD_Q), four_disk_safety(NEW_Q)
    assert old_safety["valid"] and new_safety["valid"]
    assert F(lower["radius_lower_m"])>F(upper["radius_upper_m"])
    summary = {"context": "s=(0,0), first report=0.00 degrees, omnidirectional source in target B(0,1800)", "old_point": lower["old_point"], "new_point": upper["new_point"], "old_worst_radius_lower_m": lower["radius_lower_m"], "new_worst_radius_upper_m": upper["radius_upper_m"], "strict_gap_lower_m": decimal_of(F(lower["radius_lower_m"])-F(upper["radius_upper_m"]), 6), "strict_improvement_for_this_first_observation": True, "global_optimality_claim": False, "old_safety": old_safety, "new_safety": new_safety, "upper_verification": verification, "arithmetic": "80-digit decimal trigonometry with outward allowance; exact rational clipping and exact rational circle containment", "formal_proof_assistant_verification": False}
    for name, data in [("old_lower_witness.json",lower), ("new_upper_bins.json",upper), ("summary.json",summary)]:
        (output/name).write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in summary.items() if k not in ["old_safety", "new_safety"]}, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bin-width", default="0.25")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent/"results"/"certificate_audit")
    args = parser.parse_args()
    run(args.output, args.bin_width)
