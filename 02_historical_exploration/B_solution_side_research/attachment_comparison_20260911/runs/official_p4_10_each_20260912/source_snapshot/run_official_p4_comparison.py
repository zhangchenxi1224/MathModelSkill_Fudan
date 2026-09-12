"""Ten official practice cases per policy, randomized within ten alternating blocks."""
from pathlib import Path
import argparse
import hashlib
import json
import random
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
MAIN = ROOT.parents[1] / 'B_solution'
SYNTHESIS = ROOT.parent / 'synthesis_iteration_20260911'
sys.path.insert(0, str(MAIN / 'scripts'))
import collect_official_round as collector


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def prepare(out):
    if (out / 'plan.json').exists():
        return read(out / 'plan.json')
    out.mkdir(parents=True, exist_ok=False)
    snap = out / 'source_snapshot'
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc')
    for name, origin in [('attachment', ROOT), ('baseline', SYNTHESIS)]:
        target = snap / name
        target.mkdir(parents=True)
        shutil.copytree(origin / 'vendor', target / 'vendor', ignore=ignore)
        if name == 'attachment':
            for filename in ('policies.py', 'main_policy_bundle.json'):
                shutil.copy2(origin / filename, target / filename)
            shutil.copytree(origin / 'attachment', target / 'attachment', ignore=ignore)
        else:
            shutil.copytree(origin / 'methods', target / 'methods', ignore=ignore)
            (target / 'campaign').mkdir()
            shutil.copy2(origin / 'campaign/deployment_selection.json',
                         target / 'campaign/deployment_selection.json')
    for filename in ('official_worker.py', 'run_official_p4_comparison.py', 'official_report.py'):
        shutil.copy2(ROOT / filename, snap / filename)
    for filename in ('collect_official_round.py', 'round_ui.ps1'):
        shutil.copy2(MAIN / 'scripts' / filename, snap / filename)
    hashes = {str(p.relative_to(snap)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in snap.rglob('*') if p.is_file()}
    collector.write_json(out / 'source_manifest.json', hashes)
    rng = random.Random(2026091201)
    cases = []
    for block in range(1, 11):
        arms = ['attachment', 'baseline']
        rng.shuffle(arms)
        for arm in arms:
            case = dict(case_id=f'p4-{arm}-{block:03d}', problem=4, protocol=arm,
                        arm=arm, split='new_official_descriptive', pilot=False,
                        block=block, sequence=len(cases), environment='official_practice',
                        variant='p4_route_rescue' if arm == 'attachment' else 'p4_L2_d1200_w1')
            cases.append(case)
            collector.write_json(out / 'cases' / case['case_id'] / 'assignment.json', case)
    plan = dict(created_utc=collector.utc(), practice_only=True, formal_authorized=False,
                planned_cases=20, cases_per_arm=10, random_seed=2026091201,
                allocation='Independent new cases; random arm order within each of ten two-case blocks',
                baseline_origin=str(SYNTHESIS), attachment_origin=str(ROOT), cases=cases)
    collector.write_json(out / 'plan.json', plan)
    return plan


def run_policy(case, folder, robot_id, base_url):
    campaign = folder.parents[1]
    cmd = [sys.executable, '-B', '-X', 'utf8',
           str(campaign / 'source_snapshot/official_worker.py'),
           '--campaign', str(campaign), '--folder', str(folder),
           '--arm', case['arm'], '--robot-id', robot_id, '--base-url', base_url]
    with (folder / 'worker.stdout.log').open('w', encoding='utf-8') as stdout, \
         (folder / 'worker.stderr.log').open('w', encoding='utf-8') as stderr:
        completed = subprocess.run(cmd, stdout=stdout, stderr=stderr)
    if completed.returncode:
        raise RuntimeError(f"Policy process failed for {case['case_id']}; logs retained")
    return read(folder / 'result.json')


def run(out, robot_id, prepare_only=False):
    out = out.resolve()
    plan = prepare(out)
    if prepare_only:
        print(json.dumps({'prepared': str(out), 'cases': len(plan['cases'])}))
        return
    locks = []
    try:
        for directory in sorted({out, MAIN / 'results/round1'}, key=str):
            locks.append(collector.collection_lock(directory))
        # The injected runner uses this campaign's source snapshots, so the
        # collector's unrelated historical round-one baseline is not involved.
        collector.verify_freeze = lambda unused: {}
        collector.start_bridge()
        collector.status(out, 'running', planned_cases=20)
        for case in plan['cases']:
            folder = out / 'cases' / case['case_id']
            if (folder / 'post_exit_audit.json').exists():
                continue
            if (out / 'STOP_AFTER_CASE').exists():
                break
            collector.collect({'cases': [case]}, out, robot_id,
                              'http://127.0.0.1:2026', 1, policy_runner=run_policy)
            audit = read(folder / 'post_exit_audit.json')
            if audit['status'] != 'complete' or audit['cleared'] != audit['source_total_post_exit']:
                raise RuntimeError('Unfinished case retained; investigate before further practice')
        collector.status(out, 'stopped_normally', planned_cases=20)
    except Exception as exc:
        collector.status(out, 'needs_attention', error=str(exc))
        raise
    finally:
        collector.close_bridge()
        for lock in reversed(locks):
            lock.close()
    from official_report import report
    report(out)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--robot-id', required=True)
    p.add_argument('--prepare-only', action='store_true')
    a = p.parse_args()
    run(a.out, a.robot_id, a.prepare_only)
