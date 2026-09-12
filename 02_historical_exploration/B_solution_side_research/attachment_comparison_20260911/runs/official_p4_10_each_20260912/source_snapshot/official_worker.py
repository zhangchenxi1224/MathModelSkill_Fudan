"""One serial official practice run from a campaign's saved policy sources."""
from pathlib import Path
import argparse
import dataclasses
import json
import sys
import time
import traceback
import types


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def run(campaign, folder, arm, robot_id, base_url):
    source = campaign / 'source_snapshot' / arm
    sys.path.insert(0, str(source / 'vendor'))
    sys.path.insert(0, str(source))
    from bsolver.protocol import RobotClient, HTTPTransport
    client = RobotClient(robot_id=robot_id, transport=HTTPTransport(base_url),
                         log_path=folder / 'requests.jsonl')
    if arm == 'attachment':
        from policies import make_policy
        variant = 'p4_route_rescue'
        solver = make_policy(client, 4, variant)
        solver.decision_log = folder / 'decisions.jsonl'
    else:
        from methods.solver import make_solver
        selection = json.loads((source / 'campaign/deployment_selection.json').read_text(encoding='utf-8'))
        variant = selection['choices']['4']
        solver = make_solver(client, 4, selection['validation_specs'][variant],
                             decision_log=folder / 'decisions.jsonl')
    # Observational logging only: preserve original policy decisions and add a
    # change ledger containing every public channel-state/route transition.
    original_record = solver._record
    started = time.monotonic()
    previous = {}
    ledger = (folder / 'observable_states.jsonl').open('w', encoding='utf-8')

    def record(self, event, **data):
        original_record(event, **data)
        changed = {}
        for channel, knowledge in self.channels.items():
            state = dataclasses.asdict(knowledge)
            state['coverage_indices'] = sorted(state['coverage_indices'])
            serialized = json.dumps(state, ensure_ascii=False, sort_keys=True)
            if previous.get(channel) != serialized:
                changed[str(channel)] = state
                previous[channel] = serialized
        route_key = tuple(self.points)
        route = list(self.points) if previous.get('route') != route_key else None
        previous['route'] = route_key
        item = dict(sequence=self.event_counter - 1, event=event,
                    elapsed_monotonic_s=time.monotonic() - started,
                    client_state=client.state_snapshot(), stats=dict(self.stats),
                    changed_channels=changed, changed_route=route,
                    stop_evidence=self.stop_evidence)
        ledger.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + '\n')
        ledger.flush()

    solver._record = types.MethodType(record, solver)
    try:
        result = solver.run()
        result.update(environment='official_practice', case_id=folder.name,
                      protocol=arm, arm=arm, variant=variant,
                      source_total=None, directional_total=None, case_code=None)
        write(folder / 'result.json', result)
        write(folder / 'final_observable_state.json', {
            'client': client.state_snapshot(), 'points': solver.points,
            'channels': {str(k): {**dataclasses.asdict(v),
                         'coverage_indices': sorted(v.coverage_indices)}
                         for k, v in solver.channels.items()},
            'stop_evidence': solver.stop_evidence})
    except Exception:
        (folder / 'worker_exception.txt').write_text(traceback.format_exc(), encoding='utf-8')
        raise
    finally:
        ledger.close()
        client.close_log()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign', type=Path, required=True)
    p.add_argument('--folder', type=Path, required=True)
    p.add_argument('--arm', choices=['attachment', 'baseline'], required=True)
    p.add_argument('--robot-id', required=True)
    p.add_argument('--base-url', default='http://127.0.0.1:2026')
    a = p.parse_args()
    run(a.campaign, a.folder, a.arm, a.robot_id, a.base_url)
