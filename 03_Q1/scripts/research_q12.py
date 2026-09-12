"""Reproducible Q1 examples and legal two-measurement Q2 comparisons.

This script never calls the official simulator. Truth appears only in this
external experiment driver, never in candidate generation or selection.
Core calculations use stdlib plus bsolver; matplotlib is optional for plots.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bsolver.geometry import (bearing_deg, polygon_diameter, minimum_enclosing_circle,
    nearest_safe_clear, certify_clear, contains, distance, max_distance, intersect_bearings)
from bsolver.knowledge import ChannelKnowledge
from bsolver.sensing import choose_measurement, direction_outcome_bound, guaranteed_1000


def safe_certificate(hull, s, q):
    """Certify Q_safe or find a Q_safe endpoint of a ray through q.

    Intersect exact (up to floating arithmetic) one-dimensional quadratic
    intervals for all hull vertices; no sampled-source safety claim is used.
    """
    worst = max_distance(hull, q)
    if guaranteed_1000(hull, q):
        return {"type": "Q_safe", "max_distance_m": worst, "endpoint": q, "lambda": 1.0}
    radius = 999.99999
    v = q[0]-s[0], q[1]-s[1]
    vv = v[0]*v[0]+v[1]*v[1]
    if vv == 0:
        return {"type": "first_positive_station", "max_distance_m": worst}
    lo, hi = 1.0, math.inf
    for p in hull:
        w = p[0]-s[0], p[1]-s[1]
        projected = (w[0]*v[0]+w[1]*v[1])/vv
        perpendicular2 = max(0.0, w[0]*w[0]+w[1]*w[1]-projected*projected*vv)
        if perpendicular2 > radius*radius:
            raise AssertionError("Q3 candidate has no distance/convex safety certificate")
        delta = math.sqrt((radius*radius-perpendicular2)/vv)
        lo, hi = max(lo, projected-delta), min(hi, projected+delta)
        if lo > hi:
            raise AssertionError("Q3 candidate has empty safe ray interval")
    t = (lo+hi)/2
    endpoint = s[0]+t*v[0], s[1]+t*v[1]
    endpoint_worst = max_distance(hull, endpoint)
    if endpoint_worst > radius+1e-7:
        raise AssertionError("Constructed endpoint failed all-vertex verification")
    return {"type": "convex_hull_first_station_and_Q_safe", "max_distance_m": worst,
            "endpoint": endpoint, "endpoint_max_distance_m": endpoint_worst, "lambda": 1/t}


def response_at(source, receiver_radius, station, error):
    d = distance(source, station)
    if d > receiver_radius+1e-8:
        return {"measure_result": "no_signal"}
    if d <= 5:
        return {"measure_result": "near"}
    return {"measure_result": "direction",
            "svd_deg": round((bearing_deg(station, source)+error) % 360, 2) % 360}


def q1_examples():
    triangle = [(0., 0.), (39., 0.), (19.5, 39*math.sqrt(3)/2)]
    center, radius = minimum_enclosing_circle(triangle)
    rectangle = [(-8., -6.), (8., -6.), (8., 6.), (-8., 6.)]
    current = (100., 0.)
    mec, r = minimum_enclosing_circle(rectangle)
    nearest = nearest_safe_clear(rectangle, current)
    assert nearest is not None and certify_clear(rectangle, nearest)
    assert distance(current, nearest) < distance(current, mec)
    observations = [((0., 0.), math.degrees(math.atan2(500, 1000))), ((1000., 0.), 90.)]
    crossing = intersect_bearings(observations)
    unbounded = intersect_bearings([((0., 0.), 0.)])
    empty = intersect_bearings([((0., 0.), 0.), ((-10., 0.), 180.)])
    result = {
        "equilateral_counterexample": {"vertices": triangle, "diameter_m": polygon_diameter(triangle),
            "diameter_half_m": polygon_diameter(triangle)/2, "minimum_covering_radius_m": radius,
            "mec_center": center, "can_clear_with_20m_radius": certify_clear(triangle, center),
            "diameter_is_less_than_40_but_no_20m_cover_exists": radius > 20},
        "nearest_safe_vs_mec": {"vertices": rectangle, "current": current, "mec_center": mec,
            "mec_radius_m": r, "nearest_safe": nearest, "nearest_safe_farthest_m": max_distance(rectangle, nearest),
            "movement_to_mec_m": distance(current, mec), "movement_to_nearest_safe_m": distance(current, nearest),
            "movement_saved_m": distance(current, mec)-distance(current, nearest)},
        "intersection_examples": {"bounded_kind": crossing.kind, "bounded_vertices": crossing.vertices,
            "bounded_diameter_m": polygon_diameter(crossing.vertices), "one_bearing_kind": unbounded.kind,
            "disjoint_kind": empty.kind},
    }
    assert crossing.kind == "polygon" and unbounded.kind == "unbounded" and empty.kind == "empty"
    return result


def q2_study(bin_width_deg=4.0):
    rows, decisions, cache = [], {}, {}
    distances = (100., 300., 600., 1000., 1250., 1500.)
    angles = (0., .0049, 90., 179.995, 359.995)
    for d in distances:
        for angle in angles:
            a = math.radians(angle)
            # Tiny inward shift removes a floating-point 1500+ulp boundary.
            source = d*math.cos(a)*(1-1e-15), d*math.sin(a)*(1-1e-15)
            radius = max(1000., distance(source, (0., 0.)))
            for error1 in (-1., 0., 1.):
                first = response_at(source, radius, (0., 0.), error1)
                assert first["measure_result"] == "direction"
                case_id = f"d{d:g}_a{angle:g}_e{error1:g}"
                for mode in ("fixed", "estimated", "active"):
                    k = ChannelKnowledge(1, 3)
                    k.observe((0., 0.), first)
                    assert k.position_in_outer_hull(source)
                    # Identical first observations MUST have identical actions,
                    # regardless of the experimenter's hidden target distance.
                    key = (first["svd_deg"], mode)
                    if key not in cache:
                        q, info = choose_measurement(k, (0., 0.), mode, bin_width_deg=bin_width_deg)
                        assert q is not None
                        bound = direction_outcome_bound(k.hull, q, k.epsilon_deg, bin_width_deg)
                        cert = safe_certificate(k.hull, (0., 0.), q)
                        cache[key] = (q, info, bound, cert)
                        decisions[f"angle_{key[0]:g}_{mode}"] = {
                            "first_angle_deg": key[0], "mode": mode, "point": q,
                            "candidate_metadata": info, "direction_outcome_bound_m": bound,
                            "independent_safety_certificate": cert}
                    q, info, bound, cert = cache[key]
                    second = response_at(source, radius, q, .75)
                    assert second["measure_result"] != "no_signal", (case_id, mode, q)
                    k.observe(q, second)
                    assert k.position_in_outer_hull(source), (case_id, mode, source)
                    center, post_radius = minimum_enclosing_circle(k.hull)
                    assert post_radius <= bound+1e-5
                    safe = nearest_safe_clear(k.hull, q)
                    clearable = safe is not None and certify_clear(k.hull, safe)
                    first_ray = (0-source[0], 0-source[1])
                    second_ray = q[0]-source[0], q[1]-source[1]
                    cosine = max(-1., min(1., (first_ray[0]*second_ray[0]+first_ray[1]*second_ray[1])/
                                         (math.hypot(*first_ray)*math.hypot(*second_ray))))
                    crossing_angle = math.degrees(math.acos(abs(cosine)))
                    rows.append({"case_id": case_id, "mode": mode, "source_distance_m": d,
                        "true_angle_deg": angle, "first_error_deg": error1, "first_report_deg": first["svd_deg"],
                        "second_error_deg": .75, "receiver_radius_m": radius, "q_x": q[0], "q_y": q[1],
                        "second_response": second["measure_result"], "safety_certificate": cert["type"],
                        "movement_m": distance((0., 0.), q), "movement_and_second_measure_s": distance((0., 0.), q)/5+5,
                        "crossing_acute_angle_deg": crossing_angle, "posterior_diameter_m": polygon_diameter(k.hull),
                        "posterior_mec_radius_m": post_radius, "direction_outcome_bound_m": bound,
                        "mec_center_error_m": distance(center, source), "certified_clear_after_two": clearable,
                        "extra_clear_movement_m": distance(q, safe) if clearable else None})
    near_checks = []
    for d in (0., 1., 5., 5.0001):
        response = response_at((d, 0.), 1000., (0., 0.), 0)
        k = ChannelKnowledge(1, 3)
        k.observe((0., 0.), response)
        near_checks.append({"distance_m": d, "response": response["measure_result"],
            "true_state_retained": k.position_in_outer_hull((d, 0.))})
    summaries = {}
    for mode in ("fixed", "estimated", "active"):
        selected = [r for r in rows if r["mode"] == mode]
        summaries[mode] = {"n": len(selected),
            "no_signal_count": sum(r["second_response"] == "no_signal" for r in selected),
            "mean_movement_m": statistics.mean(r["movement_m"] for r in selected),
            "mean_posterior_mec_radius_m": statistics.mean(r["posterior_mec_radius_m"] for r in selected),
            "max_posterior_mec_radius_m": max(r["posterior_mec_radius_m"] for r in selected),
            "mean_mec_center_error_m": statistics.mean(r["mec_center_error_m"] for r in selected),
            "mean_crossing_acute_angle_deg": statistics.mean(r["crossing_acute_angle_deg"] for r in selected),
            "certified_clear_fraction_after_two": statistics.mean(r["certified_clear_after_two"] for r in selected)}
    return rows, {"study": "synthetic_legal_two_measurement_comparison_not_official",
        "legal_initial_scenarios": len(rows)//3, "rows": len(rows), "bin_width_deg": bin_width_deg,
        "first_errors_deg": [-1, 0, 1], "second_error_deg": .75,
        "hidden_truth_is_used_only_for_observation_generation_and_evaluation": True,
        "no_source_distribution_or_error_independence_claim": True,
        "summary_by_mode": summaries, "near_threshold_checks": near_checks}, decisions


def figures(output, q1, rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Polygon
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8), constrained_layout=True)
    tri = q1["equilateral_counterexample"]
    axes[0].add_patch(Polygon(tri["vertices"], facecolor="#DBEAFE", edgecolor="#1D4ED8"))
    for radius, color, style, label in ((tri["diameter_half_m"], "#DC2626", "--", "D/2: insufficient"),
            (tri["minimum_covering_radius_m"], "#15803D", "-", "minimum enclosing circle")):
        axes[0].add_patch(Circle(tri["mec_center"], radius, fill=False, color=color, linestyle=style, label=label))
    axes[0].set(xlim=(-7, 47), ylim=(-16, 39), title="Q1: diameter does not define a covering circle")
    axes[0].legend(loc="lower center", fontsize=8)
    rect = q1["nearest_safe_vs_mec"]
    axes[1].add_patch(Polygon(rect["vertices"], facecolor="#DBEAFE", edgecolor="#1D4ED8"))
    for point, color, label in ((rect["mec_center"], "#15803D", "MEC center"),
                               (rect["nearest_safe"], "#D97706", "nearest certified clear point"),
                               (rect["current"], "#111827", "robot")):
        axes[1].scatter(*point, color=color, label=label)
    axes[1].plot([rect["nearest_safe"][0], rect["current"][0]], [0, 0], color="#D97706")
    axes[1].set(xlim=(-15, 105), ylim=(-27, 27), title="Q1: minimize travel within the safe-clear set")
    axes[1].legend(loc="upper right", fontsize=8)
    for ax in axes:
        ax.set_aspect("equal"); ax.set_xlabel("x (m)"); ax.set_ylabel("y (m)")
        ax.grid(alpha=.2)
    fig.savefig(output/"q1_geometry.png", dpi=180)
    plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.2), constrained_layout=True)
    for mode, color in (("fixed", "#1D4ED8"), ("estimated", "#D97706"), ("active", "#15803D")):
        distances = sorted({r["source_distance_m"] for r in rows})
        mean_radius = [statistics.mean(r["posterior_mec_radius_m"] for r in rows
                       if r["mode"] == mode and r["source_distance_m"] == d) for d in distances]
        axes[0].plot(distances, mean_radius, marker="o", label=mode, color=color)
        mode_rows = [r for r in rows if r["mode"] == mode]
        axes[1].scatter([r["movement_m"] for r in mode_rows], [r["posterior_mec_radius_m"] for r in mode_rows],
                        label=mode, color=color, alpha=.5, s=15)
    axes[0].axhline(20, color="#777777", linestyle="--", label="20 m clear radius")
    axes[0].set(xlabel="True first-station distance (m)", ylabel="Mean posterior MEC radius (m)",
                title="90 legal scenarios; same fixed second-site error")
    axes[1].set(xlabel="Movement to second station (m)", ylabel="Posterior MEC radius (m)",
                title="Travel and localization quality trade-off")
    for ax in axes:
        ax.legend(fontsize=8); ax.grid(alpha=.2)
    fig.savefig(output/"q2_strategy_comparison.png", dpi=180)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT/"results"/"q12")
    parser.add_argument("--bin-width", type=float, default=4.)
    parser.add_argument("--skip-plots", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    q1 = q1_examples()
    rows, summary, decisions = q2_study(args.bin_width)
    for filename, data in (("q1_examples.json", q1), ("q2_summary.json", summary), ("q2_decisions.json", decisions)):
        (args.output/filename).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    with (args.output/"q2_cases.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    if not args.skip_plots:
        figures(args.output, q1, rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
