"""Setup and read-only oracle for real Codex Channel recovery UI verification."""
from __future__ import annotations

import json
import os
from pathlib import Path
from zf.core.config.loader import load_config
from zf.core.events import EventLog, EventWriter
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def configure(spec: dict, env: dict, repo: Path) -> None:
    spec['channel']['provider_timeout_seconds'] = 300
    spec['channel']['agent_profiles'] = {'recovery-reader': {
        'revision': 1, 'persona': 'Answer the owner concisely. Read-only requirement discussion.',
        'display_name': 'Recovery Reader', 'channel_role': 'product_pm',
        'provider': 'codex', 'backend': 'codex', 'model': 'gpt-6.1-sol',
        'visibility_ceiling': 'planner', 'permission_ceiling': 'read_only', 'lifecycle': 'persistent',
    }}
    import shlex, sys
    env['ZF_KANBAN_AGENT_CODEX_HEADLESS_CMD'] = shlex.join([sys.executable, str(repo / 'tests/e2e/prd_ui/codex_fault_relay.py')])


def setup(project: Path) -> None:
    config = load_config(project / 'zf.yaml')
    log = EventLog(project / config.project.state_dir / 'events.jsonl')
    writer = EventWriter(log)
    service = ControlledActionService(project / config.project.state_dir, writer, config=config,
        project_root=project, actor='web', source='real-recovery-fixture', surface='web')
    def action(name: str, body: dict):
        request = writer.emit('runtime.action.requested', actor='web', payload={'action': name, 'request': body})
        result = service.execute(action=name, requested_action=name, requested=request, payload=body)
        assert result.get('ok'), result
    try:
        for kind in ('abort', 'completed'):
            channel = f'ch-codex-{kind}'
            action('channel-create', {'channel_id': channel, 'name': f'Codex {kind} recovery', 'owner_actor_ref': 'web'})
            action('channel-invite-member', {'channel_id': channel, 'member_id': 'recovery-reader',
                'profile_id': 'recovery-reader', 'member_type': 'provider_agent', 'permission_profile': 'read_only',
                'permissions': ['read', 'message', 'summarize', 'propose_workflow']})
            action('channel-set-leader', {'channel_id': channel, 'leader_member_id': 'recovery-reader',
                'expected_revision': 0, 'idempotency_key': f'{channel}-leader'})
            action('channel-discussion-mode', {'channel_id': channel, 'thread_id': 'main', 'mode': 'conversation',
                'default_responder_id': 'recovery-reader', 'max_rounds': 1})
    finally:
        log.close()


def native_metadata(session_id: str) -> list[dict]:
    home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    files = list((home / 'sessions').glob(f'*/*/*/rollout-*-{session_id}.jsonl'))
    assert len(files) == 1
    rows = []
    for line in files[0].read_text().splitlines():
        row = json.loads(line); body = row.get('payload') or {}
        if row.get('type') == 'event_msg' and body.get('type') in {'task_started', 'turn_aborted', 'task_complete', 'task_completed'}:
            rows.append({'type': body['type'], 'turn_id': body.get('turn_id'), 'timestamp': row.get('timestamp')})
        if row.get('type') == 'turn_context':
            rows.append({'type': 'model_context', 'model': body.get('model'), 'effort': body.get('effort')})
    return rows


def audit(project: Path) -> dict:
    config = load_config(project / 'zf.yaml'); state = project / config.project.state_dir
    log = EventLog(state / 'events.jsonl')
    try: events = log.read_all()
    finally: log.close()
    result = {}
    for kind in ('abort', 'completed'):
        rows = [e for e in events if e.payload.get('channel_id') == f'ch-codex-{kind}']
        failures = [e for e in rows if e.type == 'channel.agent.reply.failed']
        assert len(failures) == 1 and failures[0].payload['failure_status'] == 'delivery_unknown'
        requested = [e for e in rows if e.type == 'channel.agent.reply.requested']
        completed = [e for e in rows if e.type == 'channel.agent.reply.completed']
        sid = failures[0].payload['provider_session_id']
        native = native_metadata(sid)
        if kind == 'abort':
            assert len(requested) == 2 and len(completed) == 1
            assert requested[0].payload['request_id'] == requested[1].payload['request_id']
            assert requested[1].payload['run_generation'] == completed[0].payload['run_generation'] == 2
            receipt = hydrate_sidecar_ref(state, requested[1].payload['native_abort_receipt_ref']).payload
            assert receipt['failure_event_id'] == failures[0].id and receipt['provider_session_id'] == sid
            assert any(e['type'] == 'turn_aborted' and e['turn_id'] == receipt['native_turn_id'] for e in native)
            recovered_sid = completed[0].payload['provider_session_id']
            recovered_native = native_metadata(recovered_sid)
            assert any(e['type'] in {'task_complete', 'task_completed'} and e['turn_id'] != receipt['native_turn_id'] for e in recovered_native)
            result['abort_receipt'] = receipt
            result['recovered_native'] = recovered_native
        else:
            assert len(requested) == 1 and not completed
            assert any(e['type'] in {'task_complete', 'task_completed'} for e in native)
            assert not any(e['type'] == 'turn_aborted' for e in native)
        assert any(e['type'] == 'model_context' and e['model'] == 'gpt-6.1-sol' and e['effort'] == 'high' for e in native)
        result[kind] = {'request_id': failures[0].payload['request_id'], 'provider_session_id': sid,
                        'failure_event_id': failures[0].id, 'native_metadata': native}
    assert not any(e.type in {'task.created', 'workflow.invoke.requested', 'plan.approved', 'channel.consensus.reached'} for e in events)
    return result
