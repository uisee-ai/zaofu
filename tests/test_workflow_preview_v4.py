"""A delivery Preview can safely propose a v4 controller without applying it."""

from pathlib import Path

import pytest

from tests.test_task_pipeline_profile import _task_pipeline
from tests.test_web_workflow_proposals import _prd_client
from tests.test_workflow_preview_review import Agent
from zf.core.config.loader import load_config
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_delivery import build_flow_submit_preview
from zf.runtime.workflow_intake import build_flow_intake
from zf.runtime.workflow_preview_review import WorkflowPreviewReview
from zf.runtime.workflow_requests import load_workflow_request, revise_workflow_request


def test_prd_v4_eight_lane_candidate_is_reviewed_without_applying(tmp_path, monkeypatch):
    _, state = _prd_client(tmp_path, monkeypatch)
    config_file = tmp_path / 'zf.yaml'
    config_file.write_text(config_file.read_text().replace('backend: mock', 'backend: codex').replace(
        '  project: {name: demo, state_dir: .zf}',
        '  project: {name: demo, state_dir: .zf}\n  workflow: {candidate_quality_source: task_contract_required}',
    ))
    original = (tmp_path / 'zf.yaml').read_bytes()
    writer = EventWriter(EventLog(state / 'events.jsonl'))
    intake = build_flow_intake(
        kind='prd', objective='Build the product from docs/prd/request.md',
        request_id='v4-review', backend='mock', project_id='default', target_root='app',
        acceptance=('Deliver a verified product',), output=tmp_path / 'docs/intake/prd.md',
    )
    revise_workflow_request(state, Path(intake['workflow_input_manifest_ref']), actor='test', confirm=True, writer=writer)
    build_flow_submit_preview(config_path=tmp_path / 'zf.yaml', intake_path=Path(intake['intake_ref']), flow_kind='prd', allow_missing_env=True)
    service = WorkflowPreviewReview(state, tmp_path, writer)
    request = load_workflow_request(state, 'v4-review')
    binding = {k: request[k] for k in ('request_id', 'revision', 'proposal_digest')}
    policy = _task_pipeline(mode='blocking', maxActiveTaskPipelines=8, pools={'impl': {'capacity': 8}, 'verify': {'capacity': 8}})
    patches = [
        {'field': 'config.0.lanes', 'replacement': 8},
        {'field': 'config.0.flowProfile', 'replacement': 'prd-flow-v4-task-pipeline'},
        {'field': 'config.0.taskPipeline', 'replacement': policy},
        {'field': 'config.0.targetRoot', 'replacement': 'app'},
    ]
    agent = Agent({'answer': '生成独立实现和验证池的候选配置。', 'changes': patches})
    draft = service.discuss({**binding, 'mode': 'chat', 'message': '使用 PRD v4，8 条实现和 8 条验证 lane，目标 app'}, agent=agent)
    result = service.apply(draft['draft_ref'])
    assert result['requires_config_apply'] and not result['started']
    proposal = hydrate_sidecar_ref(state, result['proposal_ref']).payload
    candidate = hydrate_sidecar_ref(state, proposal['effective_config_ref']).payload
    config_path = tmp_path / 'candidate.yaml'
    config_path.write_text(candidate['yaml'])
    config = load_config(config_path)
    pipeline = config.workflow.flow_metadata['task_pipeline']
    assert pipeline['mode'] == 'blocking'
    assert pipeline['pools']['impl']['capacity'] == 8
    assert pipeline['pools']['verify']['capacity'] == 8
    assert (tmp_path / 'zf.yaml').read_bytes() == original
    assert not any(e.type in {'workflow.config.change.applied', 'workflow.invoke.requested'} for e in writer.event_log.read_all())


def test_v4_candidate_rejects_pool_capacity_without_roles(tmp_path, monkeypatch):
    _, state = _prd_client(tmp_path, monkeypatch)
    # The compiler must reject invalid role capacities; the UI never applies it.
    from zf.core.config.workflow_profiles import expand_prd_flow
    with pytest.raises(ValueError, match='capacity exceeds'):
        expand_prd_flow({'flowProfile': 'prd-flow-v4-task-pipeline', 'lanes': 2,
                         'taskPipeline': _task_pipeline(pools={'impl': {'capacity': 8}, 'verify': {'capacity': 8}})})
