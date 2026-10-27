"""Controller synthesis must produce a reviewable config, never silently apply it."""
from pathlib import Path

import pytest
import yaml

from tests.test_workflow_proposal import _config, _request, _synthesis_result
from zf.core.config.loader import load_config
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_proposal import (
    build_workflow_proposal, materialize_synthesized_workflow_candidate,
)
from zf.runtime.workflow_requests import load_workflow_request
from zf.runtime.workflow_synthesis import run_workflow_synthesis


@pytest.mark.parametrize("family,kind,source", [
    ("IssueFlow", "issue", "issueRef"), ("PrdFlow", "prd", "prdRef"),
    ("RefactorFlow", "refactor", "objectiveRef"),
])
@pytest.mark.parametrize("lane_key", ["lanes", "laneCount", "lane_count"])
def test_admitted_controller_lanes_become_bound_candidate(tmp_path, family, kind, source, lane_key):
    base = tmp_path / "zf.yaml"
    _config(base)
    body = base.read_text().replace("IssueFlow", family).replace("issueRef", source)
    body = body.replace("spec:\n  lanes:", "spec:\n  deliveryPolicy: report_only\n  lanes:", 1)
    if family == "RefactorFlow":
        body = body.replace("  deliveryPolicy:", "  flowProfile: refactor-flow/v3\n  assembly: none\n  deliveryPolicy:", 1)
        body = body.replace("  backend: mock", "  laneRoleTemplate: {backend: mock}")
    else:
        body = body.replace("  deliveryPolicy:", "  topology: fanout\n  deliveryPolicy:", 1)
    body = body.replace("  lanes:", f"  {lane_key}:")
    base.write_text(body)
    config = load_config(base)
    state = tmp_path / ".zf"
    request = _request(state, requirement_fields={
        "kind": kind, "source_root": "src", "target_root": "app",
    })
    writer = EventWriter(EventLog(state / "events.jsonl"))
    result = _synthesis_result(request, lanes=2)
    result["selected_flow_family"] = family
    result["short_flow_spec"]["flow_family"] = family
    synthesis = run_workflow_synthesis(
        state_dir=state, project_root=tmp_path, config=config, writer=writer,
        request_id=request["request_id"], actor="test", candidate_result=result,
    )
    request = load_workflow_request(state, request["request_id"])
    candidate = materialize_synthesized_workflow_candidate(
        state, request=request, base_config_path=base,
        synthesis_result_ref=synthesis.result_ref, flow_kind=kind,
    )
    assert candidate != base
    assert base.read_text() == body
    docs = list(yaml.safe_load_all(candidate.read_text()))
    assert docs[0]["spec"][lane_key] == 2
    original = list(yaml.safe_load_all(body))
    original[0]["spec"][lane_key] = 2
    assert docs == original  # No implicit topology, gate, permission or budget changes.
    proposal, _ = build_workflow_proposal(
        state, request=request, base_config_path=base, candidate_config_path=candidate,
        synthesis_result_ref=synthesis.result_ref, flow_kind=kind, writer=writer,
        preflight={"status": "GO", "blockers": [],
                   "delivery_contract": {"strictness": "standard"}},
    )
    assert proposal["change_mode"] == "config_change"
    assert proposal["approval_status"] == "approvable", proposal["blockers"]
    assert base.read_text() == body
    effective = hydrate_sidecar_ref(state, proposal["effective_config_ref"]).payload["config"]
    assert len([r for r in effective["roles"] if r["role_kind"] == "writer"]) == 2
    assert not candidate.exists()


