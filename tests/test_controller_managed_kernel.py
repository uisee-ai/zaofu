"""Shared Kernel/Gate contracts and physical Project command callers."""

import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
import yaml

from tests.test_controller_managed import managed as managed, workspace as workspace
from tests.test_lane_stage_streaming_runtime import (
    _state, _start, _fanout_id, _manifest, _child, _complete_writer, _complete_verify,
)


@pytest.mark.mock_e2e
@pytest.mark.parametrize('mode', ['standalone', 'managed'])
def test_same_workflow_kernel_gates_and_artifacts_in_both_modes(mode, request, tmp_path, monkeypatch):
    from zf.core.config.loader import load_config
    from zf.core.config.schema import RoleConfig
    from zf.core.config.project_context import ProjectContext
    from zf.core.workspace.registry import WorkspaceRegistry
    from zf.integrations.controller.execution import stream_turn

    if mode == 'managed':
        config, peer, project, _ = request.getfixturevalue('managed')
        root = project.project_root
        raw = yaml.safe_load(project.config_path.read_text())
        raw['project']['state_dir'] = '.zf'
        project.config_path.write_text(yaml.safe_dump(raw))
        context = ProjectContext(root, project.config_path, load_config(project.config_path), root / '.zf')
        WorkspaceRegistry(workspace=config.workspace).upsert_context(context)
    else:
        request.getfixturevalue('workspace')  # Isolate HOME/registry; no enrollment or binding.
        root = tmp_path / 'standalone'
        root.mkdir()
    monkeypatch.chdir(root)
    state, log, transport, orch = _state(root, task_count=1)
    sent = transport.send_task

    def dispatch(role_name, briefing_path, prompt, *, context=None):
        def provider():
            sent(role_name, briefing_path, prompt, context=context)
            return [SimpleNamespace(usage={'input_tokens': 3, 'output_tokens': 1})], SimpleNamespace(value='completed')
        stream_turn(provider, root=root,
            context=context or SimpleNamespace(operation_id='', run_id=''),
            role=RoleConfig(name=role_name), session_id=role_name, cancel=lambda: None)

    transport.send_task = dispatch
    _start(orch)
    writer_id = _fanout_id(log, 'demo-impl')
    child = _child(_manifest(state, writer_id), 'TASK-1')
    _complete_writer(orch, fanout_id=writer_id, child=child,
                     task_id='TASK-1', file_name='task-1.txt')
    assert (Path(child['workdir']) / 'task-1.txt').read_text() == 'TASK-1\n'
    verifier_id = _fanout_id(log, 'demo-verify')
    _complete_verify(orch, state_dir=state, fanout_id=verifier_id)
    assert 'test.passed' in [event.type for event in log.read_all()]
    assert transport.sent
    from zf.runtime.workflow_spine_projection import refresh_spine_projections
    assert refresh_spine_projections(state, log)['stage_count'] >= 1
    if mode == 'managed':
        from zf.integrations.controller.workspace import synchronize_workspace
        synchronize_workspace(config)
        assert len(peer.budgets) == len(transport.sent) == len(peer.settlements)


def test_actual_project_load_unload_commands_wait_for_owned_runtime_and_do_not_repeat(managed, monkeypatch):
    from zf.core.events import EventWriter
    from zf.core.events.factory import event_log_from_project
    from zf.integrations.controller.ledger import Ledger
    from zf.integrations.controller.workspace import synchronize_workspace
    from zf.runtime import controller_actions

    config, peer, context, _ = managed
    writer = EventWriter(event_log_from_project(context.state_dir, config=context.config))
    project = context.config.observability.controller.project_id
    guard = context.state_dir / 'processes' / 'watcher.pid.json'
    effects = []

    def start(command, **kwargs):
        assert command[-1] == 'start'
        assert kwargs['env']['ZF_CONTROLLER_PROJECT_LEASE'] == peer.leases[-1]['id']
        assert kwargs['cwd'] == context.project_root
        effects.append('start')
        guard.parent.mkdir(parents=True, exist_ok=True)
        guard.write_text(json.dumps({'owner_pid': os.getpid()}))
        writer.emit('loop.started', actor='fixture', payload={})
        return SimpleNamespace(pid=os.getpid(), poll=lambda: None)

    def stop(command, **kwargs):
        assert command[-1] == 'stop'
        effects.append('stop')
        guard.unlink()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(controller_actions.subprocess, 'Popen', start)
    monkeypatch.setattr(controller_actions.subprocess, 'run', stop)

    def offer(identifier, action):
        peer.commands = [{'id': identifier, 'workspace_id': config.workspace_id,
            'deployment_id': peer.deployment_id, 'generation': 1, 'policy_version': 2,
            'expires_at': int(time.time()) + 60, 'user_id': 'owner', 'session_id': 'login',
            'payload': {'action': action, 'project_id': project}}]
        synchronize_workspace(config)
        synchronize_workspace(config)
        return Ledger(config.state_dir).get(identifier)['body']['result']

    assert offer('load-once', 'project.load')['effect'] == 'watcher_started'
    assert offer('unload-once', 'project.unload')['effect'] == 'runtime_stopped'
    assert effects == ['start', 'stop']
    ledger = Ledger(config.state_dir)
    row = ledger.get('project:' + project)
    ledger.put(row['id'], 'draining', row['body'])
    assert offer('load-while-unresolved', 'project.load')['status'] == 'previous_runtime_unresolved'
    assert effects == ['start', 'stop']


def test_inherited_project_owner_requires_same_git_repository(managed, monkeypatch, tmp_path):
    from tests.test_lane_stage_streaming_runtime import _init_repo, _git
    from zf.integrations.controller.binding import project_binding, ManagedDenied
    _, _, context, _ = managed
    _init_repo(context.project_root)
    linked = tmp_path / 'linked-worker'
    _git(context.project_root, 'worktree', 'add', '--detach', str(linked))
    monkeypatch.setenv('ZF_PROJECT_ROOT', str(context.project_root))
    assert project_binding(linked)[2].project_root == context.project_root
    outside = tmp_path / 'unrelated'
    outside.mkdir()
    monkeypatch.setenv('GIT_DIR', str(context.project_root / '.git'))
    with pytest.raises(ManagedDenied):
        project_binding(outside)
