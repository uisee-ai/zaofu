"""Keep full referenced ports available to preflight, Critic and Package readers."""
import hashlib
import json
import shutil

import pytest

from zf.core.config.schema import ZfConfig
from zf.core.events.model import ZfEvent
from zf.runtime.fanout_artifact_refs import prepare_fanout_synth_reports
from zf.runtime.plan_artifact_package_ports import materialize_explicit_plan_ports
from zf.runtime.plan_candidate_ports import plan_port_bodies
from zf.runtime.plan_synth_handoff import build_plan_candidate_input_refs
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.result_submit import parse_semantic_result_bytes
from zf.runtime.workflow_read_result import normalize_workflow_read_result


@pytest.mark.parametrize("name", ["source_inventory", "capability_matrix", "acceptance_matrix",
                                  "test_matrix", "real_e2e_matrix", "source_index"])
@pytest.mark.parametrize('form', ['inline', 'port_body', 'inline_only'])
def test_complete_referenced_port_survives_author_cleanup(tmp_path, name, form):
    project = tmp_path / "project"
    state = project / "runtime-state"
    author = state / "workdirs/planner/project"
    ref = f"artifacts/plan/{name}.json"
    source = author / ref
    source.parent.mkdir(parents=True)
    body = {"schema_version": "fixture.v1", "content": "完整语义，保留失败不变项", "rows": [{"id": "S1"}]}
    source.write_text(json.dumps(body, ensure_ascii=False))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    original = ({f"{name}_ref": ref, "artifact_refs": [ref], name: body} if form == 'inline' else
                {'artifact_refs': [ref], 'plan_ports': [{'logical_name': name, 'body': body}]})
    if form == 'inline_only':
        original.pop('artifact_refs')
    submitted = parse_semantic_result_bytes(json.dumps({
        **original, "summary": "完整引用交接", "result_semantics": "artifact_production",
    }).encode())
    normalized, issues = normalize_workflow_read_result(ZfEvent(
        type="fanout.child.completed", actor="planner", payload={"report": submitted},
    ))
    assert not issues
    if form == 'inline':
        assert normalized[f"{name}_ref"] == ref
    reports = prepare_fanout_synth_reports(
        reports=[{"child_id": "planner", "role_instance": "planner", "report": normalized}],
        manifest={"fanout_id": "F-REF"}, state_dir=state, project_root=project,
        config=ZfConfig(), roles=[],
    )
    shutil.rmtree(author)
    report = reports[0]["report"]
    assert name not in report
    assert all('body' not in port for port in report.get('plan_ports', []))
    if form == 'inline_only':
        digest = report['plan_ports'][0]['sha256']
    errors = []
    bodies = plan_port_bodies([report], trigger={}, state_dir=state, project_root=project, errors=errors)
    assert not errors
    assert bodies[name] == body
    inputs, _ = build_plan_candidate_input_refs(state_dir=state, project_root=project, reports=reports)
    bound = [item for item in inputs if item["sha256"] == digest]
    assert len(bound) == 1  # duplicate ref discovery must not duplicate the required body
    assert hydrate_sidecar_ref(state, bound[0]).payload == body
    ports = materialize_explicit_plan_ports(state_dir=state, project_root=project, payload=report, flow_kind="prd")
    assert ports[name]["sha256"] == digest
    assert hydrate_sidecar_ref(state, ports[name]).payload == body
    # The final synth aggregation must preserve scalar refs too; checking only
    # direct materialization misses the production Package admission caller.
    from tests.test_writer_fanout_runtime import _FanoutPayloadProbe
    projected = _FanoutPayloadProbe()._generic_fanout_success_payload(
        manifest={'children': []}, success_event='task_map.ready',
        extra_payloads=[{**report, 'source_commit': 'a' * 40}])
    projected_ports = materialize_explicit_plan_ports(
        state_dir=state, project_root=project, payload=projected, flow_kind='prd')
    assert hydrate_sidecar_ref(state, projected_ports[name]).payload == body
    assert (original.get(name) or original['plan_ports'][0]['body']) == body
    (state / ports[name]["ref"]).unlink()
    errors = []
    assert name not in plan_port_bodies([report], trigger={}, state_dir=state, project_root=project, errors=errors)
    assert any(error["code"] == "plan_port_unreadable" for error in errors)


