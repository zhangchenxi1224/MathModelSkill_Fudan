"""Offline CLI for set-valued archives; reads public request and aggregate logs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from bsolver.posterior_archive import archive_requests  # noqa: E402


def read_object(path):
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: expected an object")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True,
                        help="case directory, parent directory, or requests.jsonl; repeatable")
    parser.add_argument("--output", required=True, help="new directory for per-case archives and index")
    parser.add_argument("--problem", type=int, choices=(3, 4),
                        help="explicit override when public metadata does not supply the problem")
    parser.add_argument("--epsilon", type=float, default=1.0051)
    args = parser.parse_args(argv)
    paths = set()
    for source in map(Path, args.input):
        if not source.exists():
            parser.error(f"input does not exist: {source}")
        if source.is_file():
            paths.add(source.resolve())
        else:
            paths.update(path.resolve() for path in source.rglob("requests.jsonl"))
    output_root = Path(args.output).resolve()
    index = []
    used_ids = set()
    for path in sorted(paths):
        entry = {"requests": str(path), "status": "error"}
        try:
            assignment = read_object(path.parent/"assignment.json")
            result = read_object(path.parent/"result.json")
            audit = read_object(path.parent/"post_exit_audit.json")
            problem = (args.problem or assignment.get("problem") or result.get("problem")
                       or audit.get("problem") or (result.get("config") or {}).get("problem"))
            if problem not in (3, 4):
                raise ValueError("problem unavailable in public metadata; pass --problem")
            case_id = str(assignment.get("case_id") or result.get("case_id") or path.parent.name)
            if case_id in used_ids:
                raise ValueError(f"duplicate case_id {case_id}; use separate output roots")
            # A metadata identifier cannot choose a path outside the output dir.
            if Path(case_id).name != case_id or case_id in ("", ".", "..") or any(c in case_id for c in "/\\:"):
                raise ValueError("case_id must be a plain directory name")
            used_ids.add(case_id)
            # Collector uses the descriptive *_post_exit fields. Retain the
            # short aliases for hand-authored fixtures and older public audits.
            counts = {}
            for key, alias in (("N", "source_total_post_exit"),
                               ("Ndir", "directional_total_post_exit")):
                value = audit.get(key)
                if value is None:
                    value = audit.get(alias)
                if value is not None:
                    counts[key] = value
            target = output_root/case_id/"posterior_archive.json"
            if target.resolve() == path:
                raise ValueError("output must not overwrite the input request journal")
            archive = archive_requests(path, target, problem=problem, case_id=case_id,
                                       epsilon_deg=args.epsilon, post_exit_counts=counts)
            entry.update(case_id=case_id, problem=problem, archive=str(target),
                         status=archive["status"], issues=archive["issues"],
                         requests_sha256=archive["requests_sha256"],
                         exit_confirmed=archive["exit_confirmed"],
                         confirmed_present_channels=sum(channel["existence"] == "confirmed_present"
                                                        for channel in archive["channels"].values()),
                         empty_outer_hull_channels=[int(f) for f, state in archive["channels"].items()
                                                    if state["position_outer_empty"]])
        except (ValueError, TypeError, OSError) as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
        index.append(entry)
    output_root.mkdir(parents=True, exist_ok=True)
    manifest = {"archive_interpretation": "conservative sets and symbolic constraints, not identified parameters",
                "case_count": len(index), "cases": index}
    (output_root/"index.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n",
                                           encoding="utf-8")
    print(json.dumps({"cases": len(index), "errors": sum(e["status"] == "error" for e in index),
                      "audit_issues": sum(e["status"] == "audit_issues" for e in index),
                      "index": str(output_root/"index.json")}, ensure_ascii=False))
    return int(any(entry["status"] == "error" for entry in index))


if __name__ == "__main__":
    raise SystemExit(main())
