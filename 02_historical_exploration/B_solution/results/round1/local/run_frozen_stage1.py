"""Frozen orchestration: run all arms, disclose development comparisons only."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from bsolver.round_experiments import run_manifest, summarize_run, write_json


def main():
    output = Path(__file__).resolve().parent
    frozen = json.loads((output / "source_manifest.json").read_text(encoding="utf8"))
    for name, expected in frozen["files"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Frozen stage1 dependency changed: {name}")
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf8"))
    rows, timing = run_manifest(manifest, output, workers=8, logs=True, resume="--resume" in sys.argv)
    # Confirmation times are persisted by run_manifest but are not summarized here.
    reliability = {"runs": len(rows), "complete": sum(r["evaluation_complete"] for r in rows),
        "failures": sum(not r["evaluation_complete"] for r in rows),
        "hull_invariant_violations": sum(len(r["hull_invariant_violations"]) for r in rows),
        "confirmation_performance_disclosed": False}
    write_json(output / "aggregate_reliability.json", reliability)
    development = [r for r in rows if r["partition"] == "development"]
    write_json(output / "development/results.json", development)
    summary = summarize_run(development, output / "development")
    print(json.dumps({"timing": timing, "aggregate_reliability": reliability,
                      "development_summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
