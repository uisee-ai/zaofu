"""Both tracked entry surfaces use the same approved light delivery contract."""
import json
from pathlib import Path

import pytest

from tests.test_channel_prd_task_contract import _ready_prd_fixture, _task_payload, _execute
from tests.test_control_actions_workflow_request import _service
from zf.cli.main import main
from zf.core.config.loader import load_config
from zf.core.events.log import EventLog
from zf.core.state.session import SessionStore
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport
from zf.runtime.workflow_start import WorkflowStartService

pytestmark = pytest.mark.mock_e2e


def _setup(tmp_path, surface, *, root_tests=False, owner_actor_ref="web"):
    if surface == 'channel':
        state, writer, service, authority = _ready_prd_fixture(
            tmp_path, implementation_start=True, owner_actor_ref=owner_actor_ref,
        )
        payload = _task_payload(authority)
        cfgroot = tmp_path / 'config-fixture'
        cfgroot.mkdir()
        _service(cfgroot)
        (tmp_path / 'zf.yaml').write_bytes((cfgroot / 'zf.yaml').read_bytes())
        service.config = load_config(tmp_path / 'zf.yaml')
    else:
        service, _ = _service(tmp_path)
        state, writer = service.state_dir, service.writer
        authority = {}
        payload = {'task_id': 'TASK-LIGHT', 'title': '修复JSON输出', 'execution_mode': 'workflow',
                   'contract': {'behavior': '修复JSON输出', 'acceptance': '非法输入明确拒绝',
                                'verification': 'python -m pytest -q', 'verification_tiers': ['runtime'],
                                'scope': ['counter.py', 'test_counter.py'] if root_tests else ['src/**', 'tests/**'], 'owner_role': 'fix-lane-0',
                                'product_contract_ref': 'requirement.md'}}
    (tmp_path / 'requirement.md').write_text('修复现有JSON输出；非法输入明确拒绝；python -m pytest -q。')
    config_path = tmp_path / 'zf.yaml'
    config_path.write_text(config_path.read_text() + '\n  workflow:\n    kind_routes:\n      issue: {pattern_id: issue-lanes-impl}\n')
    service.config = load_config(config_path)
    created = _execute(service, writer, 'create-task', payload)
    assert created['ok'], created
    task = TaskStore(state / 'kanban.json').get(created['task_id'])
    return service, task, authority


@pytest.mark.parametrize('surface,fault', [
    (surface, fault) for surface in ('kanban', 'channel')
    for fault in ('', 'root_tests', 'changed_contract', 'missing_input', 'tampered_source', 'missing_source')
    if (surface == 'channel' or fault not in ('tampered_source', 'missing_source'))
    and not (surface == 'channel' and fault == 'root_tests')
])
def test_preview_approval_and_light_map_preserve_source_and_scope(tmp_path, monkeypatch, capsys, surface, fault):
    service, task, authority = _setup(tmp_path, surface, root_tests=fault == 'root_tests',
                                     owner_actor_ref="operator:cli")
    state = service.state_dir
    log = EventLog(state / 'events.jsonl')
    start = WorkflowStartService(state, service.config, project_root=tmp_path)
    routes = start.routes(task_id=task.id)
    route = next(r for r in routes['routes'] if r.get('entry_class') == 'light_adapter')
    assert route['start_adapter'] == 'light_delivery_request_submit'
    params = {'target_root': str(tmp_path)}
    if fault == 'missing_input':
        task.contract.verification = ''
        TaskStore(state / 'kanban.json').update(task.id, contract=task.contract)
    preview = start.preview({'task_id': task.id, 'route_id': route['route_id'],
                             'objective': task.contract.behavior, 'parameters': params},
                            require_bindings=False, origin=surface)
    if fault == 'missing_input':
        assert not preview['ok'], preview
        assert not any(e.type == 'workflow.invoke.requested' for e in log.read_all())
        return
    assert preview['ok'], preview
    proposed = start.propose(service.writer, preview['payload'], actor='operator', origin=surface)
    assert proposed['ok'], proposed
    assert not any(e.type == 'workflow.invoke.requested' for e in log.read_all())
    monkeypatch.chdir(tmp_path)
    for key in ('ZF_PROJECT_ROOT', 'ZF_STATE_DIR', 'ZF_WORKFLOW_ACTION_TOKEN'):
        monkeypatch.delenv(key, raising=False)
    args = ['workflow', 'start', '--apply', '--proposal-event-id', proposed['proposal_event_id'],
            '--authorization-ref', 'operator:test', '--authorization-token', 'local-test-only', '--format', 'json']
    assert main(args) == 3
    capsys.readouterr()
    assert not any(e.type == 'workflow.invoke.requested' for e in log.read_all())
    monkeypatch.setenv('ZF_WORKFLOW_ACTION_TOKEN', 'local-test-only')
    if fault == 'changed_contract':
        task.contract.scope = ['different/**']
        TaskStore(state / 'kanban.json').update(task.id, contract=task.contract)
    elif fault == 'tampered_source':
        (state / authority['source_ref']).write_text('{}')
    elif fault == 'missing_source':
        (state / authority['source_ref']).unlink()
    code = main(args)
    result = json.loads(capsys.readouterr().out)
    if fault and fault != 'root_tests':
        assert code != 0, result
        if fault in ('tampered_source', 'missing_source'):
            assert result['status'] == 'workflow_input_invalid'
        assert not any(e.type == 'workflow.invoke.requested' for e in log.read_all())
        return
    assert code == 0, result
    assert main(args) == 0
    capsys.readouterr()
    invokes = [e for e in log.read_all() if e.type == 'workflow.invoke.requested']
    assert len(invokes) == 1
    if surface == 'channel':
        assert invokes[0].payload['source_refs']['channel_prd_ref'] == authority['source_ref']
    SessionStore(state / 'session.yaml').create(project_root=str(tmp_path))
    runtime = Orchestrator(state, service.config,
                           TmuxTransport(TmuxSession(session_name='unused', dry_run=True)), project_root=tmp_path)
    runtime.run_once(events=invokes)
    entries = [e for e in log.read_all() if e.type == 'issue.requested']
    assert len(entries) == 1
    runtime.run_once(events=entries)
    maps = [e for e in log.read_all() if e.type == 'task_map.ready']
    assert len(maps) == 1
    data = json.loads((state / maps[0].payload['task_map_ref']).read_text())
    assert len(data['tasks']) == 1
    assert data['tasks'][0]['allowed_paths'] == task.contract.scope
    if task.contract.acceptance_criteria:
        assert data['tasks'][0]['acceptance_criteria'] == task.contract.acceptance_criteria
    stages = service.config.workflow.stages
    assert any('verify' in stage.id for stage in stages)
    assert any(any('judge' in role for role in stage.roles) for stage in stages)

    runtime.run_once(events=maps)
    all_events = log.read_all()
    rejected = [e for e in all_events if e.type == 'fanout.cancelled']
    assert not rejected, [e.payload for e in rejected]
    children = [t for t in TaskStore(state / 'kanban.json').list_all() if t.id != task.id]
    assert children, 'Task Map must produce a writer task through its real consumer'
    assert children[0].contract.scope == task.contract.scope
