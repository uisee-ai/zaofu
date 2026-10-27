import hashlib
import json
from dataclasses import asdict

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.operator_plan_preview import build_plan_preview
from zf.runtime.plan_artifact_review import PlanArtifactReview
from zf.runtime.resume_plan_preview import resumed_plan_refs
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_resume import WorkflowBatchResumeCheckpoint
from zf.runtime.workflow_resume_apply import _apply_batch_task_map_ready
from tests.test_workflow_preview_review import Agent


@pytest.fixture
def lineage(tmp_path):
    state = tmp_path / 'state'
    (state / 'artifacts').mkdir(parents=True)
    plan = '# 完整计划\n\n## 需求\n用户可以开始游戏。\n\n## 验收\n验证运行结果。\n'
    plan_path = state / 'artifacts' / 'full.md'
    plan_path.write_text(plan)
    task_path = state / 'artifacts' / 'task-map.json'
    task_path.write_text(json.dumps({'tasks': []}))
    digest = lambda b: hashlib.sha256(b).hexdigest()
    identity = {'workflow_run_id': 'run-1', 'trace_id': 'run-1', 'task_map_ref': 'artifacts/task-map.json',
        'task_map_generation': 'gen-1', 'plan_artifact_package_digest': 'package-1',
        'requirement_spec_ref': 'artifacts/requirement.json', 'requirement_spec_digest': 'requirement-1'}
    writer = EventWriter(EventLog(state / 'events.jsonl'))
    def emit(event_id, kind, payload):
        return writer.append(ZfEvent(id=event_id, type=kind, payload=payload))
    emit('old', 'task_map.ready', {**identity, 'plan_artifact_ref': 'artifacts/full.md',
        'task_map_digest': digest(task_path.read_bytes())})
    emit('requested', 'plan.approval.requested', {**identity, 'plan_id': 'old',
        'plan_artifact_ref': 'artifacts/full.md', 'plan_artifact_digest': digest(plan.encode())})
    emit('approved', 'plan.approved', {'plan_id': 'old'})
    emit('fanout-start', 'fanout.started', {'fanout_id': 'writer-1', 'trigger_event_id': 'old',
        'trace_id': 'run-1', 'task_map_ref': identity['task_map_ref'], 'expected_children': [
            {'child_id': 'child-1', 'payload': {}}]})
    emit('failure', 'integration.failed', {**identity, 'fanout_id': 'writer-1'})
    resume = {**identity, 'source': 'workflow_resume_batch', 'resume_scope': 'failed_children_only',
        'upstream_fanout_id': 'writer-1', 'rework_of': 'failure'}
    return tmp_path, state, writer, plan_path, task_path, plan, resume


def test_existing_retry_binds_full_approved_plan_and_agent_revises_quote(lineage):
    root, state, writer, path, _, plan, resume = lineage
    writer.append(ZfEvent(id='retry', type='task_map.ready', payload=resume))
    writer.append(ZfEvent(type='plan.approval.requested', payload={
        'plan_id': 'retry', 'trace_id': 'run-1', 'task_map_ref': resume['task_map_ref']}))
    events = writer.event_log.read_all()
    preview = build_plan_preview(state, events, plan_id='retry', project_root=root)
    assert preview['content_kind'] == 'full_plan'
    assert preview['markdown'] == plan
    assert next(a for a in preview['artifacts'] if a['name'] == 'accepted_plan')['ref'] == 'artifacts/full.md'
    service = PlanArtifactReview(state, root, writer)
    quote = '用户可以开始游戏。'
    result = service.discuss({'plan_id': 'retry', 'name': 'accepted_plan', 'mode': 'chat',
        'message': '加入暂停功能', 'quote': {'text': quote, 'start': plan.index(quote), 'end': plan.index(quote)+len(quote)}},
        agent=Agent({'answer': '仅修改引用内容。', 'replacement': '用户可以开始游戏并暂停。'}))
    candidate = hydrate_sidecar_ref(state, result['draft_ref']).payload
    assert candidate['body'] == plan.replace(quote, '用户可以开始游戏并暂停。')
    assert path.read_text() == plan
    assert service.apply(result['draft_ref'])['ok']
    assert path.read_text() == plan
    assert not any(e.type == 'plan.approved' and e.payload.get('plan_id') == 'retry' for e in writer.event_log.read_all())


