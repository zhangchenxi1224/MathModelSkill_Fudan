"""Run the unchanged selected policy with collector paths resolved inside this delivery."""
from pathlib import Path
import argparse,sys
ROOT=Path(__file__).resolve().parent
HISTORY=ROOT.parent/'02_historical_exploration'
sys.path.insert(0,str(HISTORY/'B_solution/scripts'))
import run_selected_official as runner
runner.MAIN=HISTORY/'B_solution'
runner.PREVIOUS=HISTORY/'B_solution_side_research/synthesis_iteration_20260911'
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--robot-id')
    a=p.parse_args()
    runner.run(a.out,a.robot_id)
