"""Partial graph first paint and its bound transition to the complete API."""
import json
from unittest.mock import Mock

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.workflow_requests import workflow_request_path
from zf.web.projections import workflow_workbench_cache, workflow_workbench_sources
from tests.test_workflow_preview_review import setup  # noqa: F401


def test_structure_skips_plan_hydration_then_full_graph_keeps_every_detail(setup, monkeypatch):
    client, service, binding = setup
    request_id = binding["request_id"]
    service.writer.append(ZfEvent(type="plan.approval.requested", origin="kernel", payload={
        "request_id": request_id, "plan_id": "PLAN-1",
    }))
    original = workflow_workbench_sources.build_plan_preview
    preview = Mock(wraps=original)
    monkeypatch.setattr(workflow_workbench_sources, "build_plan_preview", preview)
    url = f"/api/projects/default/workflow-requests/{request_id}/graph"
    structure = client.get(url + "/structure")
    assert structure.status_code == 200, structure.text
    partial = structure.json()
    assert partial["schema_version"] == "sdlc-workbench-structure.v1" and partial["partial"]
    assert partial["scope"]["request_id"] == request_id and partial["generation"]
    assert "plans" not in partial
    assert all(node["kind"] == "workflow" for node in partial["nodes"])
    preview.assert_not_called()
    assert partial["nodes"]
    selected = partial["nodes"][0]["id"]
    node = client.get(url + "/node", params={"node_id": selected},
                      headers={"X-ZF-Structure-Generation": partial["generation"]})
    assert node.status_code == 200, node.text
    assert node.json()["scope"] == partial["scope"] and node.json()["node"]["id"] == selected
    preview.assert_not_called()
    assert client.get(url + "/node", params={"node_id": selected}).status_code == 422
    assert client.get(url + "/node", params={"node_id": selected}, headers={
        "X-ZF-Structure-Generation": "",
    }).status_code == 400
    full = client.get(url, headers={"X-ZF-Structure-Generation": partial["generation"]})
    assert full.status_code == 200, full.text
    assert not full.json().get("partial")
    preview.assert_called()
    actual = {node["id"]: node for node in full.json()["nodes"]}
    assert node.json()["node"] == actual[selected]
    for node in partial["nodes"]:
        assert (node["label"], node["status"]) == (actual[node["id"]]["label"], actual[node["id"]]["status"])
    assert client.get(url + "/structure", headers={"If-None-Match": structure.headers["etag"]}).status_code == 304


def test_changed_source_rejects_summary_bound_full_graph_without_changing_full_api(setup):
    client, service, binding = setup
    url = f"/api/projects/default/workflow-requests/{binding['request_id']}/graph"
    partial = client.get(url + "/structure").json()
    service.writer.append(ZfEvent(type="worker.progress", origin="kernel", payload={
        "request_id": binding["request_id"], "message": "new generation",
    }))
    response = client.get(url, headers={"X-ZF-Structure-Generation": partial["generation"]})
    assert response.status_code == 409 and response.json()["status"] == "structure_changed"
    if partial["nodes"]:
        stale_node = client.get(url + "/node", params={"node_id": partial["nodes"][0]["id"]},
                                headers={"X-ZF-Structure-Generation": partial["generation"]})
        assert stale_node.status_code == 409 and "node" not in stale_node.json()
    complete = client.get(url)
    assert complete.status_code == 200 and complete.json()["scope"]["head_seq"] > partial["scope"]["head_seq"]


def test_structure_history_is_isolated_from_current_run(setup):
    client, service, binding = setup
    url = f"/api/projects/default/workflow-requests/{binding['request_id']}/graph"
    service.writer.append(ZfEvent(type="worker.progress", origin="kernel", payload={
        "request_id": binding["request_id"], "message": "latest",
    }))
    latest = client.get(url + "/structure").json()
    old = client.get(url + "/structure?as_of=1").json()
    assert old["scope"]["historical"] and old["scope"]["source_seq"] == 1
    assert latest["scope"]["source_seq"] > old["scope"]["source_seq"]
    assert client.get(url + "?as_of=1", headers={
        "X-ZF-Structure-Generation": latest["generation"],
    }).status_code == 409
    assert client.get(url, headers={
        "X-ZF-Structure-Generation": old["generation"],
    }).status_code == 409