@pytest.mark.parametrize('field', ['workflow_run_id', 'task_map_ref', 'task_map_generation',
    'plan_artifact_package_digest', 'requirement_spec_digest', 'requirement_spec_ref', 'upstream_fanout_id', 'rework_of', 'resume_scope'])
def test_unmatched_retry_does_not_inherit_prior_plan(lineage, field):
    root, state, writer, _, _, _, resume = lineage
    assert resumed_plan_refs(resume, writer.event_log.read_all(), state_dir=state, project_root=root)
    assert not resumed_plan_refs({**resume, field: 'different'}, writer.event_log.read_all(), state_dir=state, project_root=root)


@pytest.mark.parametrize('which', ['plan', 'task_map', 'approval'])
def test_changed_body_or_rejected_source_does_not_inherit(lineage, which):
    root, state, writer, plan, task_map, _, resume = lineage
    if which == 'plan': plan.write_text('改动后的正文')
    if which == 'task_map': task_map.write_text('{"tasks": ["different"]}')
    if which == 'approval': writer.append(ZfEvent(type='plan.rejected', payload={'plan_id': 'old'}))
    assert not resumed_plan_refs(resume, writer.event_log.read_all(), state_dir=state, project_root=root)


def test_preview_does_not_use_mismatched_approval(lineage):
    root, state, writer, _, _, _, resume = lineage
    writer.append(ZfEvent(id='retry', type='task_map.ready', payload=resume))
    writer.append(ZfEvent(type='plan.approval.requested', payload={
        'plan_id': 'retry', 'trace_id': 'run-1', 'task_map_ref': resume['task_map_ref'],
        'plan_artifact_package_digest': 'different-package'}))
    preview = build_plan_preview(state, writer.event_log.read_all(), plan_id='retry', project_root=root)
    assert preview['content_kind'] != 'full_plan'
    assert not any(a['name'] == 'accepted_plan' for a in preview['artifacts'])


def test_preview_cannot_retroactively_bind_a_later_approval(lineage):
    root, state, writer, _, _, _, resume = lineage
    events = [e for e in writer.event_log.read_all() if e.id != 'approved']
    events += [ZfEvent(id='retry', type='task_map.ready', payload=resume),
               ZfEvent(type='plan.approval.requested', payload={
                   'plan_id': 'retry', 'trace_id': 'run-1', 'task_map_ref': resume['task_map_ref']}),
               ZfEvent(type='plan.approved', payload={'plan_id': 'old'})]
    preview = build_plan_preview(state, events, plan_id='retry', project_root=root)
    assert preview['content_kind'] != 'full_plan'
    assert not any(a['name'] == 'accepted_plan' for a in preview['artifacts'])


def test_mapping_events_follow_same_bound_lineage(lineage):
    root, state, writer, _, _, _, resume = lineage
    events = [asdict(e) for e in writer.event_log.read_all()]
    assert resumed_plan_refs(resume, events, state_dir=state, project_root=root)['plan_artifact_ref'] == 'artifacts/full.md'


def test_new_resume_event_carries_full_plan_binding(lineage):
    root, state, writer, _, _, _, _ = lineage
    checkpoint = WorkflowBatchResumeCheckpoint(checkpoint_id='checkpoint', source_event_id='failure',
        source_event_type='integration.failed', blocking_event_id='failure', safe_resume_action='repair_failed_children',
        pdd_id='P1', trace_id='run-1', fanout_id='writer-1', task_map_ref='artifacts/task-map.json',
        source_commit='abc123', candidate_base_commit='abc123', failed_children=['child-1'])
    result = _apply_batch_task_map_ready(writer, checkpoint, [], reason='retry', task_ids=['T1'],
        resume_scope='failed_children_only', state_dir=state, project_root=root)
    assert result.applied
    ready = [e for e in writer.event_log.read_all() if e.type == 'task_map.ready'][-1]
    assert ready.payload['plan_artifact_ref'] == 'artifacts/full.md'
    assert ready.payload['plan_artifact_digest'] == hashlib.sha256((state / 'artifacts/full.md').read_bytes()).hexdigest()
