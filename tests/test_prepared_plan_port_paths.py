"""Prepared ports must be usable by the Package before Critic spends a turn."""
import hashlib
import json
import pytest
from tests.test_plan_candidate_preflight import _PlanSynthHarness, _source_index, _task_map


def _dispatch(harness):
    harness._dispatch_fanout_synth('fanout-path', {
        'fanout_id': 'fanout-path', 'trace_id': 'run-path', 'stage_id': 'issue-triage',
        'trigger_event_id': 'evt-input',
        'trigger_payload': {'flow_kind': 'issue', 'issue_ref': 'issue.md'},
        'aggregate_config': {'success_event': 'task_map.ready',
                            'failure_event': 'issue.plan.failed', 'synth_role': 'plan-critic'},
        'children': [],
    }, 'wait_for_all', 'plan-critic')


def _report(port):
    return {'child_id': 'planner', 'report': {
        'task_map': _task_map(), 'source_index': _source_index(),
        'source_index_ref': 'inline:source-index', 'plan_md': '# Plan',
        'plan_ports': [port],
    }}


@pytest.mark.parametrize('absolute', [True, False])
def test_requirement_is_pinned_before_actual_critic_dispatch(tmp_path, absolute):
    harness = _PlanSynthHarness(tmp_path, [])
    path = harness.state_dir / 'workflow-requests/run-path/requirements/revision-0002.json'
    path.parent.mkdir(parents=True)
    raw = json.dumps({'schema_version': 'requirement-spec.v1', 'objective': 'Keep all requirements.'}).encode()
    path.write_bytes(raw)
    ref = str(path) if absolute else path.relative_to(harness.state_dir).as_posix()
    harness.reports = [_report({'logical_name': 'requirement_spec', 'ref': ref,
                               'sha256': hashlib.sha256(raw).hexdigest()})]
    _dispatch(harness)
    assert harness.critic_dispatch_attempted
    # The upstream relocation now pins digest-bound input refs as Package
    # sidecars. Prove exact bytes and binding instead of requiring rejection.
    from zf.runtime.sidecar_refs import sidecar_path
    reports = list(harness.state_dir.glob('artifacts/fanouts/fanout-path/prepared-child-reports/planner/*.json'))
    assert len(reports) == 1
    port = json.loads(reports[0].read_text())['plan_ports'][0]
    assert port['ref'] != ref
    assert port['sha256'] == hashlib.sha256(raw).hexdigest()
    assert sidecar_path(harness.state_dir, port['ref']).read_bytes() == raw
    assert path.read_bytes() == raw


@pytest.mark.parametrize('location', ['inline', 'sidecar', 'relative_workdir', 'absolute_workdir'])
def test_supported_port_forms_reach_critic_after_normal_relocation(tmp_path, location):
    harness = _PlanSynthHarness(tmp_path, [])
    body = {'schema_version': 'requirement-spec.v1', 'objective': 'Keep all requirements.'}
    raw = json.dumps(body).encode()
    port = {'logical_name': 'requirement_spec'}
    if location == 'inline':
        port['body'] = body
    else:
        root = harness.state_dir if location == 'sidecar' else harness.state_dir / 'workdirs/planner/project'
        path = root / 'artifacts/plan/requirement.json'
        path.parent.mkdir(parents=True)
        path.write_bytes(raw)
        port.update(ref=str(path) if location == 'absolute_workdir' else 'artifacts/plan/requirement.json',
                    sha256=hashlib.sha256(raw).hexdigest())
    harness.reports = [_report(port)]
    _dispatch(harness)
    assert harness.critic_dispatch_attempted
    if 'workdir' in location:
        relocated = harness.state_dir / 'artifacts/fanouts/fanout-path/planner/artifacts/plan/requirement.json'
        assert relocated.read_bytes() == raw


@pytest.mark.parametrize('kind', ['parent_traversal', 'symlink_escape', 'private_root'])
def test_prepared_port_path_guard_is_read_only_and_rejects_escapes(tmp_path, kind):
    from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight
    state = tmp_path / 'state'
    source = tmp_path / 'outside.json'
    source.write_text('{"note":"fixture"}')
    (state / 'artifacts').mkdir(parents=True)
    if kind == 'symlink_escape':
        (state / 'artifacts/link.json').symlink_to(source)
        ref = 'artifacts/link.json'
    elif kind == 'private_root':
        (state / 'private').mkdir()
        (state / 'private/input.json').write_bytes(source.read_bytes())
        ref = 'private/input.json'
    else:
        ref = 'artifacts/../../outside.json'
    report = _report({'logical_name': 'execution_spec', 'ref': ref,
                      'sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result = evaluate_plan_candidate_preflight(
        state_dir=state, project_root=tmp_path, reports=[report],
        manifest={'trigger_payload': {'flow_kind': 'issue', 'issue_ref': 'issue.md'}},
        metadata={'flow_kind': 'issue'}, ports_materialized=True,
    )
    assert any(e['code'] == 'plan_port_not_materialized' for e in result['errors'])
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
