"""Small fixtures for evidence completeness, packaging and missing validation data."""
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]/"scripts"
sys.path.insert(0, str(SCRIPTS))


def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS/(name+".py"))
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


package = module("package_round_pipeline")
summary = module("summarize_round_pipeline")
plots = module("plot_official_validation")
report_pdf = module("build_research_report_pdf")


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def test_package_does_not_replace_existing_or_earlier_delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(package, "ROOT", tmp_path)
    monkeypatch.setattr(package, "first_study_selected", lambda path: path.suffix in (".py", ".pdf"))
    source = tmp_path/"src/a.py"
    source.parent.mkdir()
    source.write_text("fixture only")
    out = tmp_path/"output/new.zip"
    result = package.build_package(out)
    assert result["verified_files"] == 1
    with zipfile.ZipFile(out) as archive:
        assert archive.read("B_solution/src/a.py") == b"fixture only"
    old = out.read_bytes()
    with pytest.raises(FileExistsError):
        package.build_package(out)
    assert out.read_bytes() == old
    with pytest.raises(ValueError, match="reserved"):
        package.build_package(tmp_path/"output/B题研究与复现材料.zip")


def test_package_keeps_all_required_new_evidence_families():
    names = ["results/round1/cases/c/original_logs/practice.jlog", "results/round1/cases/c/requests.jsonl",
             "results/round1/local/development/results.json", "results/round1/posterior_archives/c/posterior_archive.json",
             "results/calibration/radius_extreme_witnesses/summary.json", "results/calibration/model_check_plot_design.json",
             "results/round2/validation_freeze.json", "results/round2/cases/c/start_failure.json",
             "results/code_snapshots/baseline/src.py", "output/pdf/new_study.pdf",
             "output/pdf/new_study.build.json", "output/pdf/new_study.qa.json",
             "results/pipeline_summary/report_visual_qa/page-01.png"]
    assert all(package.selected(package.ROOT/name) for name in names)
    assert not package.selected(package.ROOT/"results/round2/collector.lock")


def test_missing_audit_attempts_and_not_started_remain_separate(tmp_path):
    cases = [{"case_id": name, "problem": 3, "protocol": "baseline", "split": "fit"} for name in ("ok", "failed", "pending")]
    save(tmp_path/"plan.json", {"cases": cases})
    for case in cases:
        save(tmp_path/"cases"/case["case_id"]/"assignment.json", case)
    save(tmp_path/"cases/ok/result.json", {"case_id": "ok", "status": "complete", "total_virtual_time_s": 100,
                                          "clear_attempts": 10, "clear_successes": 10})
    save(tmp_path/"cases/ok/post_exit_audit.json", {"status": "complete", "N": 10, "Ndir": 0, "cleared": 10,
                                                   "total_virtual_time_s": 100})
    save(tmp_path/"cases/failed/start_failure.json", {"error": "fixture start failure"})
    rows, _ = summary.collect_official(tmp_path)
    value = summary.summarize(rows)
    assert value["attempts"] == 2 and value["complete"] == 1 and value["failures"] == 1 and value["not_started"] == 1
    assert value["penalized_loss_mean_s"] == 180050
    assert value["completion_time_mean_s"] == 100
    assert next(r for r in rows if r["case_id"] == "failed")["audit_missing"]


def test_empty_missing_statistics_and_stale_csv_are_not_zero(tmp_path):
    value = summary.summarize([{"attempted": True, "evaluation_complete": False}])
    assert value["failed_clear_attempts_mean"] is None and value["walk_distance_m_mean"] is None
    assert summary.summarize([])["penalized_loss_mean_s"] is None
    csv = tmp_path/"stale.csv"
    csv.write_text("old apparent stage results")
    summary.csv_write(csv, [])
    assert "old" not in csv.read_text(encoding="utf-8-sig")


def test_plot_partial_component_denominator_and_single_case_interval():
    rows = [{"problem": 3, "arm": "baseline", "attempted": True, "components": {k: 10 for k in plots.COMPONENT_KEYS}},
            {"problem": 3, "arm": "baseline", "attempted": True, "components": {}},
            {"problem": 4, "arm": "baseline", "attempted": True, "components": {k: 900 for k in plots.COMPONENT_KEYS}}]
    components, n = plots.components_from_cases(rows, 3, "baseline")
    assert n == 1 and components["movement_s"] == 10
    assert plots.components_from_cases(rows, 3, "candidate") == (None, 0)
    assert "CI unavailable" in plots.difference_text({"difference": 2, "ci95": [None, None]}, "C-B")


def test_pdf_wrapper_protects_old_pdf_and_any_existing_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(report_pdf, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="reserved"):
        report_pdf.protected_output(tmp_path/"output/pdf/B题研究论文.pdf")
    existing = tmp_path/"report.pdf"
    existing.write_bytes(b"old artifact")
    with pytest.raises(FileExistsError):
        report_pdf.protected_output(existing)
    assert existing.read_bytes() == b"old artifact"


def test_execution_inventory_does_not_read_withheld_results(tmp_path):
    save(tmp_path/"run_metadata.json", {"strategy_runs": 4000, "distinct_scenarios_run": 1000})
    # Reading this would fail. The helper must use aggregate counts only.
    (tmp_path/"results.json").write_text("WITHHELD PERFORMANCE")
    dev = [{"case_id": f"d{i}", "variant": "l1", "partition": "development"} for i in range(3)]
    confirmation = [{"case_id": "c1", "variant": "l1", "partition": "confirmation"}]
    inventory, sources = summary.execution_inventory([(tmp_path/"development", dev), (tmp_path/"confirmation", confirmation)])
    assert len(inventory) == 1
    item = inventory[0]
    assert item["physical_strategy_runs"] == 4000 and item["published_analysis_rows"] == 4
    assert item["executed_rows_not_in_this_analysis"] == 3996
    assert sources == [tmp_path/"run_metadata.json"]
    with pytest.raises(ValueError, match="double-count"):
        summary.execution_inventory([(tmp_path/"development", dev), (tmp_path/"confirmation", dev)])
