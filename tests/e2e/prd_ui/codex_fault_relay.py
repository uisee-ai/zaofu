"""Real Codex stdio relay with one opt-in transport fault, never fake results.

The isolated runner supplies a /tmp/zf-* control file. Only fault metadata is
recorded; prompts, credentials and native reasoning are never copied here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time


def main() -> int:
    root = Path(os.environ['ZF_UI_RUN_ROOT']).resolve()
    assert root.parent == Path('/tmp') and root.name.startswith('zf-')
    control = root / 'fault.json'
    mode = ''
    if control.exists():
        mode = json.loads(control.read_text())['mode']
        control.rename(root / f'fault-{mode}-claimed.json')
    assert mode in {'', 'abort', 'completed'}
    process = subprocess.Popen(['codex', '-c', 'model="gpt-6.1-sol"', '-c', 'model_reasoning_effort="high"',
                                *sys.argv[1:]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    terminal = threading.Event()
    fault_recorded = threading.Event()
    def stop(*_):
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def relay_input():
        try:
            for line in sys.stdin:
                process.stdin.write(line)
                process.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass
    threading.Thread(target=relay_input, daemon=True).start()
    def record_fault(kind: str, params: dict):
        if fault_recorded.is_set(): return
        fault_recorded.set()
        (root / 'evidence' / f'fault-{mode}.json').write_text(json.dumps({
            'fault': kind, 'pid': process.pid, 'unix_time': time.time(),
            'thread_id': params.get('threadId'), 'turn_id': (params.get('turn') or {}).get('id'),
            'provider': 'real codex', 'model': 'gpt-6.1-sol', 'effort': 'high',
        }, indent=2))
    def abort_turn(params):
        # Interrupt only after Codex acknowledges a real native turn. If it
        # completed before the injection, fail the test instead of forging abort.
        time.sleep(2)
        if terminal.is_set(): return
        record_fault('SIGTERM after native turn started', params)
        stop()
    try:
        for line in process.stdout:
            try: row = json.loads(line)
            except json.JSONDecodeError: row = {}
            method, params = row.get('method'), row.get('params') or {}
            if method == 'turn/started' and mode == 'abort':
                threading.Thread(target=abort_turn, args=(params,), daemon=True).start()
            if method == 'turn/completed':
                terminal.set()
                if mode == 'completed':
                    record_fault('drop real completion notification and disconnect', params)
                    # The native transcript owns completion. The application
                    # sees a disconnected transport, not a fabricated failure.
                    stop()
                    break
            sys.stdout.write(line)
            sys.stdout.flush()
    finally:
        stop()
    return process.wait()


if __name__ == '__main__':
    raise SystemExit(main())
