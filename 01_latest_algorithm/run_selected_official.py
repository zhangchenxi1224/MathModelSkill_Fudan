"""Run five official practice cases per problem with the selected feedback policy."""
from pathlib import Path
import argparse
import contextlib
import importlib.util
import json
import re
import shutil
import sys
import traceback

ROOT = Path(__file__).resolve().parent
MAIN = ROOT.parents[1] / 'B_solution'
PREVIOUS = ROOT.parent / 'synthesis_iteration_20260911'
from selected_policy import make_solver, selection
from bsolver_frozen.protocol import RobotClient, HTTPTransport
sys.path.insert(0, str(MAIN / 'scripts'))
import collect_official_round as collector


def report(out):
    module_spec = importlib.util.spec_from_file_location('official_results_table', PREVIOUS / 'official_table.py')
    table = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(table)
    table.SCHEMES = {
        3: '最新版：dp_geometric，全部反馈几何更新＋局部两步前瞻；保留覆盖清除',
        4: '最新版：three_feedback_fast，有方位／近距离／无信号三类反馈成本决策；保留覆盖清除',
    }
    return table.build(out, out, out / 'report')


def run_policy(case, folder, robot_id, base_url):
    client = RobotClient(robot_id=robot_id, transport=HTTPTransport(base_url), log_path=folder / 'requests.jsonl')
    try:
        solver = make_solver(client, case['problem'], decision_log=folder / 'decisions.jsonl')
        result = solver.run()
    except Exception:
        (folder / 'exception.txt').write_text(traceback.format_exc(), encoding='utf-8')
        raise
    finally:
        client.close_log()
    result.update(environment='official_practice', case_id=case['case_id'], protocol='selected_feedback',
                  selected_policy=case['selected_policy'], source_total=None, directional_total=None, case_code=None)
    collector.write_json(folder / 'result.json', result)
    return result


def run(out, robot_id=None):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    chosen = selection()
    cases = [dict(case_id=f'feedback-p{p}-{i:03d}', problem=p, protocol='selected_feedback',
                  selected_policy=chosen['policies'][str(p)], split='official_descriptive', pilot=False)
             for p in (3, 4) for i in range(1, 6)]
    if not (out / 'plan.json').exists():
        collector.write_json(out / 'plan.json', dict(practice_only=True, planned_cases=10, per_problem=5, cases=cases))
        collector.write_json(out / 'selection.json', chosen)
        for case in cases:
            collector.write_json(out / 'cases' / case['case_id'] / 'assignment.json', case)
        source = out / 'source'
        source.mkdir()
        for directory in ('feedback', 'methods', 'vendor', 'runtime'):
            shutil.copytree(ROOT / directory, source / directory, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for name in ('selected_policy.py', 'selection.json', 'run.py', 'run_selected_official.py'):
            shutil.copy2(ROOT / name, source / name)
        (source / 'inputs').mkdir()
        shutil.copy2(ROOT / 'inputs/previous_selection.json', source / 'inputs/previous_selection.json')
        for path in (MAIN / 'scripts/collect_official_round.py', MAIN / 'scripts/round_ui.ps1', PREVIOUS / 'official_table.py'):
            shutil.copy2(path, source / path.name)
    else:
        if json.loads((out / 'selection.json').read_text(encoding='utf-8')) != chosen:
            raise RuntimeError('A different selected policy exists in this output folder')
    # This run uses its own selected policy, not the collector's historical baseline.
    collector.verify_freeze = lambda folder: chosen
    locks = []
    try:
        for directory in sorted({out, MAIN / 'results/round1'}, key=str):
            locks.append(collector.collection_lock(directory))
        collector.start_bridge()
        snapshot = collector.ui(output=out / 'initial_ui.json')
        if robot_id is None:
            robot_id = next((m.group(1) for name in collector.names(snapshot)
                             if (m := re.fullmatch(r'队号\s+(\d+)', name))), None)
        if robot_id is None:
            raise RuntimeError('Logged-in team number not visible; supply --robot-id')
        if '返回演练测试' in collector.names(snapshot) and '测试已结束' in collector.names(snapshot):
            collector.return_to_practice(snapshot)
        collector.status(out, 'running', planned_cases=10)
        for case in cases:
            if (out / 'STOP_AFTER_CASE').exists():
                break
            collector.collect({'cases': [case]}, out, robot_id, 'http://127.0.0.1:2026', 1, policy_runner=run_policy)
            report(out)
        collector.status(out, 'finished', planned_cases=10)
        collector.ui(output=out / 'final_ui.json')
    except Exception as exc:
        collector.status(out, 'needs_attention', error=str(exc))
        (out / 'exception.txt').write_text(traceback.format_exc(), encoding='utf-8')
        raise
    finally:
        collector.close_bridge()
        for lock in reversed(locks):
            lock.close()
        report(out)


class Tee:
    def __init__(self, *streams):
        self.streams = streams
    def write(self, text):
        for stream in self.streams:
            stream.write(text)
            stream.flush()
    def flush(self):
        for stream in self.streams:
            stream.flush()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--robot-id')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / 'console.log').open('a', encoding='utf-8') as log:
        with contextlib.redirect_stdout(Tee(sys.stdout, log)), contextlib.redirect_stderr(Tee(sys.stderr, log)):
            run(args.out, args.robot_id)
