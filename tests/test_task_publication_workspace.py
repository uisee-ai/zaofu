"""A reused role's new workspace must not stall its retired candidate."""
from contextlib import nullcontext

import pytest

from tests.test_task_host_actions import case
from tests.test_task_publication_batch import batch
from zf.core.events import ZfEvent
from zf.runtime.environment_usage import environment_in_use
from zf.runtime.task_host_actions import apply
from zf.runtime.task_host_manifest import git, preview
from zf.runtime.task_publication import reconcile_task_publication


def other_task_turn(service, root, *, change=''):
    other = service.state_dir / 'workdirs/other/project'
    git(service.project_root, 'worktree', 'add', '-b', 'other-task', str(other), 'main')
    identity = {'operation_id': 'op-9', 'task_id': 'T2' if change == 'same_task' else 'T9', 'role_instance': 'dev-2',
        'workflow_run_id': 'run-1', 'task_pipeline_stage': 'impl',
        'task_map_generation': 'map-1', 'operation_generation': 1,
        'active_attempt_id': 'attempt-9', 'dispatch_id': 'dispatch-9'}
    service.writer.append(ZfEvent(type='workflow.operation.requested', payload=identity))
    service.writer.append(ZfEvent(type='workflow.operation.started', payload=identity))
    workspace = str(root if change == 'same_workspace' else other)
    if change == 'overlap':
        (root / 'nested').mkdir()
        workspace = str(root / 'nested')
    if change == 'outside_state':
        workspace = str(service.project_root)
    dispatch = service.writer.append(ZfEvent(type='task.pipeline.stage.dispatched',
        actor='orchestrator', origin='agent' if change == 'agent_dispatch' else 'kernel',
        task_id='T9', correlation_id='run-1', payload={**identity, 'workdir': workspace}))
    service.writer.append(ZfEvent(type='codex.hook.user_prompt_submit', actor='dev-2',
        origin='agent' if change == 'agent_hook' else 'kernel', correlation_id='run-1',
        causation_id='unknown' if change == 'unbound' else dispatch.id,
        payload={'session_id': 'new-session', 'turn_id': 'new-turn',
                 'cwd': str(root) if change == 'wrong_cwd' else workspace}))
    if change == 'settled':
        service.writer.append(ZfEvent(type='workflow.operation.settled', payload=identity))
    if change == 'multiple_owners':
        service.writer.append(ZfEvent(type='workflow.operation.requested',
            payload={**identity, 'operation_id': 'op-10', 'task_id': 'T10'}))
    return other


def test_publication_and_retry_do_not_wait_for_reused_lane(batch):
    service, roots = batch
    root = roots[1]
    other = other_task_turn(service, root)
    old_head = git(root, 'rev-parse', 'HEAD')
    with environment_in_use(other):
        assert reconcile_task_publication(service) == 8
        assert git(root, 'rev-parse', 'HEAD') != old_head
        assert reconcile_task_publication(service) == 8
        assert reconcile_task_publication(service) == 0
    events = service.writer.event_log.read_all()
    assert len([e for e in events if e.type == 'task.rework.requested']) == 8
    assert not any(e.type in {'dev.build.done', 'task.done'} for e in events)


@pytest.mark.parametrize('change', ['same_workspace', 'wrong_cwd', 'agent_dispatch',
                                  'agent_hook', 'unbound', 'settled', 'old_holder',
                                  'same_task', 'overlap', 'outside_state', 'multiple_owners'])
def test_unknown_or_owned_workspace_still_waits(batch, change):
    service, roots = batch
    root = roots[1]
    other_task_turn(service, root, change=change)
    head = git(root, 'rev-parse', 'HEAD')
    with environment_in_use(root) if change == 'old_holder' else nullcontext():
        assert reconcile_task_publication(service) == 7
        assert git(root, 'rev-parse', 'HEAD') == head
    assert not any(e.type == 'human.escalate' and e.task_id == 'T2'
                   for e in service.writer.event_log.read_all())


def test_controlled_host_action_uses_the_same_workspace_fence(batch):
    service, roots = batch
    root = roots[1]
    other = other_task_turn(service, root)
    manifest = preview(service, operation_id='op-2', kind='commit', paths=['probe.py'])
    with environment_in_use(other):
        result = apply(service, manifest, approval_ref='owner:test',
                       reviewed_digest=manifest['manifest_digest'])
    assert result['status'] == 'completed'
    assert result['result']['product_verdict'] == 'not_evaluated'