@pytest.mark.parametrize("mutation", ["edit", "delete", "repair", "retarget"])
def test_structure_generation_tracks_external_refs_and_symlink_retarget(setup, mutation):
    client, service, binding = setup
    root = service.project_root
    source = root / "docs" / "external-source.md"
    source.parent.mkdir(exist_ok=True)
    target = root / "docs" / "other-source.md"
    target.write_text("other")
    source.write_text("first")
    ref = root / "docs" / "source-link.md" if mutation == "retarget" else source
    if mutation == "retarget":
        ref.symlink_to(source.name)
    if mutation == "repair":
        source.unlink()
    request_path = workflow_request_path(service.state_dir, binding["request_id"])
    request = json.loads(request_path.read_text())
    request["supporting_ref"] = str(ref)
    request_path.write_text(json.dumps(request))
    url = f"/api/projects/default/workflow-requests/{binding['request_id']}/graph"
    first = client.get(url + "/structure")
    assert first.status_code == 200, first.text
    partial = first.json()
    assert partial["generation"] == first.headers["etag"]
    if mutation == "edit":
        source.write_text("changed")
    elif mutation == "delete":
        source.unlink()
    elif mutation == "repair":
        source.write_text("repaired")
    else:
        ref.unlink()
        ref.symlink_to(target.name)
    rejected = client.get(url, headers={"X-ZF-Structure-Generation": partial["generation"]})
    assert rejected.status_code == 409 and rejected.json()["status"] == "structure_changed"
    updated = client.get(url + "/structure", headers={"If-None-Match": first.headers["etag"]})
    assert updated.status_code == 200 and updated.json()["generation"] != partial["generation"]
    assert client.get(url, headers={"X-ZF-Structure-Generation": updated.json()["generation"]}).status_code == 200


def test_compiled_structure_sidecar_tamper_and_repair_rejects_old_generation(setup):
    client, service, binding = setup
    detail_url = f"/api/projects/default/workflow-requests/{binding['request_id']}"
    detail = client.get(detail_url).json()
    compiled_path = service.state_dir / detail["proposal"]["effective_config_ref"]["ref"]
    original = compiled_path.read_bytes()
    url = detail_url + "/graph"
    first = client.get(url + "/structure").json()
    compiled_path.write_text("{}")
    assert client.get(url, headers={"X-ZF-Structure-Generation": first["generation"]}).status_code == 409
    broken = client.get(url + "/structure").json()
    assert broken["generation"] != first["generation"] and not broken["nodes"]
    compiled_path.write_bytes(original)
    assert client.get(url, headers={"X-ZF-Structure-Generation": broken["generation"]}).status_code == 409
    repaired = client.get(url + "/structure").json()
    assert repaired["generation"] != broken["generation"]
    assert repaired["nodes"] == first["nodes"]


def test_source_change_while_complete_graph_builds_never_publishes_mixed_generation(setup, monkeypatch):
    client, service, binding = setup
    detail_url = f"/api/projects/default/workflow-requests/{binding['request_id']}"
    detail = client.get(detail_url).json()
    compiled_path = service.state_dir / detail["proposal"]["effective_config_ref"]["ref"]
    url = detail_url + "/graph"
    partial = client.get(url + "/structure").json()
    original_build = workflow_workbench_cache.build_sdlc_graph
    def changing(inputs):
        graph = original_build(inputs)
        compiled_path.write_text("{}")
        return graph
    monkeypatch.setattr(workflow_workbench_cache, "build_sdlc_graph", changing)
    response = client.get(url, headers={"X-ZF-Structure-Generation": partial["generation"]})
    assert response.status_code in {409, 503}
    assert "nodes" not in response.json()


