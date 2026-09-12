"""Read-only public-log calibration. No official API or GUI operation occurs."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bsolver.calibration import calibrate, SPLIT_SALT


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", nargs="+", type=Path, required=True, help="Official case roots, searched recursively")
    parser.add_argument("--compare", nargs="*", type=Path, default=[], help="Local roots for identical-protocol observable comparisons")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exploratory", action="store_true", help="Force exploratory status for legacy or preliminary data")
    parser.add_argument("--split-salt", default=SPLIT_SALT)
    parser.add_argument("--fit-fraction", type=float, default=.75)
    parser.add_argument("--prior-effective-cases", type=float, default=2.)
    parser.add_argument("--bootstrap", type=int, default=500)
    args = parser.parse_args(argv)
    model = calibrate(args.input, args.output, compare_roots=args.compare,
                      exploratory=args.exploratory, salt=args.split_salt, fit_fraction=args.fit_fraction,
                      prior_effective_cases=args.prior_effective_cases, bootstrap_repeats=args.bootstrap)
    print(json.dumps({"model": str(args.output / "model.json"), "status": model["model_status"],
                      "provenance": model["provenance"],
                      "fit_joint_cases": {p: m["n_fit_known_joint"] for p, m in model["problems"].items()}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
