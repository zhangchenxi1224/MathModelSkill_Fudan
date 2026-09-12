"""Resume-safe PRACTICE collection through visible UI and four published endpoints.

Never starts formal tests; never reads simulator databases or target truth files.
Post-exit counts come only from the application's visible accessibility tree.
"""
from pathlib import Path
import argparse
import datetime as dt
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
_BRIDGE = None


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def prepare(out, seed=20260911):
    plan_path = out / 'plan.json'
    if plan_path.exists():
        return json.loads(plan_path.read_text(encoding='utf-8'))
    records = []
    for problem in (3, 4):
        for protocol, number in [('baseline', 60), ('survey', 20)]:
            group = []
            for i in range(1, number + 1):
                case_id = f'r1-p{problem}-{protocol}-{i:03d}'
                h = hashlib.sha256(f'{seed}:{case_id}'.encode()).hexdigest()
                group.append(dict(case_id=case_id, problem=problem, protocol=protocol,
                                  survey_seed=int(h[:8], 16), allocation_hash=h,
                                  environment='official_practice', round=1))
            development = {r['case_id'] for r in sorted(group, key=lambda x:x['allocation_hash'])[:number//4]}
            for r in group:
                r['split'] = 'development' if r['case_id'] in development else 'fit'
            records.extend(group)
    pilots = ['r1-p3-baseline-001', 'r1-p4-baseline-001', 'r1-p3-survey-001', 'r1-p4-survey-001']
    tail = [r for r in records if r['case_id'] not in pilots]
    random.Random(seed).shuffle(tail)
    by_id = {r['case_id']:r for r in records}
    ordered = [by_id[k] for k in pilots] + tail
    for i, r in enumerate(ordered):
        r.update(sequence=i, pilot=i < 4, assigned_utc=utc())
        write_json(out/'cases'/r['case_id']/'assignment.json', r)
    plan = dict(created_utc=utc(), seed=seed, source='user_authorized_practice_round',
                allocation='whole-case; 25% hash-ranked development within problem/protocol',
                formal_authorized=False, baseline='joint_triangular', cases=ordered)
    write_json(plan_path, plan)
    return plan


def verify_freeze(out):
    freeze = json.loads((out/'baseline_freeze.json').read_text(encoding='utf-8'))
    for name, digest in freeze['files'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != digest:
            raise RuntimeError(f'Frozen baseline changed: {name}')
    survey_freeze=out/'survey_freeze.json'
    if survey_freeze.exists():
        expected=json.loads(survey_freeze.read_text(encoding='utf-8'))
        if hashlib.sha256((ROOT/'src/bsolver/survey.py').read_bytes()).hexdigest()!=expected['sha256']:
            raise RuntimeError('Frozen survey protocol changed')
    return freeze


def ui(action='snapshot', name=None, output=None):
    global _BRIDGE
    if _BRIDGE is not None:
        _BRIDGE.stdin.write(json.dumps(dict(action=action,name=name or '',output=str(output) if output else ''), ensure_ascii=False)+'\n')
        _BRIDGE.stdin.flush()
        line = _BRIDGE.stdout.readline()
        if not line:
            raise RuntimeError('Visible UI bridge closed')
        payload = json.loads(line.lstrip('\ufeff'))
        if 'error' in payload:
            raise RuntimeError(payload['error'])
        return payload
    cmd = ['powershell', '-NoProfile', '-File', str(ROOT/'scripts/round_ui.ps1'), '-Action', action]
    if name:
        if '正式' in name or '中止' in name:
            raise ValueError('Formal/abort UI is outside this collector')
        cmd += ['-Name', name]
    if output:
        cmd += ['-OutputPath', str(output)]
    r = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace', timeout=30)
    if r.returncode:
        raise RuntimeError(r.stderr or r.stdout)
    return json.loads(r.stdout.lstrip('\ufeff'))


def names(snapshot):
    return [r['name'] for r in snapshot['items']]


def start_bridge():
    global _BRIDGE
    _BRIDGE = subprocess.Popen(['powershell','-NoProfile','-File',str(ROOT/'scripts/round_ui.ps1'),'-Server'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               encoding='utf-8', errors='replace', bufsize=1)


def close_bridge():
    global _BRIDGE
    if _BRIDGE:
        _BRIDGE.stdin.close()
        try:
            _BRIDGE.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _BRIDGE.terminate()
        _BRIDGE = None


def collection_lock(out):
    # OS-held lock is automatically released on process death; a stale PID file
    # alone never licenses a second collector to act on the same robot.
    import msvcrt
    fp=(out/'collector.lock').open('a+b')
    if fp.tell()==0:
        fp.write(b' ')
        fp.flush()
    fp.seek(0)
    try:
        msvcrt.locking(fp.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        fp.close()
        raise RuntimeError('Another collector holds this round lock')
    fp.seek(1)
    fp.truncate()
    fp.write(str(os.getpid()).encode())
    fp.flush()
    return fp


def status(out, state, **details):
    audits=[json.loads(p.read_text(encoding='utf-8')) for p in (out/'cases').glob('*/post_exit_audit.json')]
    grouped={}
    for r in audits:
        key=f'p{r["problem"]}_{r["protocol"]}'
        grouped[key]=grouped.get(key,0)+1
    write_json(out/'collector_status.json',dict(state=state,updated_utc=utc(),pid=os.getpid(),
                                               audited=len(audits),counts=grouped,**details))


def audit_finished(case, folder, snapshot=None):
    result = json.loads((folder/'result.json').read_text(encoding='utf-8'))
    session = json.loads((folder/'session.json').read_text(encoding='utf-8'))
    snapshot = snapshot or ui(output=folder/'post_exit_ui.json')
    ns = names(snapshot)
    if session['case_code'] not in ns or '测试已结束' not in ns:
        raise RuntimeError('Post-exit UI case does not match; do not attach another case truth')
    text = '\n'.join(ns)
    match = re.search(r'本次案例含干扰源\s*(\d+)\s*个，其中全向\s*(\d+)\s*个、定向\s*(\d+)\s*个',text)
    total, omni, directional = (map(int,match.groups()) if match else (None,None,None))
    if total is None:
        match = re.search(r'共\s*(\d+)\s*个，\s*全向\s*(\d+)\s*个，\s*定向\s*(\d+)\s*个',text)
        if match:
            total, omni, directional = map(int, match.groups())
    if total is not None and (total != omni+directional or not 10<=total<=16):
        raise RuntimeError('UI count parsing is inconsistent; preserve raw UI and inspect')
    filenames = [s for s in ns if re.fullmatch(r'practice-p[34]-\d+-[A-Z0-9-]+\.jlog',s)]
    filename = next((f for f in filenames if session['case_code'] in f),None)
    digest, nbytes = None, None
    if filename:
        source = ROOT.parent/'B题/Jammers-simulator-win64/Jammers-simulator/JammersSimulatorData/behavior-logs'/filename
        dest = folder/'original_logs'/filename
        dest.parent.mkdir(exist_ok=True)
        if source.is_file():
            data = source.read_bytes()
            if dest.exists() and dest.read_bytes() != data:
                raise RuntimeError('Original log changed; never overwrite a conflicting log')
            if not dest.exists():
                shutil.copyfile(source,dest)
            assert dest.read_bytes() == data
            digest, nbytes = hashlib.sha256(data).hexdigest(),len(data)
    audit = dict(environment='official_practice',case_id=case['case_id'], problem=case['problem'],
                 protocol=case['protocol'], split=case['split'], case_code=session['case_code'],
                 source_total_post_exit=total, directional_total_post_exit=directional,
                 omnidirectional_total_post_exit=omni, cleared=result['clear_successes'],
                 clear_fraction=result['clear_successes']/total if total else None,
                 status=result['status'], total_virtual_time_s=result['total_virtual_time_s'],
                 original_log_filename=filename, original_log_sha256=digest, original_log_bytes=nbytes,
                 metadata_source='visible post-exit UI accessibility tree: post_exit_ui.json',audited_utc=utc())
    write_json(folder/'post_exit_audit.json',audit)
    if result['status']=='complete' and total is not None and result['clear_successes']!=total:
        raise RuntimeError('False complete detected against post-exit official truth; audit retained')
    return audit


def return_to_practice(snapshot):
    ns = names(snapshot)
    if '确认' in ns:
        ui('invoke','确认')
        time.sleep(.15)
    ui('invoke','返回演练测试')
    deadline=time.monotonic()+15
    while True:
        ns=names(ui())
        if '开始问题3演练测试' in ns and '开始问题4演练测试' in ns:
            return
        if time.monotonic()>=deadline:
            raise RuntimeError('Practice home did not finish rendering')
        time.sleep(.2)


def wait_finished_ui(folder, code, timeout=20):
    deadline = time.monotonic()+timeout
    while True:
        snapshot=ui()
        ns=names(snapshot)
        if code in ns and '测试已结束' in ns and '返回演练测试' in ns:
            write_json(folder/'post_exit_ui.json',snapshot)
            return snapshot
        if time.monotonic() >= deadline:
            write_json(folder/'post_exit_ui_unconfirmed.json',snapshot)
            raise RuntimeError('HTTP returned but finished UI not yet confirmed; do not start another case')
        time.sleep(.3)


def collect(plan, out, robot_id, base_url, maximum, adopt=False, policy_runner=None):
    completed_this_call = 0
    for case in plan['cases']:
        folder = out/'cases'/case['case_id']
        if (folder/'post_exit_audit.json').exists():
            continue
        if completed_this_call >= maximum:
            break
        verify_freeze(out)
        if (out/'STOP_AFTER_CASE').exists():
            print('STOP_AFTER_CASE requested; no new case started',flush=True)
            break
        snapshot = ui()
        if (folder/'result.json').exists():
            code=json.loads((folder/'session.json').read_text(encoding='utf-8'))['case_code']
            snapshot=wait_finished_ui(folder,code)
            audit=audit_finished(case,folder,snapshot)
            return_to_practice(snapshot)
            print(json.dumps({'recovered_post_exit':case['case_id'],'audit':audit},ensure_ascii=False),flush=True)
            continue
        if (folder/'requests.jsonl').exists() or (folder/'session.json').exists() or (folder/'launch_intent.json').exists():
            raise RuntimeError(f'Unfinished attempted session {case["case_id"]}; no automatic replay/new case')
        expected = f"问题{case['problem']} 演练 测试"
        if adopt and completed_this_call==0 and expected in names(snapshot):
            pass
        else:
            start = f"开始问题{case['problem']}演练测试"
            if start not in names(snapshot):
                raise RuntimeError('Practice home is not ready; no unknown control invoked')
            if case['protocol']=='survey':
                from bsolver.survey import run_survey  # fail before consuming a case if protocol absent
            write_json(folder/'launch_intent.json',dict(utc=utc(),problem=case['problem'],control=start))
            ui('invoke',start)
        ready_until=time.monotonic()+90
        while True:
            snapshot=ui()
            ns=names(snapshot)
            if expected in ns and '等待机器狗进入' in ns:
                break
            if time.monotonic()>ready_until:
                write_json(folder/'start_failure.json',dict(error='UI did not become ready',snapshot=snapshot,utc=utc()))
                raise RuntimeError('Case did not become ready; preserve and inspect')
            time.sleep(.7)
        write_json(folder/'ready_ui.json',snapshot)
        code=next((s for s in ns if re.fullmatch(r'[A-Z0-9]{4}(?:-[A-Z0-9]{4}){3}',s) and not s.startswith('XXXX')),None)
        if code is None:
            raise RuntimeError('Ready case code unavailable; no enter sent')
        write_json(folder/'session.json',dict(case_code=code,started_observed_utc=utc(),problem=case['problem']))
        result=(policy_runner or run_policy)(case,folder,robot_id,base_url)
        snapshot=wait_finished_ui(folder,code)
        audit=audit_finished(case,folder,snapshot)
        return_to_practice(snapshot)
        completed_this_call+=1
        status(out,'running',last_case=case['case_id'])
        print(json.dumps({'collected':case['case_id'],'status':result['status'],'N':audit['source_total_post_exit'],
                          'Ndir':audit['directional_total_post_exit'],'T':result['total_virtual_time_s'],
                          'cleared':result['clear_successes']},ensure_ascii=False),flush=True)
        if case['pilot'] and result['status']!='complete':
            raise RuntimeError('Pilot incomplete; inspect before expansion')


def run_policy(case, folder, robot_id, base_url):
    from bsolver.protocol import RobotClient, HTTPTransport
    from bsolver.strategy import Solver
    from bsolver.experiments import variant_configs
    config = variant_configs(case['problem'])['joint_triangular']
    client = RobotClient(robot_id=robot_id, transport=HTTPTransport(base_url), log_path=folder/'requests.jsonl')
    if case['protocol'] == 'survey':
        from bsolver.survey import run_survey
        result = run_survey(client, config, case['survey_seed'], folder/'decisions.jsonl')
    else:
        result = Solver(client, config, decision_log=folder/'decisions.jsonl').run()
    client.close_log()
    result.update(environment='official_practice', case_id=case['case_id'], protocol=case['protocol'],
                  source_total=None, directional_total=None, case_code=None,
                  baseline_sha256=verify_freeze(folder.parents[1])['source_sha256'])
    write_json(folder/'result.json', result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=ROOT/'results/round1')
    p.add_argument('--prepare-only', action='store_true')
    p.add_argument('--robot-id')
    p.add_argument('--base-url', default='http://127.0.0.1:2026')
    p.add_argument('--max-new', type=int, default=4)
    p.add_argument('--adopt-ready-first', action='store_true')
    p.add_argument('--policy-only', action='store_true', help='one already verified ready practice case; operator audits afterward')
    args = p.parse_args()
    plan = prepare(args.output)
    verify_freeze(args.output)
    if args.prepare_only:
        print(json.dumps({'planned':len(plan['cases']), 'output':str(args.output)}, ensure_ascii=False))
        return
    if not args.robot_id:
        p.error('--robot-id is required; never hardcode a team number')
    if not args.policy_only:
        lock=collection_lock(args.output)
        try:
            status(args.output,'running')
            start_bridge()
            collect(plan,args.output,args.robot_id,args.base_url,args.max_new,args.adopt_ready_first)
            status(args.output,'stopped_normally')
        except Exception as exc:
            status(args.output,'needs_attention',error=f'{type(exc).__name__}: {exc}')
            raise
        finally:
            close_bridge()
            lock.close()
        return
    for case in plan['cases']:
        folder = args.output/'cases'/case['case_id']
        if (folder/'result.json').exists():
            continue
        if (folder/'requests.jsonl').exists():
            raise RuntimeError('An attempted case has no result; inspect before any new action')
        snapshot = ui(output=folder/'ready_ui.json')
        ns = names(snapshot)
        expected = f"问题{case['problem']} 演练 测试"
        if expected not in ns or '等待机器狗进入' not in ns:
            raise RuntimeError(f'Expected ready {expected}; no action sent')
        code = next((s for s in ns if re.fullmatch(r'[A-Z0-9]{4}(?:-[A-Z0-9]{4}){3}',s) and not s.startswith('XXXX')),None)
        write_json(folder/'session.json', dict(case_code=code, started_observed_utc=utc(), problem=case['problem']))
        print(json.dumps(run_policy(case, folder, args.robot_id, args.base_url), ensure_ascii=False), flush=True)
        return


if __name__ == '__main__':
    main()
