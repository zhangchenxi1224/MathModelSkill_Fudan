"""Command-line entry points; no credential is stored in source or examples."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import socket
import time
from .strategy import Solver, SolverConfig
from .protocol import RobotClient, HTTPTransport


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    local = sub.add_parser("local", help="run a self-built case")
    local.add_argument("--problem", type=int, choices=[3, 4], default=4)
    local.add_argument("--seed", type=int, default=1)
    local.add_argument("--count", type=int, choices=range(10, 17), default=10)
    local.add_argument("--variant", default="joint_triangular")
    local.add_argument("--layout", default="random")
    local.add_argument("--error-mode", default="deterministic")
    local.add_argument("--output", default="results/local_demo")
    batch = sub.add_parser("batch", help="paired self-built experiments")
    batch.add_argument("--split", choices=["tune", "test", "stress"], default="test")
    batch.add_argument("--cases-per-problem", type=int, default=8)
    batch.add_argument("--variants", default="baseline,active,nearest_safe,joint_square,joint_triangular")
    batch.add_argument("--output", default="results/batch")
    official = sub.add_parser("official", help="connect only after an operator starts the intended simulator module")
    official.add_argument("--robot-id", required=True)
    official.add_argument("--problem", type=int, choices=[3, 4], required=True)
    official.add_argument("--base-url", default="http://127.0.0.1:2026")
    official.add_argument("--mode", choices=["practice", "formal"], default="practice")
    official.add_argument("--formal-authorized", action="store_true",
                          help="use only after the user explicitly authorizes formal tests")
    official.add_argument("--variant", default="joint_triangular")
    official.add_argument("--wait-ready", type=float, default=90,
                          help="seconds to wait for a TCP listener before one enter request")
    official.add_argument("--output", required=True)
    args = parser.parse_args()
    from .experiments import generate_case, run_case, run_batch, variant_configs
    if args.command == "local":
        case = generate_case(args.seed, args.problem, args.count, args.layout, error_mode=args.error_mode)
        result = run_case(case, variant_configs(args.problem)[args.variant], args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "complete" else 1
    if args.command == "batch":
        cases = []
        base_seed = {"tune": 11000, "test": 71000, "stress": 91000}[args.split]
        layouts = ["boundary", "center_and_boundary", "clustered", "hidden_outward_last"]
        for p in [3, 4]:
            for i in range(args.cases_per_problem):
                cases.append(generate_case(base_seed+p*100+i, p, 10 if i%2 == 0 else 16,
                                           layouts[i%4] if args.split == "stress" else "random",
                                           ["min", "max", "mixed"][i%3],
                                           ["deterministic", "correlated", "extreme"][i%3]))
        result = run_batch(cases, args.variants.split(","), args.output)
        return 0 if all(r["status"] == "complete" for r in result) else 1
    if args.mode == "formal" and not args.formal_authorized:
        parser.error("Formal runs require explicit user authorization and --formal-authorized.")
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    config = variant_configs(args.problem)[args.variant]
    # Credentials are supplied by command line/environment at execution and are
    # never needed by the simulator's four action endpoints beyond robot_id.
    transport = HTTPTransport(args.base_url)
    client = RobotClient(robot_id=args.robot_id, transport=transport, log_path=str(out/"requests.jsonl"))
    from urllib.parse import urlparse
    url = urlparse(args.base_url)
    until = time.monotonic()+args.wait_ready
    while True:
        try:
            with socket.create_connection((url.hostname, url.port or 80), timeout=.5):
                break
        except OSError:
            if time.monotonic() >= until:
                raise RuntimeError("simulator interface not ready; no official action was sent")
            time.sleep(.25)
    result = Solver(client, config, decision_log=out/"decisions.jsonl").run()
    result["environment"] = "official_"+args.mode
    result["case_code"] = None
    result["source_total"] = None
    result["clear_fraction"] = None
    (out/"result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
