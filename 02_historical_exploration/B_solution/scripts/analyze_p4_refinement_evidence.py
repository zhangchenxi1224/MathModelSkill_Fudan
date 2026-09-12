"""Read-only diagnosis of all round-2 P4 logs; never contacts an endpoint."""
from __future__ import annotations

import collections
import hashlib
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/p4_refinement_diagnostics"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    records, sources = [], []
    for folder in sorted((ROOT / "results/round2/cases").glob("r2-p4-validation-*")):
        assignment = read(folder / "assignment.json")
        result = read(folder / "result.json")
        audit = read(folder / "post_exit_audit.json")
        decisions = [json.loads(line) for line in
                     (folder / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]
        actions = [d for d in decisions if d["event"] in ("measure", "clear")]
        successes = [d for d in actions if d["event"] == "clear"
                     and d["response"]["clear_result"] == "success"]
        n = result["clear_successes"]
        assert n == len(successes) and result["status"] == "complete"
        assert n == audit["source_total_post_exit"]
        last_time = successes[-1]["virtual_time_s"]
        groups = collections.defaultdict(lambda: dict(actions=0, walk_m=0., time_s=0.))
        previous, previous_time = (0., 0.), 0.
        for d in actions:
            key = d["reason"] if d["event"] == "measure" else d["certificate"]["type"]
            g = groups[key]
            g["actions"] += 1
            g["walk_m"] += math.dist(previous, d["position"])
            g["time_s"] += d["virtual_time_s"] - previous_time
            previous, previous_time = d["position"], d["virtual_time_s"]
        assert math.isclose(sum(g["walk_m"] for g in groups.values()),
                            result["walk_distance_m"], abs_tol=1e-6)
        assert math.isclose(sum(g["time_s"] for g in groups.values()),
                            result["total_virtual_time_s"], abs_tol=1e-6)
        record = dict(case_id=assignment["case_id"], arm=assignment["arm"],
                      case_code=audit["case_code"], n=n, n_directed=audit["directional_total_post_exit"],
                      total_time_s=result["total_virtual_time_s"],
                      average_clear_time_s=result["average_clear_time_s"],
                      last_success_time_s=last_time,
                      post_last_success_tail_s=result["total_virtual_time_s"] - last_time,
                      movement_s=result["walk_distance_m"] / 5,
                      measurement_s=5 * result["measures"],
                      switch_s=result["switches"],
                      clear_call_s=3 * result["clear_attempts"] + 2 * n,
                      fallback_targets=result["fallback_targets"],
                      certified_clears=result["certified_clears"],
                      failed_clear_attempts=result["clear_attempts"] - n,
                      attribution=dict(groups),
                      stop_type=result["stop_evidence"]["type"])
        assert record["post_last_success_tail_s"] >= 0
        records.append(record)
        for name in ("assignment.json", "result.json", "post_exit_audit.json", "decisions.jsonl"):
            path = folder / name
            sources.append(dict(path=path.relative_to(ROOT).as_posix(), sha256=sha(path)))
    assert len(records) == 60
    aggregates = {}
    for arm in ("baseline", "candidate"):
        rows = [r for r in records if r["arm"] == arm]
        assert len(rows) == 30
        fields = ("total_time_s", "average_clear_time_s", "post_last_success_tail_s",
                  "movement_s", "measurement_s", "switch_s", "clear_call_s",
                  "fallback_targets", "certified_clears", "failed_clear_attempts")
        means = {field: statistics.fmean(r[field] for r in rows) for field in fields}
        aggregates[arm] = dict(cases=30, mean=means,
                              targets=sum(r["n"] for r in rows),
                              fallback_targets=sum(r["fallback_targets"] for r in rows),
                              certified_clears=sum(r["certified_clears"] for r in rows),
                              time_share={k: means[k] / means["total_time_s"] for k in
                                          ("movement_s", "measurement_s", "switch_s", "clear_call_s")})
    payload = dict(scope="All 60 official practice P4 round-2 cases; no new official actions",
                   interpretation=[
                       "Tail begins after the actual last successful clear and is retrospective only.",
                       "Unknown source count prevents the policy from using this tail as an early-stop oracle.",
                       "Action attribution includes travel into that action, including return from local tasks.",
                       "This is descriptive evidence, not a counterfactual estimate of savings."],
                   aggregates=aggregates, records=records, sources=sources)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "official_cost_diagnosis.json"
    if path.exists():
        old = read(path)
        if old != payload:
            raise RuntimeError("Preserve existing diagnosis; source changed")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(aggregates, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
