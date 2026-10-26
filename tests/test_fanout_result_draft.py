"""Legacy reader result drafts must be editable without opening state artifacts."""
from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from zf.cli.hook_workdir_guard import is_authorized_result_scratch_target
from zf.core.config.schema import FanoutAggregateConfig, ProjectConfig, RoleConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.events.model import ZfEvent
from zf.runtime.fanout import FanoutChild, FanoutContext
from zf.runtime.orchestrator import Orchestrator


@pytest.fixture
def draft(tmp_path):
    state = tmp_path / '.state'
    state.mkdir()
    log = EventLog(state / 'events.jsonl')
    writer = EventWriter(log)
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state
    orch.project_root = tmp_path
    orch.config = ZfConfig(project=ProjectConfig(name='research'))
    child = FanoutChild('risk', 'risk')
    context = FanoutContext('fanout-research', 'research', 'fanout_reader',
                            'workflow-research', 'trigger', '', [child])
    briefing = orch._write_fanout_briefing(
        role=RoleConfig(name='risk', instance_id='risk', backend='mock', role_kind='reader'),
        context=context, child_id='risk', run_id='attempt-risk',
        aggregate=FanoutAggregateConfig(mode='wait_for_all',
            child_success_event='research.child.completed', child_failure_event='research.child.failed',
            success_event='research.fanout.completed', failure_event='research.fanout.failed'),
        child_payload={'task_id':'TASK-RESEARCH'},
    ).read_text()
    command = briefing.split('Success command:\n```bash\n', 1)[1].split('\n```', 1)[0]
    argv = shlex.split(command)
    path = Path(argv[argv.index('--payload-file') + 1])
    writer.append(context.started_event())
    writer.append(context.child_dispatched_event(child, run_id='attempt-risk'))
    yield state, log, writer, path, context, child
    log.close()


def allowed(draft, *, actor='risk', target=None):
    state, log, _, path, _, _ = draft
    return is_authorized_result_scratch_target(state, log, actor=actor, target=target or path)


def test_actual_reader_briefing_draft_is_editable_and_survives_recovery(draft):
    assert allowed(draft)
    path = draft[3]
    payload = json.loads(path.read_text())
    payload['report']['summary'] = 'B preserves strict integers; C accepts bool.'
    path.write_text(json.dumps(payload))
    assert allowed(draft)
    assert path.parent.name == 'completion-drafts'


@pytest.mark.parametrize('terminal', ['fanout.child.completed', 'fanout.child.failed',
    'fanout.cancelled', 'fanout.aggregate.completed', 'run.goal.blocked'])
def test_result_draft_authority_expires(draft, terminal):
    assert allowed(draft)
    draft[2].append(ZfEvent(type=terminal, actor='zf-cli', correlation_id='workflow-research',
        payload={'fanout_id':'fanout-research', 'child_id':'risk', 'run_id':
                 'workflow-research' if terminal=='run.goal.blocked' else 'attempt-risk',
                 'status':'completed'}))
    assert not allowed(draft)


def test_result_draft_does_not_authorize_other_actor_or_arbitrary_artifact(draft):
    assert allowed(draft)
    assert not allowed(draft, actor='source')
    other = draft[3].parent / 'other.json'
    other.write_text('{}')
    assert not allowed(draft, target=other)


def test_result_draft_rejects_new_dispatch_old_attempt(draft):
    draft[2].append(draft[4].child_dispatched_event(draft[5], run_id='attempt-next'))
    assert not allowed(draft)


def test_result_draft_rejects_symlink(draft, tmp_path):
    path = draft[3]
    path.unlink()
    target = tmp_path / 'outside.json'
    target.write_text('{}')
    path.symlink_to(target)
    assert not allowed(draft)


def test_recovery_preserves_draft_content(draft):
    from zf.runtime.fanout_result_draft import prepare_fanout_result_draft
    path = draft[3]
    original = {'report': {'summary': 'Existing independent findings'}}
    path.write_text(json.dumps(original))
    assert prepare_fanout_result_draft(draft[0], run_id='attempt-risk', label='success',
                                      payload={'report': {'summary': 'template'}}) == str(path)
    assert json.loads(path.read_text()) == original


@pytest.mark.parametrize('operation,expected', [('Update', 0), ('Delete', 2), ('Add', 2)])
def test_task_scope_hook_only_allows_in_place_draft_edit(draft, monkeypatch, operation, expected):
    from zf.cli import hook_recv
    monkeypatch.setattr(hook_recv, '_active_task_id_for_actor', lambda *_: 'TASK-RESEARCH')
    monkeypatch.setattr(hook_recv, '_active_task_scope', lambda *_: ['sources.md'])
    result = hook_recv._evaluate_allowed_paths_guard(
        state_dir=draft[0], actor='risk', event_type='codex.hook.pre_tool_use',
        event_payload={'tool_name':'apply_patch', 'tool_input':{
            'patch': f'*** Begin Patch\n*** {operation} File: {draft[3]}\n@@\n-old\n+new\n*** End Patch'},
            'cwd':str(draft[0].parent)}, event_log=draft[1], event_writer=draft[2], causation_id=None,
    )
    assert result == expected
