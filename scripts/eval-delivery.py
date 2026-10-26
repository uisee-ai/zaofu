#!/usr/bin/env python3
"""Freeze and operate paired, real ZaoFu deliveries with explicit evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/e2e/fixtures/delivery-eval'
ARMS = {
    'baseline': ('5191c9e6', Path('/home/min/workspace/public/zaofu-worktrees/eval-baseline-20261021')),
    'candidate': ('4b0a96e0', Path('/home/min/workspace/public/zaofu-worktrees/real-ab-build-20261021')),
}
CASES = ['cargo-mini', 'warehouse', 'publishing']
PYTHON = Path(sys.executable)
ZF = PYTHON.with_name('zf')


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def configured_state(project: Path) -> Path:
    configs = list(yaml.safe_load_all((project / 'zf.yaml').read_text()))
    spec = next(d['spec'] for d in configs if d.get('kind') == 'ZfConfig')
    state = (project / spec['project']['state_dir']).resolve()
    if not state.is_relative_to(project.resolve()):
        raise ValueError('evaluation state must remain inside its owned project')
    return state


def command(args: list[str], *, cwd: Path, env: dict | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f'command failed ({result.returncode}): {args!r}\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}')
    return result.stdout


def summarize_events(path: Path) -> dict:
    events = []
    if path.exists():
        lines = path.read_text().splitlines()
        for index, line in enumerate(lines):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                if index != len(lines) - 1:
                    raise
    types: dict[str, int] = {}
    for event in events:
        kind = str(event.get('type', ''))
        types[kind] = types.get(kind, 0) + 1
    terminals = [e for e in events if e.get('type') in {'run.goal.completed', 'run.goal.blocked', 'run.failed', 'run.completed'}]
    return {'event_count': len(events), 'types': types, 'last_events': [
        {'id': e.get('id'), 'type': e.get('type'), 'timestamp': e.get('ts'),
         'task_id': e.get('task_id'), 'role': e.get('actor')} for e in events[-12:]
    ], 'terminal_events': terminals, 'goal_completed': bool(types.get('run.goal.completed'))}


def usage_totals(path: Path) -> dict:
    """Sum persisted delta rows once; do not re-add cumulative_usage snapshots."""
    totals = {'noncached_input_tokens': 0, 'cached_input_tokens': 0,
              'output_tokens': 0, 'rows': 0, 'unpriced_rows': 0}
    seen = set()
    billed = 0.0
    authoritative = True
    for line in path.read_text().splitlines() if path.exists() else []:
        row = json.loads(line)
        key = row.get('dedupe_key') or row.get('usage_receipt_id')
        if not key:
            raise ValueError('cost row has no stable receipt identity')
        if key in seen:
            continue
        seen.add(key)
        totals['rows'] += 1
        for source, target in [('input_tokens', 'noncached_input_tokens'),
                               ('cache_read_tokens', 'cached_input_tokens'),
                               ('output_tokens', 'output_tokens')]:
            value = row.get(source, 0)
            if not isinstance(value, int) or value < 0:
                raise ValueError('invalid token delta')
            totals[target] += value
        if row.get('cost_status') == 'unpriced':
            totals['unpriced_rows'] += 1
        cost = row.get('billed_cost_usd')
        if cost is None:
            cost = row.get('provider_reported_cost_usd')
        if cost is None:
            authoritative = False
        else:
            billed += float(cost)
    totals['authoritative_cost_usd'] = billed if authoritative and totals['rows'] else None
    totals['cost_gate_status'] = 'measurable' if totals['authoritative_cost_usd'] is not None else 'UNKNOWN'
    return totals


def prepare(output: Path) -> None:
    if output.exists():
        raise ValueError('output must be a new directory')
    if not str(output.resolve()).startswith('/tmp/zf-'):
        raise ValueError('temporary products must live under /tmp/zf-*')
    for arm, (ref, repo) in ARMS.items():
        actual = command(['git', 'rev-parse', 'HEAD'], cwd=repo).strip()
        expected = command(['git', 'rev-parse', ref], cwd=repo).strip()
        if actual != expected or command(['git', 'status', '--porcelain'], cwd=repo).strip():
            raise ValueError(f'{arm} checkout is not the frozen clean revision')
    output.mkdir(parents=True)
    snapshot = output / 'oracle'
    snapshot.mkdir()
    for name in ['delivery_acceptance.py', 'delivery_browser.cjs']:
        shutil.copyfile(ROOT / 'tests/e2e' / name, snapshot / name)
    common = (FIXTURES / 'common.md').read_text()
    ordering = random.Random(20261021).sample(['baseline', 'candidate'], 2)
    registration = {
        'schema_version': 'delivery-evaluation.v1', 'registered_at': time.time(),
        'phase': 'pilot', 'unit': 'one complete PRD delivery, not each AC or provider call',
        'commits': {}, 'model': 'gpt-6-astra', 'reasoning_effort': 'medium',
        'limits': {'active_seconds_per_arm': 2400, 'run_tokens': 10000000,
                   'operation_seconds': 600, 'operation_tokens': 1500000,
                   'manual_product_repairs': 0, 'semantic_rework_attempts': 2},
        'orders': {case: ordering if i % 2 == 0 else ordering[::-1] for i, case in enumerate(CASES)},
        'gate': {'mandatory_pass': 1.0, 'new_severe_defects': 0,
                 'delivery_success_not_lower': True, 'total_cost_ratio_max': 0.8,
                 'elapsed_ratio_max': 1.1, 'manual_intervention_not_higher': True},
        'cost_policy': 'Use genuine metered price/cost with provenance; unknown model pricing is UNKNOWN, never free. Report cached/noncached input, output, all retries separately. Token reduction alone cannot pass money-cost gate.',
        'advancement': 'Run both cargo-mini arms even if first fails. Advance to warehouse/publishing only if both first products reach goal completion and external mandatory acceptance. Confirmation sample size is preregistered after pilot variance, not inferred from 3 pairs.',
        'approval': 'Same plan_approval=false for isolated synthetic products, independent Critic/Verify retained; real cargo keeps manual Plan approval.',
        'failure_policy': 'No replacement runs, no dropping failures, no candidate edits mid-batch. Environment errors and product rejection classified separately. All wall time and interventions retained.',
        'exposure': 'Evaluation prompts/oracles frozen before any call. Oracle is outside product; provider must not inspect it. Host transport is not a security isolation boundary; trace review required.',
        'limits_of_inference': 'Exploratory small-product pilot; no statistical significance or full-cargo claim. Publishing is a transfer probe not an independently blinded holdout.',
        'oracle_digests': {p.name: digest(p) for p in snapshot.iterdir()},
        'cases': {}, 'runs': {},
    }
    image = command(['docker', 'image', 'inspect', 'mcp/playwright:latest', '--format', '{{.Id}}'], cwd=ROOT).strip()
    registration['browser_image'] = image
    registration['codex_version'] = command(['codex', '--version'], cwd=ROOT).strip()
    for arm, (ref, repo) in ARMS.items():
        registration['commits'][arm] = command(['git', 'rev-parse', ref], cwd=repo).strip()
    for case in CASES:
        seed = output / 'seeds' / case
        (seed / 'docs').mkdir(parents=True)
        (seed / 'scripts').mkdir()
        (seed / 'app').mkdir()
        (seed / 'app/.gitkeep').touch()
        (seed / 'docs/prd.md').write_text(common + '\n' + (FIXTURES / f'{case}.md').read_text())
        (seed / 'README.md').write_text(f'# {case}\n\n独立的完整交付评估产品，产品需求见 docs/prd.md。\n')
        (seed / '.gitignore').write_text('.zf-eval/\n.zf/\nzf.yaml\nnode_modules/\napp/node_modules/\napp/dist/\napp/evidence/\n')
        (seed / 'AGENTS.md').write_text('# 产品执行边界\n\n按 docs/prd.md 完整交付。运行状态只用 zf CLI 和受控接口维护；不可直接写 events/kanban。不得修改PRD、zf.yaml、skills、AGENTS.md或仓库外评估器。不要搜索外部oracle。普通任务按briefing required_reads读取。独立Critic/Verify有效，不自批。只能修改本仓库允许范围，不使用8001端口，不干预其他会话。\n')
        (seed / 'scripts/setup-worktree.sh').write_text('#!/usr/bin/env bash\nset -euo pipefail\nif [[ ! -f app/package.json ]]; then\n echo "Planning only: product not built"\n exit 0\nfi\nnpm ci --prefix app\n')
        command(['git', 'init', '-q'], cwd=seed)
        command(['git', 'config', 'user.name', 'ZaoFu Delivery Evaluation'], cwd=seed)
        command(['git', 'config', 'user.email', 'delivery-eval@localhost'], cwd=seed)
        paths = ['README.md', '.gitignore', 'AGENTS.md', 'docs/prd.md', 'scripts/setup-worktree.sh', 'app/.gitkeep']
        command(['git', 'add', '--', *paths], cwd=seed)
        command(['git', 'commit', '-q', '-m', 'test: seed independent delivery evaluation'], cwd=seed)
        registration['cases'][case] = {'seed_commit': command(['git', 'rev-parse', 'HEAD'], cwd=seed).strip(), 'prd_digest': digest(seed / 'docs/prd.md')}
        for arm, (_, repo) in ARMS.items():
            run_id = f'{case}-{arm}'
            project = output / 'runs' / run_id / 'project'
            project.parent.mkdir(parents=True)
            command(['git', 'clone', '-q', str(seed), str(project)], cwd=output)
            command(['git', 'config', 'user.name', 'ZaoFu Delivery Evaluation'], cwd=project)
            command(['git', 'config', 'user.email', 'delivery-eval@localhost'], cwd=project)
            session = f'zf-delivery-{output.name[-8:]}-{case}-{arm}'
            config = yaml.safe_load_all((repo / 'examples/prod/controller/prd-fanout-v3.yaml').read_text())
            docs = list(config)
            flow, runtime = docs[0]['spec'], docs[1]['spec']
            flow.update(lanes=1, prdRef='docs/prd.md', targetRoot='app', targetRef='HEAD')
            flow['roleDefaults'] = {'model': 'gpt-6-astra', 'model_reasoning_effort': 'medium', 'transport': 'tmux'}
            runtime['profile_sources'] = [str(repo / 'examples/prod/controller/common/profiles.yaml')]
            runtime['project'] = {'name': run_id, 'state_dir': '.zf-eval', 'scripts': {'setup': 'bash scripts/setup-worktree.sh'}}
            runtime['session'] = {'tmux_session': session}
            runtime['skill_sources'] = [{'name': 'zaofu-skills', 'path': str(repo / 'skills'), 'mode': 'readonly'}]
            runtime['runtime'] = {'run_manager': {'resident_agent': {'enabled': False}}, 'autoresearch_resident': {'enabled': False}, 'feishu_inbound': {'enabled': False}}
            for role in runtime.get('roles', []):
                role.update(model='gpt-6-astra', model_reasoning_effort='medium', transport='tmux')
            workflow = runtime.setdefault('workflow', {})
            workflow.update(plan_approval=False, impl_self_check_required=True)
            workflow['work_units'] = {'enabled': True, 'split_quality': {'mode': 'blocking', 'max_acceptance_criteria': 0}}
            workflow['run_limits'] = {'timeout_seconds': 2400, 'token_budget': 10000000, 'cost_budget_usd': 40}
            workflow['execution_profiles'] = {'bounded-direct-v1': {'strategy': 'direct', 'limits': {'timeout_seconds': 600, 'token_budget': 1500000, 'max_usage_samples': 60, 'cost_budget_usd': 8}}}
            (project / 'zf.yaml').write_text(yaml.safe_dump_all(docs, sort_keys=False, allow_unicode=True))
            registration['runs'][run_id] = {'case': case, 'arm': arm, 'project': str(project), 'harness': str(repo), 'session': session, 'config_digest': digest(project / 'zf.yaml')}
    write_json(output / 'registration.json', registration)
    (output / 'registration.sha256').write_text(digest(output / 'registration.json') + '\n')
    print(json.dumps({'root': str(output), 'orders': registration['orders'], 'registration_sha256': digest(output / 'registration.json')}, indent=2))


def registration(root: Path) -> dict:
    if digest(root / 'registration.json') != (root / 'registration.sha256').read_text().strip():
        raise ValueError('registration changed after freeze')
    return json.loads((root / 'registration.json').read_text())


def env_for(run: dict) -> dict:
    env = os.environ.copy()
    env['PYTHONPATH'] = str(Path(run['harness']) / 'src')
    env['PATH'] = str(PYTHON.parent) + os.pathsep + env.get('PATH', '')
    env['ZF_TMUX_SESSION'] = run['session']
    return env


def run_arm(root: Path, run_id: str) -> None:
    record = registration(root)
    run = record['runs'][run_id]
    project = Path(run['project'])
    env = env_for(run)
    run_dir = project.parent
    if (run_dir / 'started.json').exists():
        raise ValueError('arm already started; inspect/resume explicitly, never silently retry')
    if digest(project / 'zf.yaml') != run['config_digest']:
        raise ValueError('configuration changed after freeze')
    write_json(run_dir / 'started.json', {'started_at': time.time(), 'run_id': run_id})
    steps = [
        ['init', '--no-workspace-register', '--no-git-hooks', '--skip-instruction-docs'],
        ['flow', 'intake', '--kind', 'prd', '--from', 'docs/prd.md', '--objective', '按完整docs/prd.md实现独立小产品，真实构建、测试与浏览器验收；必须完成产品而非仅规划。', '--target', 'app', '--backend', 'codex', '--lanes', '1', '--acceptance', 'docs/prd.md全部mandatory行为真实通过，干净构建并运行app/dist/server.mjs，保留失败与重试证据。', '--request-id', run_id, '--output', 'docs/intake.md', '--json'],
        ['flow', 'clarify', '--config', 'zf.yaml', '--intake', 'docs/intake.md', '--confirm', '--json'],
        ['flow', 'submit', '--config', 'zf.yaml', '--intake', 'docs/intake.md', '--kind', 'prd', '--dry-run', '--json'],
    ]
    try:
        for i, args in enumerate(steps):
            output = command([str(ZF), *args], cwd=project, env=env)
            (run_dir / f'prepare-{i}.log').write_text(output)
        # Keep exactly the same accepted intake documents in the repo before writers fork.
        generated = command(['git', 'ls-files', '--others', '--exclude-standard'], cwd=project).splitlines()
        allowed = [p for p in generated if p.startswith(('docs/', 'artifacts/'))]
        if allowed:
            command(['git', 'add', '--', *allowed], cwd=project)
            staged = command(['git', 'diff', '--cached', '--name-only'], cwd=project).splitlines()
            if sorted(staged) != sorted(allowed):
                raise ValueError('unexpected staged paths')
            command(['git', 'commit', '-q', '-m', 'test: bind confirmed delivery inputs'], cwd=project)
        with (run_dir / 'runtime.log').open('w') as log:
            process = subprocess.Popen([str(ZF), 'start'], cwd=project, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        write_json(run_dir / 'runtime.json', {'pid': process.pid, 'started_at': time.time(), 'session': run['session']})
        time.sleep(1)
        if process.poll() is not None:
            raise RuntimeError('runtime exited during startup; see runtime.log')
        output = command([str(ZF), 'flow', 'submit', '--config', 'zf.yaml', '--intake', 'docs/intake.md', '--kind', 'prd', '--apply', '--json'], cwd=project, env=env)
        (run_dir / 'submit.json').write_text(output)
        print(json.dumps({'run_id': run_id, 'pid': process.pid, 'submitted': True}))
    except Exception as exc:
        write_json(run_dir / 'preflight-failure.json', {'timestamp': time.time(), 'error': str(exc), 'classification': 'runner_environment_or_configuration'})
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run-arm', 'resume-runtime', 'status', 'report'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--run-id')
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.root)
    elif args.action == 'run-arm':
        run_arm(args.root, args.run_id)
    elif args.action == 'resume-runtime':
        r = registration(args.root)
        run = r['runs'][args.run_id]
        project = Path(run['project'])
        run_dir = project.parent
        if not (run_dir / 'submit.json').exists():
            raise ValueError('resume requires existing accepted submit; do not recreate requests')
        prior = json.loads((run_dir / 'runtime.json').read_text())
        try:
            os.kill(prior['pid'], 0)
        except ProcessLookupError:
            pass
        else:
            raise ValueError('recorded runtime PID still exists; inspect before resume')
        (run_dir / 'runtime-startup-failure.log').write_bytes((run_dir / 'runtime.log').read_bytes())
        write_json(run_dir / 'operator-intervention.json', {
            'timestamp': time.time(), 'kind': 'evaluation_runner_startup_repair',
            'reason': 'start --simulation only accepts direct /tmp children; use ordinary start and explicit owned cleanup for nested projects',
            'previous_runtime': prior, 'product_files_changed': [], 'request_reapplied': False,
            'original_start_clock_preserved': True,
        })
        with (run_dir / 'runtime.log').open('a') as log:
            p = subprocess.Popen([str(ZF), 'start'], cwd=project, env=env_for(run), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        write_json(run_dir / 'runtime.json', {'pid': p.pid, 'started_at': time.time(), 'session': run['session']})
        print(json.dumps({'run_id': args.run_id, 'resumed_pid': p.pid}))
    else:
        r = registration(args.root)
        for name, run in r['runs'].items():
            if args.run_id and name != args.run_id:
                continue
            project = Path(run['project'])
            state = configured_state(project)
            report = summarize_events(state / 'events.jsonl')
            report = {'run_id': name, **report, 'usage': usage_totals(state / 'cost.jsonl')}
            if args.action == 'report':
                manifest_paths = []
                for manifest in (state / 'workdirs').glob('*/runtime/skills-manifest.json'):
                    manifest_paths.append({'path': str(manifest), 'sha256': digest(manifest)})
                report['skill_manifests'] = manifest_paths
                report['prd_unchanged'] = digest(project / 'docs/prd.md') == r['cases'][run['case']]['prd_digest']
                report['config_unchanged'] = digest(project / 'zf.yaml') == run['config_digest']
                report['external_acceptance'] = 'NOT_RUN'
                for filename in ['outcome.json', 'operator-intervention.json']:
                    path = project.parent / filename
                    if path.exists():
                        report[filename] = json.loads(path.read_text())
                write_json(project.parent / 'measurement.json', report)
                print(json.dumps({'run_id': name, 'goal_completed': report['goal_completed'], 'usage': report['usage'], 'report': str(project.parent / 'measurement.json')}, ensure_ascii=False))
            else:
                print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
