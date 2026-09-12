"""Run the selected release on one local world; ground truth stays in the simulator."""
from pathlib import Path
import argparse,json
import run
from selected_policy import make_solver
ROOT=Path(__file__).resolve().parent

def selected(client,problem,base_spec=None,arm=None,*,prior=None,options=None,decision_log=None):
    return make_solver(client,problem,decision_log=decision_log)

def main():
    p=argparse.ArgumentParser();p.add_argument('--problem',type=int,choices=[3,4],required=True)
    p.add_argument('--dataset',default=str(ROOT/'data/fresh_confirmation240.json'))
    p.add_argument('--index',type=int,default=0);p.add_argument('--output',required=True);args=p.parse_args()
    cases=[c for c in run.load_cases(args.dataset) if c['problem']==args.problem]
    run.make_solver=selected
    result=run.run_one(cases[args.index],'selected',args.output,None,{})
    print(json.dumps({k:result[k] for k in ['case_id','problem','complete','source_total','total_virtual_time_s','T_per_source_s','wall_s']},ensure_ascii=False))

if __name__=='__main__':main()
