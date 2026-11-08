"""A dependency repair must not inherit its failed consumer's partial code."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from tests.test_task_partial_successor import scenario, git
from tests.test_task_pipeline_generation import _runtime, _PACKAGE_ID, _PACKAGE_REF, _PACKAGE_DIGEST
from tests.test_plan_candidate_preflight import _task_map, _source_index
from zf.core.events import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.task_pipeline_runtime import admit_task_pipeline_generation, TaskPipelineRuntimeError
from zf.runtime.task_pipeline_terminal import task_pipeline_workspace_base
from zf.runtime.writer_fanout_admission import load_writer_task_map


def pair(candidate, partial):
    task_map = _task_map()
    first = task_map['tasks'][0]
    first.update(base_commit=candidate, source_refs=['git:' + candidate])
    second = deepcopy(first)
    second.update(task_id='NEXT', title='Resume consumer', goal_claim_ids=[],
                  allowed_paths=['product.txt', 'gap.txt'], blocked_by=['TASK-1'],
                  base_commit=partial, source_refs=['git:' + partial],
                  supersedes_task_ids=['PART'])
    second['validation']['commands'][0].update(id='test-next', acceptance_ids=['AC-2'])
    second['acceptance_criteria'][0].update(id='AC-2', verification_command_ids=['test-next'])
    task_map['tasks'].append(second)
    task_map['target_commit'] = partial
    return task_map


@pytest.mark.parametrize('mode', ['blocking', 'shadow', 'off'])
def test_plan_preflight_allows_mixed_bases_only_for_independent_pipeline(tmp_path, mode):
    task_map = pair('a' * 40, 'b' * 40)
    index = _source_index()
    index['tasks'].append({**index['tasks'][0], 'task_id': 'NEXT'})
    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / '.state', project_root=tmp_path,
        reports=[{'report': {'task_map': task_map, 'source_index': index, 'plan_md': '# Plan'}}],
        manifest={'trigger_payload': {'flow_kind': 'issue', 'issue_ref': 'docs/issue.md'}},
        metadata={'flow_kind': 'issue', 'task_pipeline': {'mode': mode}},
    )
    conflicts = [e for e in result['errors'] if e['code'] == 'writer_fanout_base_conflict']
    assert bool(conflicts) is (mode != 'blocking')


def admitted_pair(tmp_path):
    runtime, successor, _, writer, _, candidate, partial = scenario(tmp_path)
    runtime.config = _runtime(tmp_path, base_ref=candidate).config
    runtime.event_writer = writer
    task_map = pair(candidate, partial)
    path = runtime.state_dir / 'mixed-map.json'
    path.write_text(json.dumps(task_map))
    trigger = ZfEvent(type='task_map.ready', correlation_id='run', payload={
        'task_map_ref': 'mixed-map.json', 'flow_kind': 'issue',
        'workflow_run_id': 'run', 'task_map_generation': 'mixed-map',
        'plan_artifact_package_id': _PACKAGE_ID, 'plan_artifact_package_ref': _PACKAGE_REF,
        'plan_artifact_package_digest': _PACKAGE_DIGEST,
    })
    stage = SimpleNamespace(task_map='mixed-map.json')
    args = dict(stage=stage, event=trigger, pdd_id='FEATURE', state_dir=runtime.state_dir,
                project_root=tmp_path)
    with pytest.raises(ValueError, match='conflicting base_commit'):
        load_writer_task_map(**args)
    # Exercise the real runtime loader entry; the config owns the capability.
    items = Orchestrator._load_writer_task_map(runtime, stage, trigger, 'FEATURE')
    loaded = load_writer_task_map(**args, independent_task_bases=True)
    event = admit_task_pipeline_generation(runtime, trigger_event=trigger,
        task_map_admitted_event=ZfEvent(type='task_map.admitted', payload={'task_map_digest': 'c'*64}),
        stage_id='impl', trace_id='run', loaded=loaded, task_items=items)
    assert event is not None
    return runtime, successor, candidate, partial, event


def test_admission_replay_keeps_producer_and_successor_workspaces_separate(tmp_path):
    runtime, successor, candidate, partial, event = admitted_pair(tmp_path)
    assert event.payload['task_dispatch_base_commits'] == {'TASK-1': candidate, 'NEXT': partial}
    # Replay from the canonical occurrence log, as after a controller restart.
    contexts = task_pipeline_generation_contexts(runtime.event_log.read_all())
    producer_base = task_pipeline_workspace_base(runtime, task=SimpleNamespace(id='TASK-1'),
        generation_context=contexts['TASK-1'])
    consumer_base = task_pipeline_workspace_base(runtime, task=successor,
        generation_context=contexts['NEXT'])
    assert producer_base == candidate
    assert git(tmp_path, 'show', producer_base + ':product.txt') == 'base'
    assert git(tmp_path, 'show', consumer_base + ':product.txt') == 'partial implementation'
    assert git(tmp_path, 'show', consumer_base + ':concurrent.txt') == 'other task'
    assert git(tmp_path, 'rev-parse', 'candidate/FEATURE') == candidate
    assert contexts['NEXT']['dispatch_base_commit'] == partial


@pytest.mark.parametrize('bad', [{}, {'TASK-1': 'bad'}, {'TASK-1': 'a'*40, 'NEXT': 'bad'}])
def test_malformed_compiled_bases_never_fall_back_to_shared_partial(tmp_path, bad):
    _, _, _, _, event = admitted_pair(tmp_path)
    event.payload['task_dispatch_base_commits'] = bad
    with pytest.raises(ValueError, match='invalid admitted task dispatch bases'):
        task_pipeline_generation_contexts([event])


def test_legacy_generation_keeps_shared_base(tmp_path):
    _, _, _, partial, event = admitted_pair(tmp_path)
    del event.payload['task_dispatch_base_commits']
    contexts = task_pipeline_generation_contexts([event])
    assert {c['dispatch_base_commit'] for c in contexts.values()} == {partial}


def test_unresolvable_declared_task_base_is_rejected(tmp_path):
    runtime, _, _, _, _ = admitted_pair(tmp_path)
    from zf.runtime.task_pipeline_runtime import preflight_task_pipeline_generation
    with pytest.raises(TaskPipelineRuntimeError):
        preflight_task_pipeline_generation(runtime,
            trigger_event=ZfEvent(type='task_map.ready', payload={'task_map_generation':'bad'}),
            trace_id='run', loaded=SimpleNamespace(
                workflow_run_id='run', task_map_generation='bad', dispatch_base_commit='HEAD',
                plan_artifact_package_id=_PACKAGE_ID, plan_artifact_package_ref=_PACKAGE_REF,
                plan_artifact_package_digest=_PACKAGE_DIGEST),
            task_items=[{'task_id':'MISSING','base_commit':'f'*40}])


def test_integration_bridge_consumes_each_replayed_base(tmp_path, monkeypatch):
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.runtime.task_pipeline_integration import reconcile_task_pipeline_integration

    runtime, _, candidate, partial, _ = admitted_pair(tmp_path)
    runtime.task_store = TaskStore(runtime.state_dir / 'kanban.json')
    runtime.task_store.add(Task(id='TASK-1', title='producer'))
    contexts = task_pipeline_generation_contexts(runtime.event_log.read_all())
    monkeypatch.setattr('zf.runtime.task_pipeline_integration._current_integration_source',
        lambda *a, **k: {'pipeline_key': 'p', 'operation_generation': 1})
    captured = []

    def integrate(_self, **kwargs):
        captured.append((kwargs['task_id'], kwargs['dispatch_base_commit']))
        return SimpleNamespace(status='integrated')

    monkeypatch.setattr('zf.runtime.candidates.CandidateRebuilder.integrate_task_pipeline_task', integrate)
    for tid in ['TASK-1', 'NEXT']:
        result = reconcile_task_pipeline_integration(runtime,
            projection={'queues': {'integration_ready': [tid]}},
            generation_contexts=contexts, operation_rows=[])
        assert result[0].action == 'task_pipeline_integrated'
    assert captured == [('TASK-1', candidate), ('NEXT', partial)]