def test_execution_summary_is_generation_bound_lightweight_and_read_only(setup, monkeypatch):
    client, service, binding = setup
    request_id = binding['request_id']
    service.writer.append(ZfEvent(type='plan.approval.requested', origin='kernel', payload={
        'request_id': request_id, 'plan_id': 'PLAN-1',
    }))
    before = service.writer.event_log.read_all()
    preview = Mock(wraps=workflow_workbench_sources.build_plan_preview)
    monkeypatch.setattr(workflow_workbench_sources, 'build_plan_preview', preview)
    url = f'/api/projects/default/workflow-requests/{request_id}/graph'
    structure = client.get(url + '/structure')
    assert structure.status_code == 200
    generation = structure.json()['generation']
    response = client.get(url + '/execution', headers={'X-ZF-Structure-Generation': generation})
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary['schema_version'] == 'sdlc-workbench-execution.v1'
    assert summary['partial'] and summary['execution_summary'] and summary['generation'] == generation
    assert summary['scope'] == structure.json()['scope']
    assert 'plans' not in summary and 'timeline' not in summary and 'analysis' not in summary
    assert summary['pending_plan_ids'] == ['PLAN-1']
    assert all(node['kind'] in {'workflow', 'operation', 'task', 'run_anchor', 'run', 'preparation', 'requirement'} for node in summary['nodes'])
    preview.assert_not_called()
    assert client.get(url + '/execution', headers={'X-ZF-Structure-Generation': generation, 'If-None-Match': response.headers['etag']}).status_code == 304
    assert service.writer.event_log.read_all() == before
    service.writer.append(ZfEvent(type='worker.progress', origin='kernel', payload={'request_id': request_id}))
    assert client.get(url + '/execution', headers={'X-ZF-Structure-Generation': generation}).status_code == 409
    assert client.get(url + '/execution').status_code == 422


def test_execution_summary_pending_plan_follows_approval_ledger(setup):
    client, service, binding = setup
    request_id = binding['request_id']
    url = f'/api/projects/default/workflow-requests/{request_id}/graph'
    def summary():
        structure = client.get(url + '/structure').json()
        return client.get(url + '/execution', headers={'X-ZF-Structure-Generation': structure['generation']}).json()
    service.writer.append(ZfEvent(type='plan.approval.requested', origin='kernel', payload={
        'request_id': request_id, 'plan_id': 'P1', 'approval_ref': 'approval-a',
    }))
    assert summary()['pending_plan_ids'] == ['P1']
    service.writer.append(ZfEvent(type='approval.resolved', origin='kernel', payload={
        'request_id': request_id, 'approval_ref': 'approval-a', 'resolution': 'approved',
    }))
    assert summary()['pending_plan_ids'] == []
    service.writer.append(ZfEvent(type='plan.approval.requested', origin='kernel', payload={
        'request_id': request_id, 'plan_id': 'P1', 'approval_ref': 'approval-b',
    }))
    assert summary()['pending_plan_ids'] == ['P1']


def test_execution_summary_preserves_operation_and_stage_evidence_without_attempt_nodes():
    from tests.test_sdlc_graph import sources
    from zf.runtime.sdlc_graph import build_sdlc_graph
    events = [
        ZfEvent(id='request', type='workflow.operation.requested', payload={'workflow_run_id': 'run-a', 'operation_id': 'O1', 'parent_stage_id': 'build'}),
        ZfEvent(id='start', type='workflow.operation.started', payload={'workflow_run_id': 'run-a', 'operation_id': 'O1', 'role_instance': 'dev-1', 'active_attempt_id': 'A1'}),
        ZfEvent(id='tool', type='codex.hook.pre_tool_use', causation_id='start', payload={'tool_name': 'Read', 'tool_input': {'file_path': 'src/app.py'}}),
        ZfEvent(id='end', type='workflow.operation.failed', origin='kernel', payload={'workflow_run_id': 'run-a', 'operation_id': 'O1'}),
    ]
    source = sources(events=list(enumerate(events, 1)))
    summary = build_sdlc_graph(source, execution_only=True)
    full = build_sdlc_graph(source)
    selected = {node['id']: node for node in summary['nodes']}
    complete = {node['id']: node for node in full['nodes']}
    assert selected['operation:O1'] == complete['operation:O1']
    assert selected['workflow:stage:build']['status'] == complete['workflow:stage:build']['status'] == 'failed'
    assert selected['operation:O1']['data']['recent_activity'][0]['id'] == 'tool'
    assert not any(node['kind'] in {'attempt', 'evidence', 'fanout', 'agent'} for node in summary['nodes'])
    assert any(edge['source'] == 'workflow:stage:build' and edge['target'] == 'operation:O1' for edge in summary['edges'])
