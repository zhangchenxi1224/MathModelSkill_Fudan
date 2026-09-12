"""Data-integrity checks for the offline visualization adapter."""
import copy
import importlib.util
from pathlib import Path

import pytest


PATH = Path(__file__).resolve().parents[1]/"scripts/plot_model_checks.py"
SPEC = importlib.util.spec_from_file_location("plot_model_checks", PATH)
plotter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plotter)


def metric(name, n=5, status="insufficient_evidence"):
    return {"metric": name, "official_mean": .3, "local_mean": .4,
            "difference_local_minus_official": .1, "difference_ci95": [-.6, .7],
            "bootstrap_difference_ci95": [.1, .1], "n_official_development": n,
            "n_local": 20, "status": status, "practical_margin": .1,
            "bootstrap_unit": "whole_case"}


def row(name="z", status="insufficient_evidence", metrics=None):
    return {"model_candidate": name, "problem": 4, "protocol": "survey",
            "n_official_development": 5, "n_local": 20, "status": status,
            "official_reference": "development_only", "metrics": metrics or []}


def test_all_models_and_statuses_retained_in_fixed_order():
    rows = [row("z", "matched"), row("a", "biased"), row("m")]
    group = plotter.grouped_checks({"candidate_checks": rows})[4, "survey"]
    assert [r["model_candidate"] for r in group] == ["a", "m", "z"]
    assert len(plotter.metric_catalog(group)["core"]) == 10
    assert all(plotter.scalar_payload(r, "@origin")["missing"] for r in group)


def test_copy_widened_interval_never_narrow_bootstrap():
    source = row(metrics=[metric("completion_fraction")])
    original = copy.deepcopy(source)
    result = plotter.scalar_payload(source, "completion_fraction")
    assert result["difference_ci95"] == [-.6, .7]
    assert result["difference_ci95"] != result["bootstrap_difference_ci95"]
    assert not result["small_n"]
    assert source == original
    source["metrics"][0]["n_official_development"] = 4
    assert plotter.scalar_payload(source, "completion_fraction")["small_n"]


def test_origin_requires_coordinates_and_keeps_distinct_designs():
    names = ["coverage_station_0@0.0000,0.0000_visibility",
             "coverage_station_0@100.0000,0.0000_visibility",
             "joint_a_mean_visible_station_fraction", "joint_b_mean_visible_station_fraction"]
    catalog = plotter.metric_catalog([row(metrics=[metric(n) for n in names])])
    selected = [r["metric"] for r in catalog["core"]]
    assert names[0] in selected and names[1] not in selected
    assert names[2] in selected and names[3] in selected


def test_joint_family_keeps_every_bin_and_pair_without_value_filter():
    names = [f"joint_a_visible_count_probability_{k:03d}" for k in range(32)]
    names += [f"joint_a_pair_{i:03d}_{i+1:03d}_{kind}_probability"
              for i in range(6) for kind in ("both_visible", "discordant")]
    rows = [row(metrics=[metric(n, status="screen_flag" if i % 2 else "matched") for i, n in enumerate(names)])]
    catalog = plotter.metric_catalog(rows)
    assert len(catalog["counts"]["a"]) == 32
    assert len(catalog["pairs"]["a"]) == 12


def test_declared_design_immutable_and_no_result_read(tmp_path, monkeypatch):
    target = tmp_path/"design.json"
    result = plotter.declare(target)
    assert not result["read_result_files"]
    assert plotter.declare(target) == result
    changed = copy.deepcopy(result)
    changed["design"]["fixed_core"] = []
    plotter.save(target, changed)
    with pytest.raises(ValueError, match="differs"):
        plotter.declare(target)


def test_reject_duplicate_groups_and_malformed_intervals():
    with pytest.raises(ValueError, match="Duplicate candidate"):
        plotter.grouped_checks({"candidate_checks": [row(), row()]})
    bad = metric("x")
    bad["difference_ci95"] = [.7, -.6]
    with pytest.raises(ValueError, match="Reversed"):
        plotter.scalar_payload(row(metrics=[bad]), "x")
