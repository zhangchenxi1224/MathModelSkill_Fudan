from pathlib import Path
import run
from solve import selected
ROOT=Path(__file__).resolve().parent

def main():
    release=run.read(ROOT/'release.json');cases=run.load_cases(ROOT/'data/fresh_confirmation240.json');checks=[]
    run.make_solver=selected
    for p in (3,4):
        case=next(c for c in cases if c['problem']==p)
        choice=release['policies'][str(p)]['candidate']
        expected=next(r for r in run.read(ROOT/f'runs/confirmation_p{p}/results.json') if r['world_key']==case['_world_key'] and r['arm']==choice)
        result=run.run_one(case,'selected',ROOT/'verification/entry_runs',None,{})
        matched=result['complete'] and abs(result['total_virtual_time_s']-expected['total_virtual_time_s'])<1e-6
        checks.append(dict(problem=p,candidate=choice,world_key=case['_world_key'],matched=matched,
                      T=result['total_virtual_time_s'],expected_T=expected['total_virtual_time_s']))
    run.write(ROOT/'verification/selected_entry.json',checks)
    if not all(c['matched'] for c in checks):raise ValueError('Selected entry mismatch')
    print(checks)

if __name__=='__main__':main()
