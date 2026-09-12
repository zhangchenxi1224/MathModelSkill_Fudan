"""Package this deployed experiment and its actual evidence, preserving raw logs.

Local archive only. It never uploads data or starts official cases. Existing
first-study delivery is untouched. Every packaged file is verified after write.
"""
from pathlib import Path
import argparse
import datetime as dt
import hashlib
import json
import os
import uuid
import zipfile
from package_delivery import selected as first_study_selected

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_ROUND1 = {
    "benchmark", "protocol_bench_fixture", "protocol_bench_exploratory",
    "integrity_prefix", "integrity_prefix_v2", "validation_design",
}


def digest_stream(stream):
    h = hashlib.sha256()
    while chunk := stream.read(1024*1024):
        h.update(chunk)
    return h.hexdigest()


def selected(path):
    rel = path.relative_to(ROOT)
    parts = rel.parts
    if any(x in {"__pycache__", ".pytest_cache", ".git"} for x in parts):
        return False
    if path.name in {"collector.lock", "STOP_AFTER_CASE"}:
        return False
    if parts[:2] == ("output", "pdf") and path.name.endswith((".build.json", ".qa.json")):
        return True
    if first_study_selected(path):
        return True
    if len(parts) == 1:
        return path.name in {"README.md", "run.py", "pyproject.toml", ".gitignore"}
    if parts[0] in {"src", "tests", "scripts", "docs", "configs", "sources"}:
        return True
    if parts[0] != "results":
        return False
    if parts[1] == "round1":
        return len(parts)<3 or parts[2] not in EXCLUDED_ROUND1
    if parts[1] in {"round2", "pipeline_summary"}:
        return True
    if parts[1] == "calibration" and len(parts)>2:
        return parts[2] not in {"exploratory_practice", "exploratory_protocol_smoke", "round1_prefix"}
    return False


def build_package(output):
    output = Path(output).resolve()
    if output.suffix.lower() != ".zip":
        raise ValueError("Package output must have a .zip suffix")
    report_path = output.with_suffix(".manifest.json")
    if output.exists() or report_path.exists():
        raise FileExistsError("Preserve existing delivery: choose a new package filename")
    # The old study's package is always protected, including if a missing copy
    # is accidentally passed as the new study's destination.
    if output == (ROOT/"output/B题研究与复现材料.zip").resolve():
        raise ValueError("The first-study delivery filename is reserved")
    files = sorted(p for p in ROOT.rglob("*") if p.is_file() and selected(p)
                   and p.resolve() not in {output, report_path} and not p.name.endswith(".building.zip"))
    manifest = []
    for path in files:
        with path.open("rb") as stream:
            file_sha = digest_stream(stream)
        manifest.append({"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": file_sha})
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.stem+"."+uuid.uuid4().hex+".building.zip")
    note = ("Local evidence package for the authorized data-driven PRACTICE experiment, including the first-study selected evidence and paper PDF.\n"
            "Official raw request JSONL and public post-exit metadata are included and may contain the configured team ID.\n"
            "No password is required by or stored in these experiment modules. Do not publish private logs without reviewing them.\n"
            "Official encrypted .jlog files retain their original filenames and bytes. Formal tests were not executed.\n"
            "Read docs/round1_deployment.md and the actual results report. Missing results are not replaced by planned counts.\n"
            "Original absolute paths in immutable provenance records refer to the execution host; use relative package paths to analyze copied data.\n"
            "Baseline source snapshots, later module snapshots, raw failures and prior analysis outputs are retained.\n")
    with zipfile.ZipFile(temporary, "x", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for path, item in zip(files, manifest):
            archive.write(path, "B_solution/"+item["path"])
        archive.writestr("B_solution/PIPELINE_PACKAGE_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("B_solution/PIPELINE_PACKAGE_NOTE.txt", note)
    with zipfile.ZipFile(temporary) as archive:
        for item in manifest:
            with archive.open("B_solution/"+item["path"]) as stream:
                if digest_stream(stream) != item["sha256"]:
                    raise RuntimeError("Packaged file hash mismatch: "+item["path"])
    # Same-directory hard link installs atomically and fails if a target has
    # appeared since preflight. Never use replace(), including on Windows.
    os.link(temporary, output)
    temporary.unlink()
    with output.open("rb") as stream:
        package_sha = digest_stream(stream)
    report = {"verified_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "package": str(output),
              "sha256": package_sha, "bytes": output.stat().st_size, "verified_files": len(manifest),
              "original_files_bytes": sum(r["bytes"] for r in manifest),
              "official_raw_request_files": sum(r["path"].endswith("requests.jsonl") and
                                                (r["path"].startswith("results/round1/cases/") or r["path"].startswith("results/round2/cases/")) for r in manifest),
              "original_encrypted_logs": sum(r["path"].endswith(".jlog") for r in manifest),
              "formal_tests": "not executed", "upload_performed": False}
    with report_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT/"output/B题数据驱动实验部署与证据.zip")
    args = parser.parse_args()
    report = build_package(args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