@pytest.mark.parametrize("mutation,lanes,error", [
    ("light", 2, "light topology"),
    ("ambiguous", 2, "ambiguous lane aliases"),
    ("duplicate", 2, "exactly one"),
    ("none", True, "integer"),
    ("none", 65, "integer"),
])
def test_controller_candidate_rejects_unsupported_changes(tmp_path, mutation, lanes, error):
    from zf.runtime.workflow_controller_candidate import materialize_controller_candidate

    base = tmp_path / "zf.yaml"
    _config(base)
    body = base.read_text()
    if mutation == "ambiguous":
        body = body.replace("  lanes: 1", "  topology: fanout\n  lanes: 1\n  laneCount: 1")
    elif mutation == "duplicate":
        body += "\n---\n" + body
    base.write_text(body)
    with pytest.raises(ValueError, match=error):
        materialize_controller_candidate(
            base_path=base, request_id="test-request",
            short_spec={"flow_family": "IssueFlow", "parameters": {"lanes": lanes}},
        )
    assert base.read_text() == body
    assert not list(tmp_path.glob(".*.tmp"))


def test_controller_noop_retains_exact_config_bytes(tmp_path):
    from zf.runtime.workflow_controller_candidate import materialize_controller_candidate

    base = tmp_path / "zf.yaml"
    _config(base)
    original = base.read_bytes()
    result = materialize_controller_candidate(
        base_path=base, request_id="test-request",
        short_spec={"flow_family": "IssueFlow", "parameters": {"lanes": 1}},
    )
    assert result == base
    assert base.read_bytes() == original


@pytest.mark.parametrize("family,kind", [("IssueFlow", "issue"), ("PrdFlow", "prd"),
                                        ("RefactorFlow", "refactor")])
def test_task_pipeline_requires_explicit_pool_revision(tmp_path, family, kind):
    from zf.runtime.workflow_controller_candidate import materialize_controller_candidate

    source = Path(__file__).parents[1] / f"examples/prod/controller/{kind}-task-pipeline-v4-canary.yaml"
    docs = list(yaml.safe_load_all(source.read_text()))
    base = tmp_path / "zf.yaml"
    base.write_text(yaml.safe_dump_all(docs))
    lanes = docs[0]["spec"]["lanes"] + 1
    original = base.read_bytes()
    with pytest.raises(ValueError, match="explicit pool/config revision"):
        materialize_controller_candidate(
            base_path=base, request_id="task-pipeline-request",
            short_spec={"flow_family": family, "parameters": {"lanes": lanes}},
        )
    assert base.read_bytes() == original


def test_admitted_prd_v4_pools_become_reviewable_candidate(tmp_path):
    base = tmp_path / 'zf.yaml'
    _config(base)
    base.write_text(base.read_text().replace('IssueFlow', 'PrdFlow').replace('issueRef', 'prdRef').replace(
        'spec:\n  lanes:', 'spec:\n  deliveryPolicy: report_only\n  lanes:', 1))
    original = base.read_bytes()
    config = load_config(base)
    state = tmp_path / '.zf'
    request = _request(state, requirement_fields={'kind': 'prd', 'target_root': 'app'})
    writer = EventWriter(EventLog(state / 'events.jsonl'))
    result = _synthesis_result(request, lanes=8)
    result['selected_flow_family'] = 'PrdFlow'
    result['short_flow_spec']['flow_family'] = 'PrdFlow'
    result['short_flow_spec']['parameters']['task_pipeline'] = {
        'profile': 'prd-flow-v4-task-pipeline', 'mode': 'blocking',
        'impl_capacity': 8, 'verify_capacity': 8, 'max_active_task_pipelines': 8,
    }
    synthesis = run_workflow_synthesis(state_dir=state, project_root=tmp_path, config=config,
                                       writer=writer, request_id=request['request_id'],
                                       actor='test', candidate_result=result)
    request = load_workflow_request(state, request['request_id'])
    candidate = materialize_synthesized_workflow_candidate(
        state, request=request, base_config_path=base,
        synthesis_result_ref=synthesis.result_ref, flow_kind='prd',
    )
    compiled = load_config(candidate).workflow.flow_metadata['task_pipeline']
    assert compiled['profile_id'] == 'prd-flow-v4-task-pipeline'
    assert compiled['mode'] == 'blocking'
    assert compiled['pools']['impl']['capacity'] == 8
    assert compiled['pools']['verify']['capacity'] == 8
    proposal, _ = build_workflow_proposal(
        state, request=request, base_config_path=base, candidate_config_path=candidate,
        synthesis_result_ref=synthesis.result_ref, flow_kind='prd', writer=writer,
        preflight={'status': 'GO', 'blockers': [], 'delivery_contract': {'strictness': 'standard'}},
    )
    assert proposal['change_mode'] == 'config_change'
    assert proposal['approval_status'] == 'approvable', proposal['blockers']
    assert base.read_bytes() == original
    assert not any(e.type in {'workflow.config.change.applied', 'workflow.invoke.requested'} for e in writer.event_log.read_all())


