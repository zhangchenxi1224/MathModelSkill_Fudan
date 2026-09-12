"""Plot selection preserves actual groups and refuses unfinished validation."""
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import plot_report_figures as plots


def test_only_frozen_variant_and_design_groups_are_selected(tmp_path):
    rows = [{"problem": 3, "partition": phase, "variant": variant, "noise_model": "all",
             "mechanism_id": "__design_mixture__", "pool": pool, "composition_model": model}
            for phase in ("development", "confirmation") for variant in ("l1", "l2") for pool, model in plots.GROUPS]
    path = tmp_path/"summary.json"
    path.write_text(json.dumps(rows))
    chosen = plots.paired_rows(path, 3, "l1", "confirmation")
    assert len(chosen) == 4 and all(r["variant"] == "l1" and r["partition"] == "confirmation" for r in chosen)
    with pytest.raises(ValueError, match="four actual"):
        plots.paired_rows(path, 4, "l1", "confirmation")


def test_final_official_plot_refuses_missing_or_unfinished_cases(tmp_path):
    case = {"case_id": "c1", "attempted": False}
    (tmp_path/"plan.json").write_text(json.dumps({"cases": [case]}))
    (tmp_path/"validation_summary.json").write_text(json.dumps({"cases": [case]}))
    with pytest.raises(ValueError, match="unfinished"):
        plots.validation_figures(tmp_path, tmp_path/"figures")
    case["attempted"] = True
    (tmp_path/"validation_summary.json").write_text(json.dumps({"cases": [case]}))
    with pytest.raises(ValueError, match="terminal audits"):
        plots.validation_figures(tmp_path, tmp_path/"figures")
    assert not (tmp_path/"figures").exists()
