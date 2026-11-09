"""Read-only, selected evidence export; never writes runtime state."""
from pathlib import Path
from datetime import datetime, timezone
import collections
import hashlib
import json
import subprocess

SOURCE = Path('/home/min/workspace/zaofu-worktrees/design199-20261106')
TEMP = Path('/tmp/zf-200-verification-20261106')
REPORT = SOURCE/'reports/2026-11-06-200-recovery-validation'
manifest = json.loads((TEMP/'real-canary.json').read_text())
root, state = Path(manifest['root']), Path(manifest['state'])
events = [json.loads(line) for line in (state/'events.jsonl').read_text().splitlines()]


def save(name, value):
    (REPORT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def git(*args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def seconds(start, end):
    return round((datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds(), 3)


types = {
    'workflow.invoke.requested', 'prd.scan.completed', 'task_map.ready', 'candidate.ready',
    'test.passed', 'flow.discovery.requested', 'flow.discovery.completed',
    'flow.goal.closed', 'goal.closure.synthesized', 'judge.passed', 'judge.failed',
    'task.pipeline.verify.completed', 'task.done', 'candidate.updated',
    'simulation.done', 'loop.shutdown_requested', 'loop.stopped', 'session.stopped',
}
selected = [e for e in events if e['type'] in types or e['type'].startswith((
    'run.goal.', 'run.delivery.', 'workflow.run.', 'ship.', 'delivery.', 'candidate.ship',
))]
save('workflow-milestones.json', {
    'kind': 'read-only-selected-evidence-not-a-runtime-ledger',
    'exported_at': datetime.now(timezone.utc).isoformat(),
    'manifest': manifest, 'events': selected,
})

bodies = {}
def capture(ref, digest=''):
    if isinstance(ref, dict):
        return capture(ref.get('ref', ''), ref.get('sha256', ''))
    if not isinstance(ref, str) or not ref:
        return
    path = Path(ref)
    if not path.is_absolute():
        path = state/path
    # Only explicitly selected evidence inside this isolated run.
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(state):
        return
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if digest and actual != digest:
        raise ValueError(f'digest mismatch: {ref}')
    bodies[str(path.relative_to(state))] = {
        'sha256': actual, 'byte_count': len(data), 'text': data.decode('utf-8'),
    }

admitted = [e for e in events if e['type']=='workflow.call.result.admitted']
for e in admitted:
    p = e['payload']
    for name in ('envelope_ref', 'control_result_ref', 'product_acceptance_spec_ref', 'product_acceptance_report_ref'):
        capture(p.get(name), p.get(name.replace('_ref', '_digest'), ''))
for e in selected:
    p = e.get('payload', {})
    for name in ('freeze_receipt_ref', 'task_map_ref', 'source_index_ref', 'plan_artifact_package_ref',
                 'report_ref', 'closure_report_ref', 'goal_report_ref', 'claim_set_ref'):
        capture(p.get(name), p.get(name.replace('_ref', '_digest'), ''))
    for ref in p.get('task_integration_receipt_refs', []):
        capture(ref)
# Preserve cited semantic reports and runnable probes, not provider homes,
# transcripts, navigation indexes, or an online copy of canonical state.
for _ in range(3):
    before = len(bodies)
    for body in list(bodies.values()):
        try:
            value = json.loads(body['text'])
        except (ValueError, TypeError):
            continue
        if not isinstance(value, dict):
            continue
        for name in ('goal_claim_set_ref', 'closure_fact_ref', 'report_ref',
                     'source_index_ref', 'task_map_ref', 'plan_artifact_package_ref'):
            capture(value.get(name), value.get(name.replace('_ref', '_digest'), ''))
        for name in ('evidence_refs', 'test_refs', 'e2e_refs'):
            for ref in value.get(name, []):
                capture(ref)
    if len(bodies) == before:
        break
save('admitted-results.json', {'events': admitted, 'bodies': bodies})

operations = {}
for e in events:
    p = e.get('payload', {}); oid = p.get('operation_id')
    if not oid:
        continue
    if e['type']=='workflow.operation.started':
        operations.setdefault(oid, {}).setdefault('starts', []).append({
            'ts': e['ts'], 'attempt_id': p.get('active_attempt_id'), 'lease_id': p.get('lease_id'),
        })
        operations.setdefault(oid, {}).update(
            role=p.get('role_instance'), start=e['ts'], attempt_id=p.get('active_attempt_id'),
            lease_id=p.get('lease_id'), redrive_count=p.get('redrive_count'),
        )
    if e['type']=='workflow.call.result.admitted':
        operations.setdefault(oid, {}).update(end=e['ts'], verdict=p.get('semantic_verdict'))
    if e['type'] in ('workflow.operation.completed', 'workflow.operation.settled',
                     'workflow.operation.failed', 'workflow.operation.cancelled'):
        operations.setdefault(oid, {}).update(terminal_event=e['type'], terminal_at=e['ts'])
for op in operations.values():
    if op.get('start') and op.get('end'):
        op['elapsed_seconds'] = seconds(op['start'], op['end'])
save('execution-metrics.json', {
    'as_of': events[-1]['ts'], 'operation_attempts': operations,
    'event_counts': dict(collections.Counter(e['type'] for e in events)),
    'business_task_id': 'TASK-PRD-PRODUCT',
    'local_dev_commit': git('rev-parse', 'refs/heads/dev'),
    'candidate_commit': git('rev-parse', 'refs/heads/candidate/PRD-WORKFLOW-4EF709E12CE26EFD'),
    'pricing_note': 'ChatGPT subscription usage is unpriced; zero USD is not zero cost evidence.',
})
print(json.dumps({'selected_events': len(selected), 'admitted_results': len(admitted),
                  'body_count':len(bodies), 'body_bytes':sum(v['byte_count'] for v in bodies.values()),
                  'last_event':events[-1]['ts']}, ensure_ascii=False))
