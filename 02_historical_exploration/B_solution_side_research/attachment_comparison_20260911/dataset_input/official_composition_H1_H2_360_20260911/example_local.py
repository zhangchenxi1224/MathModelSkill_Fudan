"""Offline API demo, deliberately NOT a solver or full-clear benchmark."""
import argparse
import json
from dataset import list_cases, make_local_session


def observable_demo(client, problem):
    # This function receives no source count, positions, error seed, or truth.
    results = []
    for channel in range(1, 21):
        response = client.measure(channel, (0.0, 0.0))
        result = {"channel": channel, "measure_result": response["measure_result"]}
        if response["measure_result"] == "direction":
            result["svd_deg"] = response["svd_deg"]
        results.append(result)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hypothesis", choices=["H1", "H2"], default="H1")
    parser.add_argument("--problem", type=int, choices=[3, 4], default=4)
    parser.add_argument("--case-id")
    parser.add_argument("--log", help="Optional new JSONL path outside this dataset")
    args = parser.parse_args()
    candidates = list_cases(hypothesis=args.hypothesis, problem=args.problem)
    row = next((r for r in candidates if r["case_id"] == args.case_id), None) if args.case_id else candidates[0]
    if row is None:
        parser.error("case-id is not in the requested group")
    client, evaluator = make_local_session(row["case_id"], log_path=args.log)
    try:
        client.enter()
        feedback = observable_demo(client, row["problem"])
        client.exit()
        print(json.dumps({"purpose": "offline_api_demo_not_solver_benchmark",
                          "case_id": row["case_id"], "feedback": feedback,
                          "evaluation_only": evaluator.summary()}, ensure_ascii=False, indent=2))
    finally:
        client.close_log()


if __name__ == "__main__":
    main()
