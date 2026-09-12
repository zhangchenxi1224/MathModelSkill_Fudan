"""Run one supplied local world; never connect to the official simulator."""
import argparse
import json
from pathlib import Path
from benchmark import ROOT,list_cases,run_one
from policies import SPECS

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--case-id',required=True)
    ap.add_argument('--policy',default='selected',choices=['selected']+list(SPECS))
    ap.add_argument('--output',required=True)
    args=ap.parse_args()
    row=next((r for r in list_cases() if r['case_id']==args.case_id),None)
    if row is None:ap.error('case-id is not in supplied dataset')
    policy=args.policy
    if policy=='selected':
        picked=json.loads((ROOT/'report/comparison.json').read_text(encoding='utf-8'))['recommendations']
        policy=picked[str(row['problem'])]
    dest=ROOT/args.output
    if dest.exists():ap.error('output already exists; use a new directory')
    print(json.dumps(run_one((row,policy,str(dest))),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
