#!/usr/bin/env python3
"""Run frozen tiny-interface oracles against handoff code in a networkless container.

This is an evaluation receipt, never a product/task completion verdict.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--image', default='mcp/playwright:latest')
    args = parser.parse_args()
    root = args.run.resolve(strict=True)
    manifest = json.loads((root / 'manifest.json').read_text())
    oracle_path = ROOT / 'tests/fixtures/spec_handoff_eval/oracle.json'
    if manifest.get('schema_version') != 'spec-handoff-eval.v1' or manifest['oracle_sha256'] != sha(oracle_path):
        raise ValueError('wrong experiment or changed oracle')
    target = root / 'implementation-verification'
    target.mkdir(exist_ok=False)
    image = subprocess.check_output(['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}'], text=True).strip()
    oracles = {v['id']: v for v in json.loads(oracle_path.read_text()) if v['test_code']}
    receipts = []
    for row in manifest['runs']:
        if row['case'] not in oracles:
            continue
        if row['arm'] not in {'baseline', 'candidate'}:
            raise ValueError('unknown arm')
        run = root / f"{row['case']}-{row['arm']}"
        if run.is_symlink() or (run / 'handoff').is_symlink():
            raise ValueError('symlink evidence is not allowed')
        trace = run / 'handoff/trace.jsonl'
        if trace.is_symlink() or row['status'] != 'completed_ungraded' or sha(trace) != row['handoff']['trace_sha256']:
            raise ValueError('uncompleted or changed handoff trace')
        # Read the actual final answer from the bound trace, not an editable parsed projection.
        messages = [e['item']['text'] for line in trace.read_text().splitlines()
                    if (e := json.loads(line)).get('type') == 'item.completed'
                    and e.get('item', {}).get('type') == 'agent_message']
        answer = messages[-1].strip()
        if answer.startswith('```json\n') and answer.endswith('```'):
            answer = answer[len('```json\n'):-3]
        code = json.loads(answer).get('implementation')
        if not isinstance(code, str) or not code.strip():
            receipts.append({'case': row['case'], 'arm': row['arm'], 'status': 'missing_implementation'})
            continue
        folder = target / run.name
        folder.mkdir()
        (folder / 'implementation.mjs').write_text(code)
        (folder / 'oracle.mjs').write_text(oracles[row['case']]['test_code'])
        name = 'zf-spec-oracle-' + hashlib.sha256(str(folder).encode()).hexdigest()[:12]
        command = ['docker', 'run', '--rm', '--name', name, '--network', 'none', '--read-only',
                   '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit', '64',
                   '--memory', '256m', '--cpus', '1', '--user', f'{os.getuid()}:{os.getgid()}',
                   '--tmpfs', '/tmp:rw,nosuid,size=16m', '-v', f'{folder}:/work:ro',
                   '-w', '/work', '--entrypoint', 'node', image, 'oracle.mjs']
        timed_out = False
        with (folder / 'test.log').open('w') as out:
            try:
                result = subprocess.run(command, stdout=out, stderr=subprocess.STDOUT, timeout=30)
                code = result.returncode
            except subprocess.TimeoutExpired:
                timed_out, code = True, None
            finally:
                subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        receipts.append({'case': row['case'], 'arm': row['arm'], 'status': 'passed' if code == 0 else 'failed',
                         'returncode': code, 'timed_out': timed_out,
                         'implementation_sha256': sha(folder / 'implementation.mjs'),
                         'oracle_sha256': sha(folder / 'oracle.mjs')})
    (target / 'results.json').write_text(json.dumps({'image': image, 'receipts': receipts}, indent=2) + '\n')
    print(json.dumps(receipts))
    return int(not receipts or any(r['status'] != 'passed' for r in receipts))


if __name__ == '__main__':
    raise SystemExit(main())