def test_compaction_keeps_conflicts_and_unavailable_references(tmp_path):
    from zf.runtime.plan_report_compaction import compact_report
    state = tmp_path / 'state'
    state.mkdir()
    path = state / 'inventory.json'
    path.write_text('{"mandatory":false}')
    report = {'source_inventory': {'mandatory': True}, 'source_inventory_ref': 'inventory.json'}
    assert compact_report(report, state_dir=state, project_root=tmp_path) == report
    path.unlink()
    assert compact_report(report, state_dir=state, project_root=tmp_path) == report


def test_explicit_port_body_and_digest_survive_lossless_compaction(tmp_path):
    from zf.runtime.plan_report_compaction import compact_report
    state = tmp_path / 'state'
    state.mkdir()
    body = {'entries': [{'disposition': 'pending', 'reason': 'adverse evidence remains'}]}
    raw = json.dumps(body).encode()
    (state / 'ledger.json').write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    port = {'logical_name': 'coverage_ledger', 'body': body, 'ref': 'ledger.json', 'sha256': digest}
    report = {'plan_ports': [port]}
    compacted = compact_report(report, state_dir=state, project_root=tmp_path)
    assert compacted['plan_ports'] == [{key: value for key, value in port.items() if key != 'body'}]
    errors = []
    assert plan_port_bodies([compacted], trigger={}, state_dir=state, project_root=tmp_path, errors=errors)['coverage_ledger'] == body
    assert not errors
    port['sha256'] = 'f' * 64
    assert compact_report(report, state_dir=state, project_root=tmp_path) == report


def test_reference_only_matrices_reach_blocking_package_preparation(tmp_path):
    from tests.test_writer_fanout_runtime import _FanoutPayloadProbe
    from zf.runtime.plan_artifact_package import prepare_plan_artifact_package
    from zf.runtime.plan_report_compaction import compact_report, REFERENCE_PORTS
    from zf.runtime.run_contract import stable_json_sha256, write_run_contract

    state = tmp_path / 'runtime-state'
    state.mkdir()
    (state / 'requirement.md').write_text('交付 AC-1，矩阵正文不可丢失。')
    required = ['requirement_spec', 'goal_claim_set', 'task_map', 'planning_result', *REFERENCE_PORTS]
    (state / 'task_map.json').write_text(json.dumps({
        'schema_version': 'task-map.v1', 'objective': '交付 AC-1',
        'required_plan_ports': required,
        'tasks': [{'task_id': 'T1', 'acceptance_criteria': ['AC-1']}]}))
    report = {'task_map_ref': 'task_map.json', 'prd_ref': 'requirement.md', 'source_commit': 'a' * 40}
    bodies = {}
    for name in REFERENCE_PORTS:
        body = {'schema_version': name.replace('_', '-') + '.v1',
                'status': 'ready', 'rows': ['AC-1'], 'content': '完整验收正文'}
        (state / (name + '.json')).write_text(json.dumps(body))
        report.update({name: body, name + '_ref': name + '.json'})
        bodies[name] = body
    projected = _FanoutPayloadProbe()._generic_fanout_success_payload(
        manifest={'children': []}, success_event='task_map.ready',
        extra_payloads=[compact_report(report, state_dir=state, project_root=tmp_path)])
    contract = {'schema_version': 'run-contract.v1', 'workflow': {'kind': 'prd'}}
    write_run_contract(state, {**contract, 'contract_digest': stable_json_sha256(contract)})
    package, _, _ = prepare_plan_artifact_package(
        state_dir=state, project_root=tmp_path, events=[], payload=projected,
        workflow_run_id='run-matrices', flow_kind='prd', producer_stage_id='prd-plan',
        goal_id='GOAL-1', metadata={'artifact_package': {'mode': 'blocking'}})
    ports = {port['logical_name']: port for port in package['produced']}
    assert set(required) <= ports.keys()
    for name, body in bodies.items():
        assert hydrate_sidecar_ref(state, ports[name]).payload == body
