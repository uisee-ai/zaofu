#!/usr/bin/env python3
"""Bound one evaluation arm and stop only its owned runtime via the CLI."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    raw = (args.root / 'registration.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (args.root / 'registration.sha256').read_text().strip()
    registration = json.loads(raw)
    run = registration['runs'][args.run_id]
    project = Path(run['project'])
    run_dir = project.parent
    config_docs = list(yaml.safe_load_all((project / 'zf.yaml').read_text()))
    spec = next(d['spec'] for d in config_docs if d.get('kind') == 'ZfConfig')
    state = (project / spec['project']['state_dir']).resolve()
    assert state.is_relative_to(project.resolve())
    # This tool is tied to the generated config; don't silently watch another state dir.
    assert hashlib.sha256((project / 'zf.yaml').read_bytes()).hexdigest() == run['config_digest']
    started = json.loads((run_dir / 'started.json').read_text())['started_at']
    deadline = started + registration['limits']['active_seconds_per_arm']
    last_report = 0
    events = []
    reason = 'wall_budget_exhausted'
    while time.time() < deadline:
        lines = (state / 'events.jsonl').read_text().splitlines()
        events = []
        for index, line in enumerate(lines):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                if index != len(lines) - 1:
                    raise
        terminals = [e for e in events if e['type'] in {'run.goal.completed', 'run.goal.blocked'}]
        if terminals:
            reason = terminals[-1]['type']
            break
        if time.time() - last_report >= 30:
            counts = Counter(e['type'] for e in events)
            report = {'run_id': args.run_id, 'elapsed': round(time.time() - started),
                      'events': len(events), 'counts': {k: v for k, v in counts.items()
                      if any(x in k for x in ['fanout.child', 'task_map', 'task.done', 'failed', 'blocked', 'result.invalid', 'candidate.ready', 'plan.approval'])}}
            print(json.dumps(report, ensure_ascii=False), flush=True)
            last_report = time.time()
        time.sleep(5)
    # No runtime state files are edited. Publish simulation finish and stop through zf.
    env = {**os.environ, 'PYTHONPATH': str(Path(run['harness']) / 'src'),
           'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', '')}
    zf = str(Path(sys.executable).with_name('zf'))
    outcome = {'run_id': args.run_id, 'status': reason, 'elapsed_seconds': time.time() - started,
               'goal_completed': reason == 'run.goal.completed', 'product_verified': False,
               'note': 'A goal event is not external product acceptance; grader is separate.'}
    (run_dir / 'simulation-payload.json').write_text(json.dumps({
        'purpose': 'paired-delivery-evaluation', 'workflow_kind': 'prd',
        'status': reason, 'reason': 'frozen arm terminal or fixed wall budget',
        'failure_classification': 'requires_report_review',
    }))
    with (run_dir / 'cleanup.log').open('a') as log:
        emit = subprocess.run([zf, 'emit', 'simulation.done', '--actor', 'delivery-evaluation',
                               '--payload-file', str(run_dir / 'simulation-payload.json')],
                              cwd=project, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=20)
        stop = subprocess.run([zf, 'stop', '--fast'], cwd=project, env=env,
                              stdout=log, stderr=subprocess.STDOUT, timeout=45)
    outcome['cleanup'] = {'simulation_done_exit_code': emit.returncode, 'stop_exit_code': stop.returncode}
    runtime = json.loads((run_dir / 'runtime.json').read_text())
    pid = runtime['pid']
    try:
        owner = Path(f'/proc/{pid}/cwd').resolve(strict=True)
        if owner != project:
            raise RuntimeError('runtime PID ownership mismatch; refusing to signal')
        os.kill(pid, signal.SIGTERM)
    except FileNotFoundError:
        pass
    (run_dir / 'outcome.json').write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(outcome, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
