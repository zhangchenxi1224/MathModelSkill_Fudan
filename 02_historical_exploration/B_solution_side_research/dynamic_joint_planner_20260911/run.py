"""Portable entry point: offline only, no service credentials or remote calls."""
import argparse
import json
from pathlib import Path
from evaluate import ROOT, solve_case, smoke


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    one=commands.add_parser('solve',help='evaluate one supplied synthetic JSON case')
    one.add_argument('--case',required=True)
    one.add_argument('--output',required=True,help='new directory; never overwrite')
    one.add_argument('--policy',choices=['dynamic','champion'],default='dynamic')
    one.add_argument('--config',default=str(ROOT/'configs/dynamic_h2.json'))
    small=commands.add_parser('smoke',help='8 new paired engineering cases')
    small.add_argument('--output',required=True)
    small.add_argument('--workers',type=int,choices=range(1,9),default=2)
    small.add_argument('--include-h1',action='store_true')
    args=parser.parse_args()
    if args.command=='solve':
        r=solve_case(args.case,args.output,args.policy,args.config)
        print(json.dumps({k:r[k] for k in ['status','audit_complete','policy','source_total',
                         'total_virtual_time_s','program_real_time_s','completed_plans','budget_fallbacks'] if k in r},ensure_ascii=False))
        return 0 if r['audit_complete'] else 1
    r=smoke(args.output,args.workers,args.include_h1)
    print(json.dumps(r,ensure_ascii=False,indent=2))
    return 0 if r['all_audited_complete'] and r['source_valid'] else 1


if __name__=='__main__': raise SystemExit(main())
