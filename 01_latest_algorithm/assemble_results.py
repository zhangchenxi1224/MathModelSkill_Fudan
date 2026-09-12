"""Assemble final rows, replacing every run that reached the old compute guard."""
from pathlib import Path
import argparse,json
from run import read,write

def guard_hits(path):
    text=(path.parent/"decisions.jsonl").read_text(encoding="utf-8")
    return text.count('"elapsed_budget_reached": true')+text.count('"elapsed_budget_reached":true')

def main():
    p=argparse.ArgumentParser();p.add_argument("dataset",choices=["new240","core960"]);a=p.parse_args()
    cases=read("inputs/new240/cases.json") if a.dataset=="new240" else read("inputs/core960/p3.json")+read("inputs/core960/p4.json")
    names={3:"new240_p3_dp",4:"new240_p4_fast"} if a.dataset=="new240" else {3:"core960_p3_dp",4:"core960_p4_fast"}
    config=read("selection.json")
    cache=read("inputs/budget_scan.json")
    repairs={}
    for f in Path("runs").glob("budget_repair_round*/cases/*/*/result.json"):
        r=read(f)
        if r["world_key"] in repairs:raise ValueError("Duplicate repair world")
        repairs[r["world_key"]]=(f,r)
    assembled=[];pending=[]
    for c in cases:
        choice=config["policies"][str(c["problem"])]
        source=Path("runs")/names[c["problem"]]/"cases"/c["_world_key"][:16]/choice["arm"]/"result.json"
        if not source.exists():pending.append(c["case_id"]);continue
        r=read(source);key=str(source)
        if key not in cache:cache[key]=dict(world_key=c["_world_key"],hits=guard_hits(source))
        hits=cache[key]["hits"]
        used=source
        if hits:
            if c["_world_key"] not in repairs:pending.append(c["case_id"]);continue
            used,r=repairs[c["_world_key"]]
            if guard_hits(used):raise ValueError("Repair still reached emergency guard: "+str(used))
        r={**r,"original_result_path":str(source.resolve()),"used_result_path":str(used.resolve()),
           "requests_path":str((used.parent/"requests.jsonl").resolve()),
           "decisions_path":str((used.parent/"decisions.jsonl").resolve()),
           "original_planning_guard_hits":hits,"compute_guard_rerun":bool(hits),
           "effective_selection":choice,
           "reuse_basis":"rerun with full fixed work budget" if hits else "original run never reached the shorter guard; full fixed work budget already executed"}
        assembled.append(r)
    write("inputs/budget_scan.json",cache)
    if pending:raise RuntimeError(f"{len(pending)} runs or budget reruns pending: {pending[:6]}")
    out=Path("runs")/("final_"+a.dataset)
    out.mkdir(exist_ok=False)
    for r in assembled:write(out/"cases"/r["world_key"][:16]/r["arm"]/"result.json",r)
    write(out/"results.json",assembled)
    write(out/"provenance.json",dict(dataset=a.dataset,selection=config,worlds=len(cases),
          compute_guard_reruns=sum(r["compute_guard_rerun"] for r in assembled),
          note="All raw runs are preserved. Replacement depends only on the logged computation-guard flag, not on the score. Raw logs are referenced by each result."))
    print(json.dumps(dict(dataset=a.dataset,runs=len(assembled),complete=sum(r["complete"] for r in assembled),
                         compute_guard_reruns=sum(r["compute_guard_rerun"] for r in assembled))))
if __name__=="__main__":main()
