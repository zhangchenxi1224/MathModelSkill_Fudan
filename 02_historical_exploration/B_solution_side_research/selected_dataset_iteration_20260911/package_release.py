"""Create and verify a portable evidence release after final validation passes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import zipfile


ROOT = Path(__file__).resolve().parent
MANIFEST = "release_manifest.json"
REQUIRED = ("RESULTS.md", "campaign/frozen_selection.json",
            "reports/validation_verdict.json", "reports/independent_audit.json")
DATA_TREES = ("configs", "campaign", "inputs", "dataset", "docs", "tests", "reports")
STAGES = ("round1", "round2", "round3", "full", "confirmation", "stress")
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", "RUNNING", "INVALID"}
EXCLUDED_FILES = {"RUNNING.lock", "INVALID.json"}


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_name(name):
    path = PurePosixPath(name)
    return (bool(name) and not path.is_absolute() and "\\" not in name and ":" not in name
            and all(part not in ("", ".", "..") for part in name.split("/")))


def _excluded(path):
    return (bool(set(path.parts) & EXCLUDED_PARTS) or path.name in EXCLUDED_FILES
            or path.suffix.lower() in (".pyc", ".pyo"))


def _is_link(path):
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _checked_file(path, root):
    linked_parent = any(_is_link(parent) for parent in path.parents if parent != root and parent.is_relative_to(root))
    if _is_link(path) or linked_parent or not path.resolve().is_relative_to(root):
        raise ValueError(f"release path escapes root or is linked: {path}")
    if not path.is_file():
        raise ValueError(f"required release file is missing: {path}")
    name = path.relative_to(root).as_posix()
    if not _safe_name(name) or name == MANIFEST:
        raise ValueError(f"unsafe or reserved archive path: {name}")
    return name


def check_release_ready(root):
    root = Path(root).resolve()
    for name in REQUIRED:
        _checked_file(root / name, root)
    verdict = json.loads((root / "reports/validation_verdict.json").read_text(encoding="utf-8-sig"))
    if not isinstance(verdict, dict) or verdict.get("passed") is not True:
        raise ValueError("reports/validation_verdict.json must have passed: true")
    audit = json.loads((root / "reports/independent_audit.json").read_text(encoding="utf-8-sig"))
    if (not isinstance(audit, dict) or audit.get("audit_passed") is not True
            or type(audit.get("issue_count")) is not int or audit["issue_count"] != 0):
        raise ValueError("independent audit must have audit_passed: true and integer issue_count: 0")
    for stage in STAGES:
        directory = root / "runs" / stage
        if (directory / "RUNNING.lock").exists() or (directory / "INVALID.json").exists():
            raise ValueError(f"cannot package running or invalid stage: {stage}")


def _tree_files(directory, root, *, python_only=False):
    if not directory.exists():
        return
    if _is_link(directory) or not directory.resolve().is_relative_to(root):
        raise ValueError(f"release directory escapes root or is linked: {directory}")
    for current, dirs, names in os.walk(directory, followlinks=False):
        current = Path(current)
        dirs[:] = sorted(name for name in dirs if not _excluded(Path(name)))
        for name in dirs:
            path = current / name
            if _is_link(path) or not path.resolve().is_relative_to(root):
                raise ValueError(f"linked or escaping directory in release: {path}")
        for name in sorted(names):
            path = current / name
            if _excluded(path.relative_to(root)) or (python_only and path.suffix != ".py"):
                continue
            _checked_file(path, root)
            yield path


def collect_files(root, *, excluded_paths=()):
    root = Path(root).resolve()
    excluded = {Path(p).resolve() for p in excluded_paths}
    chosen = set(root.glob("*.py"))
    chosen.update(root / name for name in ("README.md", "RESULTS.md", "pytest.ini")
                  if (root / name).is_file())
    for name in ("methods", "vendor"):
        chosen.update(_tree_files(root / name, root, python_only=True))
    for name in DATA_TREES:
        chosen.update(_tree_files(root / name, root))
    for stage in STAGES:
        directory = root / "runs" / stage
        chosen.update(directory / name for name in ("plan.json", "results.json", "summary.json")
                      if (directory / name).is_file())
        for folder in ("source", "runtime"):
            chosen.update(_tree_files(directory / folder, root))
    files = []
    for path in chosen:
        if path.resolve() in excluded or _excluded(path.relative_to(root)):
            continue
        files.append((_checked_file(path, root), path))
    files.sort(key=lambda item: item[0])
    return files


def verify_archive(path):
    """Read every archived payload again and verify the manifest exhaustively."""
    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(not _safe_name(name) for name in names):
            raise ValueError("archive contains duplicate or unsafe paths")
        manifest = json.loads(archive.read(MANIFEST))
        entries = manifest["files"]
        listed = [entry["path"] for entry in entries]
        if (len(listed) != len(set(listed)) or set(names) != set(listed) | {MANIFEST}
                or manifest["file_count"] != len(entries)):
            raise ValueError("archive file list differs from release manifest")
        total = 0
        for entry in entries:
            if entry["source_relative_path"] != entry["path"]:
                raise ValueError("manifest source path does not match archive path")
            data = archive.read(entry["path"])
            if len(data) != entry["size_bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError(f"archive checksum mismatch: {entry['path']}")
            total += len(data)
        if total != manifest["payload_bytes"]:
            raise ValueError("archive size total differs from manifest")
    return manifest


def package_release(output, *, root=ROOT):
    root = Path(root).resolve()
    output = Path(output).resolve()
    checksum = output.with_name(output.name + ".sha256")
    if output.suffix.lower() != ".zip":
        raise ValueError("output must have a .zip extension")
    if output.exists() or checksum.exists():
        raise FileExistsError("release ZIP or checksum already exists; use a new path")
    check_release_ready(root)
    chosen = collect_files(root, excluded_paths=(output, checksum))
    if not chosen:
        raise ValueError("release contains no files")
    output.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    # Exclusive creation also protects against a path appearing after checks.
    with output.open("xb") as stream:
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name, path in chosen:
                _checked_file(path, root)
                data = path.read_bytes()
                archive.writestr(name, data)
                entries.append({"path": name, "source_relative_path": name,
                                "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            manifest = {"format": "selected-dataset-portable-release-v1",
                        "created_utc": datetime.now(timezone.utc).isoformat(),
                        "file_count": len(entries), "payload_bytes": sum(e["size_bytes"] for e in entries),
                        "manifest_excludes_itself": True, "files": entries,
                        "gates": {"required_files": list(REQUIRED), "validation_verdict_passed": True,
                                  "independent_audit_passed": True, "independent_audit_issue_count": 0},
                        "trajectory_logs_included": False}
            archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    verified = verify_archive(output)
    archive_hash = sha256_file(output)
    with checksum.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(f"{archive_hash}  {output.name}\n")
    return {"output": str(output), "sha256_file": str(checksum), "sha256": archive_hash,
            "payload_files": verified["file_count"], "archive_entries": verified["file_count"] + 1,
            "payload_bytes": verified["payload_bytes"], "zip_bytes": output.stat().st_size,
            "verified": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="New ZIP file; existing ZIP/checksum paths are refused")
    args = parser.parse_args(argv)
    try:
        result = package_release(args.output)
    except Exception as exc:
        print(json.dumps({"status": "refused_or_failed", "error": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
