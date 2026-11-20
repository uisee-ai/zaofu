"""Producer preflight and relocated Plan ports must accept the same content."""
import hashlib
import json

import pytest

from tests.test_result_submit import _plan_candidate, _running_plan_candidate_operation
from zf.core.config.schema import ZfConfig
from zf.runtime.fanout_artifact_refs import prepare_fanout_synth_reports
from zf.runtime.plan_candidate_ports import plan_port_bodies


def test_author_refs_validate_submit_and_relocate_without_content_loss(tmp_path, monkeypatch):
    runtime, prepared, service, token = _running_plan_candidate_operation(tmp_path)
    author = runtime.state_dir / 'workdirs/planner/project'
    author.mkdir(parents=True)
    candidate = _plan_candidate()
    bodies = {name: candidate.pop(name) for name in ('task_map', 'source_index')}
    ports = []
    for name, body in bodies.items():
        p = author / f'artifacts/plan/{name}.json'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(body))
        ports.append({'logical_name': name, 'ref': str(p.relative_to(author)),
                      'sha256': hashlib.sha256(p.read_bytes()).hexdigest()})
    candidate['plan_ports'] = ports
    before = [e.id for e in runtime.event_log.read_all()]
    files_before = {p for p in runtime.state_dir.rglob('*') if p.is_file()}
    result = service.validate_plan_candidate(operation_id=prepared.operation_id,
        semantic_result=candidate, role_instance='planner', credential=token, project_root=author)
    assert result['status'] == 'passed', result['errors']
    assert [e.id for e in runtime.event_log.read_all()] == before
    assert {p for p in runtime.state_dir.rglob('*') if p.is_file()} == files_before
    monkeypatch.chdir(author)
    submitted = service.submit(operation_id=prepared.operation_id, semantic_result=candidate,
        role_instance='planner', credential=token)
    assert submitted.canonical_event_type == 'issue.triage.child.completed'
    report = prepare_fanout_synth_reports(reports=[{'child_id':'planner', 'role_instance':'planner', 'report':candidate}],
        manifest={'fanout_id':'F1'},state_dir=runtime.state_dir,project_root=runtime.project_root,
        config=ZfConfig(),roles=[])[0]['report']
    errors = []
    assert plan_port_bodies([report],trigger={},state_dir=runtime.state_dir,
                           project_root=runtime.project_root,errors=errors) == bodies
    assert not errors
    assert {p['logical_name']:p['sha256'] for p in report['plan_ports']} == {p['logical_name']:p['sha256'] for p in ports}


@pytest.mark.parametrize('problem', ['digest', 'missing', 'outside', 'symlink', 'traversal', 'state_conflict', 'state_symlink'])
def test_producer_port_validation_preserves_bindings_and_path_boundary(tmp_path, problem):
    state, author = tmp_path / 'state', tmp_path / 'author'
    state.mkdir(); author.mkdir()
    p = author / 'artifacts/plan/port.json'
    p.parent.mkdir(parents=True)
    raw = b'{"scope":"complete"}'
    p.write_bytes(raw)
    ref = str(p.relative_to(author))
    digest = hashlib.sha256(raw).hexdigest()
    outside = tmp_path / 'outside.json'
    outside.write_bytes(raw)
    if problem == 'digest':
        digest = '0' * 64
    elif problem == 'missing':
        p.unlink()
    elif problem == 'outside':
        ref = str(outside)
    elif problem == 'symlink':
        p.unlink(); p.symlink_to(outside)
    elif problem == 'traversal':
        ref = '../outside.json'
    else:
        canonical = state / ref
        canonical.parent.mkdir(parents=True)
        if problem == 'state_conflict':
            canonical.write_text('{"scope":"wrong"}')
        else:
            canonical.symlink_to(outside)
    errors = []
    assert not plan_port_bodies([{'plan_ports':[{'logical_name':'execution_spec', 'ref':ref, 'sha256':digest}]}],
        trigger={},state_dir=state,project_root=author,errors=errors,ports_materialized=False)
    assert errors[0]['code'] == 'plan_port_unreadable'


def test_author_file_is_not_a_materialized_package_port(tmp_path):
    p = tmp_path / 'author.json'
    p.write_text('{"scope":"complete"}')
    errors = []
    assert not plan_port_bodies([{'plan_ports':[{'logical_name':'execution_spec','ref':str(p),
        'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}]}],trigger={},state_dir=tmp_path/'state',
        project_root=tmp_path,errors=errors,ports_materialized=True)
    assert errors[0]['code'] == 'plan_port_unreadable'