def test_prd_v4_candidate_must_match_admitted_pool_capacity(tmp_path):
    base = tmp_path / 'zf.yaml'
    _config(base)
    base.write_text(base.read_text().replace('IssueFlow', 'PrdFlow').replace('issueRef', 'prdRef').replace(
        'spec:\n  lanes:', 'spec:\n  deliveryPolicy: report_only\n  lanes:', 1))
    state = tmp_path / '.zf'
    request = _request(state, requirement_fields={'kind': 'prd', 'target_root': 'app'})
    writer = EventWriter(EventLog(state / 'events.jsonl'))
    result = _synthesis_result(request, lanes=8)
    result['selected_flow_family'] = result['short_flow_spec']['flow_family'] = 'PrdFlow'
    result['short_flow_spec']['parameters']['task_pipeline'] = {
        'profile': 'prd-flow-v4-task-pipeline', 'mode': 'blocking',
        'impl_capacity': 8, 'verify_capacity': 8, 'max_active_task_pipelines': 8,
    }
    synthesis = run_workflow_synthesis(state_dir=state, project_root=tmp_path, config=load_config(base),
                                       writer=writer, request_id=request['request_id'],
                                       actor='test', candidate_result=result)
    request = load_workflow_request(state, request['request_id'])
    candidate = materialize_synthesized_workflow_candidate(
        state, request=request, base_config_path=base, synthesis_result_ref=synthesis.result_ref, flow_kind='prd')
    docs = list(yaml.safe_load_all(candidate.read_text()))
    docs[0]['spec']['taskPipeline']['pools']['verify']['capacity'] = 4
    candidate.write_text(yaml.safe_dump_all(docs, sort_keys=False))
    proposal, _ = build_workflow_proposal(
        state, request=request, base_config_path=base, candidate_config_path=candidate,
        synthesis_result_ref=synthesis.result_ref, flow_kind='prd', writer=writer,
        preflight={'status': 'GO', 'blockers': [], 'delivery_contract': {'strictness': 'standard'}},
    )
    assert proposal['approval_status'] == 'blocked'
    assert any('verify_capacity requested 8, compiled 4' in item['message'] for item in proposal['blockers'])


@pytest.mark.parametrize('change', [
    {'mode': 'unknown'}, {'verify_capacity': 9}, {'impl_capacity': True},
    {'profile': 'unregistered'}, {'unknown': 1},
])
def test_prd_v4_candidate_rejects_invalid_pool_inputs(tmp_path, change):
    from zf.runtime.workflow_controller_candidate import materialize_controller_candidate

    base = tmp_path / 'zf.yaml'
    _config(base)
    base.write_text(base.read_text().replace('IssueFlow', 'PrdFlow').replace('issueRef', 'prdRef'))
    original = base.read_bytes()
    pipeline = {'profile': 'prd-flow-v4-task-pipeline', 'mode': 'blocking', 'impl_capacity': 8,
                'verify_capacity': 8, 'max_active_task_pipelines': 8, **change}
    with pytest.raises(ValueError):
        materialize_controller_candidate(base_path=base, request_id='test', short_spec={
            'flow_family': 'PrdFlow', 'parameters': {'lanes': 8, 'task_pipeline': pipeline},
        })
    assert base.read_bytes() == original
