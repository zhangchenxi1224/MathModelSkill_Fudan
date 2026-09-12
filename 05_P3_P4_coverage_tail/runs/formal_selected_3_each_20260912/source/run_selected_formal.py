"""Run the user-authorized three formal tests per problem, using the tested snapshot."""
from pathlib import Path
import argparse
import contextlib
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parent
MAIN = ROOT.parents[1] / 'B_solution'
TESTED = ROOT / 'runs/official_coverage_tail_10_each_20260912/source'
sys.path.insert(0, str(MAIN / 'scripts'))
import collect_official_round as c


def bridge():
    c._BRIDGE = subprocess.Popen(
        ['powershell', '-NoProfile', '-File', str(ROOT/'formal_ui.ps1'), '-Server'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding='utf-8', errors='replace', bufsize=1,
        creationflags=subprocess.CREATE_NO_WINDOW)


def observe(folder, filename):
    return c.ui(output=folder/filename)


def home(problem, folder):
    snap = observe(folder, 'before_home_ui.json')
    ns = c.names(snap)
    if '测试已结束' in ns:
        if any(r['name']=='确认' and r['enabled'] for r in snap['items']):
            c.ui('invoke', '确认')
        snap = c.ui()
        back = next((s for s in c.names(snap) if re.fullmatch(r'返回问题[34]正式测试',s)),None)
        if back:
            c.ui('invoke',back)
            time.sleep(.3)
    c.ui('invoke', f'问题{problem}正式测试')
    until = time.monotonic()+20
    while True:
        snap = observe(folder, 'home_ui.json')
        text = '\n'.join(c.names(snap))
        quota = re.search(r'已用\s*(\d+)\s*次，剩余\s*(\d+)\s*次', text)
        if quota:
            return snap, tuple(map(int, quota.groups()))
        if time.monotonic() > until:
            raise RuntimeError('Formal page quota unavailable; see home_ui.json')
        time.sleep(.4)


def finished(folder, code):
    until = time.monotonic()+120
    while True:
        snap = observe(folder, 'post_exit_ui_latest.json')
        ns = c.names(snap)
        if code in ns and '测试已结束' in ns and any(code in s and s.endswith('.jlog') for s in ns):
            c.write_json(folder/'post_exit_ui.json', snap)
            return snap
        if time.monotonic() > until:
            raise RuntimeError('Formal result page not ready; keep simulator open')
        time.sleep(.5)


def save_outcome(case, folder, snap):
    result = json.loads((folder/'result.json').read_text(encoding='utf-8'))
    session = json.loads((folder/'session.json').read_text(encoding='utf-8'))
    ns = c.names(snap)
    text = '\n'.join(ns)
    match = re.search(r'本次案例含干扰源\s*(\d+)\s*个，其中全向\s*(\d+)\s*个、定向\s*(\d+)\s*个', text)
    if not match:
        match = re.search(r'共\s*(\d+)\s*个，\s*全向\s*(\d+)\s*个，\s*定向\s*(\d+)\s*个', text)
    total, omni, directional = map(int, match.groups()) if match else (None, None, None)
    code = session['case_code']
    names = [s for s in ns if s.endswith('.jlog') and code in s]
    logdir = MAIN.parent/'B题/Jammers-simulator-win64/Jammers-simulator/JammersSimulatorData/behavior-logs'
    # Only copy the behavior log for this public case code, never simulator databases.
    files = [logdir/name for name in names]
    if not files:
        files = list(logdir.glob(f'*{code}*.jlog'))
    logs=[]
    for src in files:
        if not src.is_file():
            continue
        dest = folder/'original_logs'/src.name
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(src, dest)
        logs.append(dict(filename=src.name, bytes=dest.stat().st_size,
                         sha256=hashlib.sha256(dest.read_bytes()).hexdigest()))
    audit=dict(environment='official_formal', case_id=case['case_id'], problem=case['problem'],
        protocol='selected_feedback', split='formal', case_code=code,
        source_total_post_exit=total, directional_total_post_exit=directional,
        omnidirectional_total_post_exit=omni, cleared=result['clear_successes'],
        status=result['status'], total_virtual_time_s=result['total_virtual_time_s'],
        original_logs=logs, original_log_sha256=logs[0]['sha256'] if logs else None,
        metadata_source='visible post-exit UI; hidden source counts remain null',
        stop_evidence=result.get('stop_evidence'), recorded_utc=c.utc())
    c.write_json(folder/'post_exit_audit.json', audit)
    print(json.dumps(dict(collected=case['case_id'],case_code=code,status=result['status'],
         cleared=result['clear_successes'],N_public=total,T=result['total_virtual_time_s'],
         logs=len(logs)),ensure_ascii=False),flush=True)
    return audit


def run(out, adopt_ready=False):
    out=Path(out).resolve()
    out.mkdir(parents=True,exist_ok=True)
    chosen=json.loads((TESTED/'release.json').read_text(encoding='utf-8'))
    cases=[dict(case_id=f'formal-p{p}-{i:03d}', problem=p, index=i,
                protocol='selected_feedback',split='formal',selected_policy=chosen['policies'][str(p)])
           for p in (3,4) for i in range(1,4)]
    if not (out/'plan.json').exists():
        c.write_json(out/'plan.json',dict(environment='official_formal',formal_authorized=True,
             user_request='P3、P4正式测试各三次，保留全部日志',planned_cases=6,per_problem=3,cases=cases))
        c.write_json(out/'selection.json',chosen)
        shutil.copytree(TESTED,out/'source')
        for case in cases:
            c.write_json(out/'cases'/case['case_id']/'assignment.json',case)
    for name in ('run_selected_formal.py','formal_ui.ps1'):
        shutil.copy2(ROOT/name,out/'source'/name)
    sys.path.insert(0,str(out/'source'))
    from selected_policy import make_solver
    from bsolver_frozen.protocol import RobotClient, HTTPTransport
    locks=[]
    try:
        for folder in sorted({out,MAIN/'results/round1'},key=str):
            locks.append(c.collection_lock(folder))
        bridge()
        initial=observe(out,'initial_ui.json')
        robot_id=next((m.group(1) for s in c.names(initial) if (m:=re.fullmatch(r'队号\s+(\d+)',s))),None)
        if not robot_id:
            raise RuntimeError('Simulator login unavailable')
        c.status(out,'running',planned_cases=6)
        for case in cases:
            folder=out/'cases'/case['case_id']
            if (folder/'post_exit_audit.json').exists():
                continue
            if (folder/'result.json').exists():
                code=json.loads((folder/'session.json').read_text(encoding='utf-8'))['case_code']
                save_outcome(case,folder,finished(folder,code))
                continue
            if (folder/'requests.jsonl').exists():
                raise RuntimeError('An attempted formal session has no result; no automatic replay')
            if (folder/'launch_intent.json').exists() and not adopt_ready:
                raise RuntimeError('Formal launch already attempted; inspect before adopting its ready session')
            if not adopt_ready:
                snap,(used,remaining)=home(case['problem'],folder)
                if used!=case['index']-1 or remaining<1:
                    raise RuntimeError(f'Formal quota mismatch: used={used}, remaining={remaining}')
                control=f"开始问题{case['problem']}正式测试"
                c.write_json(folder/'launch_intent.json',dict(utc=c.utc(),control=control,used=used,remaining=remaining))
                c.ui('invoke',control)
            until=time.monotonic()+120
            while True:
                snap=observe(folder,'start_ui_latest.json')
                ns=c.names(snap)
                if '继续' in ns and any(f"即将开始问题{case['problem']}正式测试" in s for s in ns):
                    c.write_json(folder/'start_continue.json',snap)
                    action=c.ui('invoke','继续')
                    c.write_json(folder/'start_continue_action.json',dict(utc=c.utc(),**action))
                    continue
                if '确认开始' in ns and any(f"占用问题{case['problem']}的一次正式测试机会" in s for s in ns):
                    c.write_json(folder/'start_confirmation.json',snap)
                    action=c.ui('invoke','确认开始')
                    c.write_json(folder/'start_confirmation_action.json',dict(utc=c.utc(),**action))
                    continue
                if f"问题{case['problem']} 正式 测试" in ns and '等待机器狗进入' in ns:
                    break
                if time.monotonic()>until:
                    raise RuntimeError('Formal start not ready; inspect start_ui_latest.json')
                time.sleep(.5)
            adopt_ready=False
            code=next((s for s in ns if re.fullmatch(r'[A-Z0-9]{4}(?:-[A-Z0-9]{4}){3}',s) and not s.startswith('XXXX')),None)
            if not code:
                raise RuntimeError('Formal ready code unavailable')
            c.write_json(folder/'ready_ui.json',snap)
            c.write_json(folder/'session.json',dict(case_code=code,problem=case['problem'],started_observed_utc=c.utc()))
            client=RobotClient(robot_id=robot_id,transport=HTTPTransport('http://127.0.0.1:2026'),log_path=folder/'requests.jsonl')
            try:
                result=make_solver(client,case['problem'],decision_log=folder/'decisions.jsonl').run()
            finally:
                client.close_log()
            result.update(environment='official_formal',case_id=case['case_id'],selected_policy=case['selected_policy'])
            c.write_json(folder/'result.json',result)
            snap=finished(folder,code)
            save_outcome(case,folder,snap)
            c.status(out,'running',last_case=case['case_id'])
        c.status(out,'finished',planned_cases=6)
        home(4,out)
        observe(out,'final_ui.json')
    except Exception as exc:
        c.status(out,'needs_attention',error=str(exc))
        (out/'exception.txt').write_text(traceback.format_exc(),encoding='utf-8')
        raise
    finally:
        c.close_bridge()
        for lock in reversed(locks):
            lock.close()


class Tee:
    def __init__(self,*streams): self.streams=streams
    def write(self,text):
        for s in self.streams: s.write(text);s.flush()
    def flush(self):
        for s in self.streams:s.flush()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--adopt-ready',action='store_true')
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    with (args.out/'console.log').open('a',encoding='utf-8') as log:
        with contextlib.redirect_stdout(Tee(sys.stdout,log)),contextlib.redirect_stderr(Tee(sys.stderr,log)):
            run(args.out,args.adopt_ready)
