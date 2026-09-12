"""Only synthetic fixture releases; these tests never inspect real run results."""
from pathlib import Path
import json
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import package_release as release


def write(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if isinstance(data, dict) else data, encoding="utf-8")
    return path


@pytest.fixture
def fixture_root(tmp_path):
    root = tmp_path / "fixture"
    root.mkdir()
    for name in ("run.py", "solve_local.py", "methods/solver.py", "vendor/bsolver/strategy.py",
                 "README.md", "RESULTS.md", "configs/a.json", "inputs/a.json", "docs/a.md", "tests/test_a.py",
                 "dataset/official_composition_H1_H2_360_20260911/runtime/bsolver_frozen/simulator.py"):
        write(root, name, "fixture content")
    write(root, "campaign/frozen_selection.json", {"choices": {"3": "test", "4": "test"}})
    write(root, "reports/validation_verdict.json", {"passed": True})
    write(root, "reports/independent_audit.json", {"audit_passed": True, "issue_count": 0})
    for stage in release.STAGES:
        for name in ("plan.json", "results.json", "summary.json", "source/run.py", "runtime/bsolver_frozen/simulator.py"):
            write(root, f"runs/{stage}/{name}", "synthetic evidence")
        write(root, f"runs/{stage}/cases/not-for-release/requests.jsonl", "omit trajectories")
    for name in ("methods/__pycache__/a.pyc", "vendor/bsolver/data.bin", ".pytest_cache/nodeids",
                 "docs/__pycache__/bad.py", "reports/INVALID/junk.json", "reports/RUNNING/junk.json",
                 "dataset/a.pyc", "runs/unlisted_stage/results.json", "runs/full/progress.txt"):
        write(root, name, "omit")
    return root


def test_release_manifest_hashes_and_expected_exclusions(fixture_root, tmp_path):
    output = tmp_path / "fixture_release.zip"
    result = release.package_release(output, root=fixture_root)
    manifest = release.verify_archive(output)
    names = {entry["path"] for entry in manifest["files"]}
    assert "solve_local.py" in names and "campaign/frozen_selection.json" in names
    assert "dataset/official_composition_H1_H2_360_20260911/runtime/bsolver_frozen/simulator.py" in names
    assert "runs/confirmation/summary.json" in names and "runs/stress/source/run.py" in names
    assert all("cases/" not in name and "__pycache__" not in name and ".pyc" not in name for name in names)
    assert "vendor/bsolver/data.bin" not in names and "runs/full/progress.txt" not in names
    assert not any("unlisted_stage" in name or "/INVALID/" in name or "/RUNNING/" in name for name in names)
    assert result["verified"] and result["payload_files"] == len(names)
    assert result["sha256"] == release.sha256_file(output)
    assert output.with_name(output.name + ".sha256").read_text().split()[0] == result["sha256"]


@pytest.mark.parametrize("passed", [False, 1, "true", None])
def test_verdict_must_be_boolean_true(fixture_root, tmp_path, passed):
    write(fixture_root, "reports/validation_verdict.json", {"passed": passed})
    output = tmp_path / "refused.zip"
    with pytest.raises(ValueError, match="passed"):
        release.package_release(output, root=fixture_root)
    assert not output.exists()


@pytest.mark.parametrize("audit", [
    {}, {"audit_passed": True}, {"issue_count": 0},
    {"audit_passed": False, "issue_count": 0},
    {"audit_passed": 1, "issue_count": 0},
    {"audit_passed": True, "issue_count": 1},
    {"audit_passed": True, "issue_count": False},
    {"audit_passed": True, "issue_count": "0"},
    {"audit_passed": True, "issue_count": 0.0},
])
def test_independent_audit_requires_pass_and_zero_issues(fixture_root, tmp_path, audit):
    write(fixture_root, "reports/independent_audit.json", audit)
    output = tmp_path / "audit-refused.zip"
    with pytest.raises(ValueError, match="independent audit"):
        release.package_release(output, root=fixture_root)
    assert not output.exists()


@pytest.mark.parametrize("missing", release.REQUIRED)
def test_required_final_evidence_missing_refused(fixture_root, tmp_path, missing):
    (fixture_root / missing).unlink()
    with pytest.raises(ValueError, match="missing"):
        release.package_release(tmp_path / "refused.zip", root=fixture_root)


def test_existing_zip_or_checksum_never_overwritten(fixture_root, tmp_path):
    output = tmp_path / "existing.zip"
    output.write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError):
        release.package_release(output, root=fixture_root)
    assert output.read_text() == "existing"
    second = tmp_path / "checksum-existing.zip"
    second.with_name(second.name + ".sha256").write_text("existing hash", encoding="utf-8")
    with pytest.raises(FileExistsError):
        release.package_release(second, root=fixture_root)
    assert not second.exists()


@pytest.mark.parametrize("marker", ["RUNNING.lock", "INVALID.json"])
def test_running_or_invalid_stage_refused(fixture_root, tmp_path, marker):
    write(fixture_root, "runs/full/" + marker, "fixture")
    with pytest.raises(ValueError, match="running or invalid"):
        release.package_release(tmp_path / "refused.zip", root=fixture_root)


def test_archive_payload_tamper_is_detected(fixture_root, tmp_path):
    original = tmp_path / "good.zip"
    release.package_release(original, root=fixture_root)
    changed = tmp_path / "tampered.zip"
    with zipfile.ZipFile(original) as src, zipfile.ZipFile(changed, "w") as dst:
        for name in src.namelist():
            dst.writestr(name, b"changed" if name == "README.md" else src.read(name))
    with pytest.raises(ValueError, match="checksum"):
        release.verify_archive(changed)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/drive", "a\\b", "a/../b", "a//b"])
def test_unsafe_zip_name_rejected(name):
    assert not release._safe_name(name)


def test_symbolic_file_escape_rejected(fixture_root, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = fixture_root / "docs/link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable on this host")
    with pytest.raises(ValueError, match="escapes root or is linked"):
        release.package_release(tmp_path / "refused.zip", root=fixture_root)
