from copy import deepcopy
import pytest

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskExecutionBinding
from zf.runtime.sdlc_graph import build_sdlc_graph
from zf.runtime.sdlc_graph_timeline import build_timeline
from zf.web.projections.workflow_workbench_sources import scope_events
from tests.test_workflow_preview_review import setup  # noqa: F401


def sources(**changes):
    value = {"scope": {"request_id": "r", "run_id": "run-a", "revision": 1,
                       "historical": False, "source_seq": 0},
             "request": {"confirmed": True}, "requirement": {"objective": "Build a product"},
             "compiled": {"nodes": [{"node_id": "stage:build", "stage_id": "build", "label": "Build"}], "edges": []},
             "events": [], "tasks": {}, "plans": [], "versions": [], "diagnostics": []}
    return {**value, **changes}


def test_scope_excludes_foreign_run_even_with_reused_task_and_causation():
    tasks = [Task(id="T1", title="A", execution_binding=TaskExecutionBinding(workflow_run_id="run-a")),
             Task(id="T2", title="B", execution_binding=TaskExecutionBinding(workflow_run_id="run-b"))]
    first = ZfEvent(id="e1", type="run.started", payload={"workflow_run_id": "run-a"})
    child = ZfEvent(id="e2", type="fanout.started", causation_id="e1")
    events = [first, child, ZfEvent(id="e3", type="verify.passed", task_id="T2", causation_id="e1"),
              ZfEvent(id="e4", type="verify.passed", task_id="T1", payload={"workflow_run_id": "run-b"}),
              ZfEvent(id="e5", type="unrelated", payload={"request_id": "other"}, causation_id="e2"),
              ZfEvent(id="e6", type="task.dispatched", task_id="T1")]
    assert [e.id for _, e in scope_events(events, request_id="r", run_id="run-a", tasks=tasks)] == ["e1", "e2", "e6"]


def test_later_task_rebinding_does_not_erase_explicit_historical_run_events():
    task = Task(id="T1", title="Reused", execution_binding=TaskExecutionBinding(workflow_run_id="run-b"))
    events = [ZfEvent(id="old", type="task.dispatched", task_id="T1", payload={"workflow_run_id": "run-a"}),
              ZfEvent(id="receipt", type="verify.passed", task_id="T1", correlation_id="run-a"),
              ZfEvent(id="new", type="task.dispatched", task_id="T1", payload={"workflow_run_id": "run-b"}),
              ZfEvent(id="ambiguous", type="verify.passed", task_id="T1", causation_id="old")]
    assert [e.id for _, e in scope_events(events, request_id="r", run_id="run-a", tasks=[task])] == ["old", "receipt"]


def test_legacy_candidate_chain_uses_exact_writer_receipts_not_shared_feature_name():
    events = [ZfEvent(id='writer-a', type='dev.build.done', payload={'workflow_run_id': 'run-a'}),
              ZfEvent(id='writer-b', type='dev.build.done', payload={'workflow_run_id': 'run-b'}),
              ZfEvent(id='start-a', type='candidate.integration.started', payload={'pdd_id': 'same'}),
              ZfEvent(id='result-a', type='candidate.quality.failed', causation_id='start-a',
                      payload={'pdd_id': 'same', 'requested_tasks': [{'approval_event_id': 'writer-a'}]}),
              ZfEvent(id='end-a', type='candidate.integration.completed', causation_id='result-a'),
              ZfEvent(id='foreign', type='candidate.quality.failed', causation_id='start-a',
                      payload={'workflow_run_id': 'run-b', 'requested_tasks': [{'approval_event_id': 'writer-a'}]}),
              ZfEvent(id='result-b', type='candidate.quality.failed', payload={'pdd_id': 'same',
                      'requested_tasks': [{'approval_event_id': 'writer-b'}]}),
              ZfEvent(id='mixed', type='candidate.updated', causation_id='start-a', payload={'pdd_id': 'same',
                      'requested_tasks': [{'approval_event_id': 'writer-a'}, {'approval_event_id': 'writer-b'}]})]
    assert [e.id for _, e in scope_events(events, request_id='r', run_id='run-a', tasks=[])] == [
        'writer-a', 'start-a', 'result-a', 'end-a']


def test_shared_task_has_one_identity_and_cross_module_dependency():
    task_map = {"tasks": [{"task_id": "T1", "title": "Shared"}, {"task_id": "T2", "blocked_by": ["T1"]}],
                "modules": [{"module_id": "M1", "title": "Feature A", "task_ids": ["T1"]},
                            {"module_id": "M2", "title": "Feature B", "task_ids": ["T1", "T2"]}]}
    before = deepcopy(task_map)
    graph = build_sdlc_graph(sources(versions=[{"body": task_map}]))
    assert len([n for n in graph["nodes"] if n["id"] == "task:T1"]) == 1
    assert len([e for e in graph["edges"] if e["target"] == "task:T1" and e["relation"] == "contains"]) == 2
    assert any(e["source"] == "task:T1" and e["target"] == "task:T2" and e["relation"] == "depends_on" for e in graph["edges"])
    assert task_map == before


def test_worker_claim_is_not_success_and_production_stays_unknown():
    task = Task(id="T1", title="Task", status="done")
    event = ZfEvent(type="judge.passed", task_id="T1", origin="worker")
    graph = build_sdlc_graph(sources(tasks={task.id: task}, events=[(1, event)]))
    assert next(n for n in graph["nodes"] if n["id"] == "task:T1")["status"] == "evidence_missing"
    assert next(n for n in graph["nodes"] if n["kind"] == "evidence")["status"] == "reported"
    assert graph["capabilities"]["production"]["status"] == "unconnected"
    assert graph["capabilities"]["release"]["status"] == "unknown"


def test_operations_keep_stage_placement_attempts_and_rework_evidence():
    events = [ZfEvent(id="o1", type="workflow.operation.requested", payload={"workflow_run_id": "run-a", "operation_id": "O1", "parent_stage_id": "build"}),
              ZfEvent(id="o2", type="workflow.operation.started", payload={"workflow_run_id": "run-a", "operation_id": "O1", "role_instance": "worker-1", "active_attempt_id": "A1"}),
              ZfEvent(id="o3", type="workflow.operation.failed", payload={"workflow_run_id": "run-a", "operation_id": "O1", "reason": "transport_timeout"}, origin="kernel"),
              ZfEvent(id="o4", type="task.rework.requested", causation_id="o3", origin="kernel"),
              ZfEvent(id="o5", type="codex.hook.pre_tool_use", causation_id="o2", payload={"tool_name": "exec_command"}),
              ZfEvent(id="o6", type="agent.usage", causation_id="o2", payload={"backend": "codex", "model": "provider-selected-model"})]
    graph = build_sdlc_graph(sources(events=list(enumerate(events, 1))))
    assert next(n for n in graph["nodes"] if n["id"] == "workflow:stage:build")["status"] == "failed"
    assert any(e["source"] == "workflow:stage:build" and e["target"] == "operation:O1" for e in graph["edges"])
    assert any(n["id"] == "attempt:A1" for n in graph["nodes"])
    operation = next(n for n in graph["nodes"] if n["id"] == "operation:O1")
    assert any(e["id"] == "o5" for e in operation["data"]["recent_activity"])
    assert operation['data']['agent_binding']['model'] == 'provider-selected-model'
    assert operation['data']['agent_binding']['source_event_id'] == 'o6'
    assert any(e["source"] == "event:o3" and e["target"] == "event:o4" for e in graph["edges"])


def test_dag_critical_path_does_not_sum_parallel_tasks():
    ops = [{"operation_id": tid, "task_id": tid, "status": "settled", "started_at": "2026-01-01T00:00:00Z", "finished_at": f"2026-01-01T00:00:{seconds:02d}Z"} for tid, seconds in [("A", 5), ("B", 8), ("C", 2)]]
    nodes = [{"id": "task:" + tid, "kind": "task"} for tid in ("A", "B", "C")]
    edges = [{"source": "task:" + tid, "target": "task:C", "relation": "depends_on"} for tid in ("A", "B")]
    result = build_timeline(ops, nodes, edges)["critical_path"]
    assert result["duration_ms"] == 10_000
    assert result["node_ids"] == ["task:B", "task:C"]
    assert build_timeline(ops[:-1], nodes, edges)["critical_path"]["status"] == "incomplete"
    assert build_timeline(ops, nodes, [*edges, {"source": "task:C", "target": "task:A", "relation": "depends_on"}])["critical_path"]["status"] == "cycle"


def test_workbench_api_uses_proposal_snapshot_and_is_read_only(setup):
    client, service, binding = setup
    url = f"/api/projects/default/workflow-requests/{binding['request_id']}/graph"
    before = service.writer.event_log.read_all()
    first = client.get(url)
    assert first.status_code == 200, first.text
    graph = first.json()
    structure = [n for n in graph["nodes"] if n["kind"] == "workflow"]
    assert structure and graph["scope"]["proposal_digest"] == binding["proposal_digest"]
    service.config_path.write_text(service.config_path.read_text().replace("lanes: 1", "lanes: 2"))
    assert [n for n in client.get(url).json()["nodes"] if n["kind"] == "workflow"] == structure
    assert len(service.writer.event_log.read_all()) == len(before)
    assert client.get(url + "?as_of=1").json()["scope"]["historical"]
    assert client.get(url.replace(binding["request_id"], "missing")).status_code == 404


def test_workbench_keeps_pending_plan_when_source_index_omits_authority_refs(setup):
    import hashlib
    import json
    from zf.runtime.workflow_requests import mark_workflow_request

    client, service, binding = setup
    for status in ["approved", "submitted"]:
        mark_workflow_request(service.state_dir, binding["request_id"],
                              status=status, actor="test", run_id="run-a")
    artifacts = service.state_dir / "artifacts"
    artifacts.mkdir(exist_ok=True)
    full = "# Racing plan\n\nRequires explicit owner approval.\n"
    (artifacts / "plan.md").write_text(full)
    (artifacts / "source_index.json").write_text(json.dumps({"tasks": []}))
    service.writer.append(ZfEvent(type="plan.approval.requested", payload={
        "plan_id": "review-plan", "workflow_run_id": "run-a",
        "plan_artifact_ref": "artifacts/plan.md",
        "plan_artifact_digest": hashlib.sha256(full.encode()).hexdigest(),
        "source_index_ref": "artifacts/source_index.json",
    }))
    before = service.writer.event_log.read_all()

    response = client.get(f"/api/projects/default/workflow-requests/{binding['request_id']}/graph")

    assert response.status_code == 200, response.text
    preview = response.json()["plans"][0]
    assert preview["plan_id"] == "review-plan"
    assert preview["status"] == "pending"
    assert preview["markdown"] == full
    assert preview["authority_refs"] == []
    assert len(service.writer.event_log.read_all()) == len(before)


def test_new_candidate_and_generation_cannot_reuse_old_task_success():
    task = Task(id='T1', title='Old', status='done')
    events = [(1, ZfEvent(type='task.done.accepted', task_id='T1', origin='kernel')),
              (2, ZfEvent(type='task.pipeline.generation.admitted', origin='kernel'))]
    graph = build_sdlc_graph(sources(tasks={task.id: task}, events=events))
    assert next(n for n in graph['nodes'] if n['id'] == 'task:T1')['status'] == 'evidence_missing'
    graph = build_sdlc_graph(sources(tasks={task.id: task}, events=events[:1], versions=[{
        'body': {'tasks': [{'task_id': 'T1', 'title': 'New draft'}]}, 'admitted': False, 'seq': 3}]))
    node = next(n for n in graph['nodes'] if n['id'] == 'task:T1')
    assert node['status'] == 'draft' and node['data']['actual']['title'] == 'Old'


@pytest.mark.parametrize('origin,generation,run_id,completed,expected', [
    ('kernel', 'GEN-2', 'run-a', ['T1'], 'done'),
    ('worker', 'GEN-2', 'run-a', ['T1'], 'evidence_missing'),
    ('kernel', 'GEN-1', 'run-a', ['T1'], 'evidence_missing'),
    ('kernel', '', 'run-a', ['T1'], 'evidence_missing'),
    ('kernel', 'GEN-2', 'run-b', ['T1'], 'evidence_missing'),
    ('kernel', 'GEN-2', 'run-a', ['T2'], 'evidence_missing'),
])
def test_task_completion_accepts_exact_current_kernel_run_settlement(origin, generation, run_id, completed, expected):
    task = Task(id='T1', title='Task', status='done')
    terminal = ZfEvent(id='terminal', type='run.goal.completed', origin=origin, payload={
        'workflow_run_id': run_id, 'task_map_generation': generation, 'completed_task_ids': completed})
    graph = build_sdlc_graph(sources(tasks={'T1': task}, events=[(1, terminal)],
                                   versions=[{'body': {}, 'task_map_generation': 'GEN-2'}]))
    node = next(n for n in graph['nodes'] if n['id'] == 'task:T1')
    assert node['status'] == expected
    assert node['data']['accepted_event_ids'] == (['terminal'] if expected == 'done' else [])


def test_workbench_uses_latest_kernel_goal_pin_for_the_current_run_generation(setup):
    import hashlib
    import json
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    from zf.runtime.goal_claim_set import build_goal_claim_set
    from zf.runtime.workflow_requests import mark_workflow_request
    client, service, binding = setup
    for status in ['approved', 'submitted']:
        mark_workflow_request(service.state_dir, binding['request_id'], status=status, actor='test', run_id='run-a')
    body = {'feature_id': 'planner-name', 'tasks': [], 'goal_claims': [{'id': 'G1', 'text': 'Planned'}]}
    path = service.state_dir / 'artifacts/graph-map.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(body))
    service.writer.append(ZfEvent(type='task_map.admitted', origin='kernel', payload={
        'workflow_run_id': 'run-a', 'task_map_ref': 'artifacts/graph-map.json',
        'task_map_digest': hashlib.sha256(path.read_bytes()).hexdigest(),
        'goal_id': 'preparation-goal', 'task_map_generation': 'GEN-2'}))
    for goal, run, generation, origin in [
        ('preparation-goal', 'run-a', 'GEN-2', 'kernel'),
        ('runtime-goal', 'run-a', 'GEN-2', 'kernel'),
        ('foreign-goal', 'run-b', 'GEN-2', 'kernel'),
        ('old-generation', 'run-a', 'GEN-1', 'kernel'),
        ('worker-claim', 'run-a', 'GEN-2', 'worker'),
    ]:
        claim_set = build_goal_claim_set({**body, 'goal_claims': [*body['goal_claims'],
            {'id': 'ORIGINAL', 'text': 'Original requirement'}]}, workflow_run_id=run,
            goal_id=goal, task_map_generation=generation)
        ref = write_immutable_json_sidecar(service.state_dir, claim_set, root='goal-closure/claim-sets',
            kind='goal_claim_set', schema_version='goal-claim-set.v1', created_by='test')
        service.writer.append(ZfEvent(type='goal.claim_set.pinned', origin=origin, payload={
            'workflow_run_id': run, 'goal_id': goal, 'task_map_generation': generation,
            'goal_claim_set_ref': ref['ref'], 'goal_claim_set_digest': ref['sha256']}))
    response = client.get(f"/api/projects/default/workflow-requests/{binding['request_id']}/graph")
    assert response.status_code == 200, response.text
    coverage = response.json()['goal_coverage']
    assert coverage['identity']['goal_id'] == 'runtime-goal'
    assert coverage['summary']['mandatory_claims'] == 2


def test_uncovered_claims_and_queue_wait_are_explicit():
    graph = build_sdlc_graph(sources(versions=[{'body': {'goal_claims': [
        {'goal_claim_id': 'R1', 'text': 'Billing'}, {'goal_claim_id': 'R2', 'text': 'Accessibility'}],
        'tasks': [{'task_id': 'T1', 'goal_claim_ids': ['R1']}]}}]))
    assert {n['id']: n['status'] for n in graph['nodes'] if n['kind'] == 'claim'} == {
        'claim:R1': 'mapped', 'claim:R2': 'uncovered'}
    timeline = build_timeline([{'operation_id': 'op', 'status': 'running',
        'requested_at': '2026-01-01T00:00:01Z', 'started_at': '2026-01-01T00:00:05Z'}], [], [])
    assert timeline['intervals'][0]['queue_wait_ms'] == 4000
    assert timeline['intervals'][0]['duration_ms'] is None


def test_fanout_execution_is_projected_into_compiled_stage():
    events = [ZfEvent(id='f1', type='fanout.started', payload={
        'workflow_run_id': 'run-a', 'fanout_id': 'fan', 'stage_id': 'build:revision',
        'expected_children': [{'child_id': 'child', 'role': 'scanner', 'role_instance': 'scanner-1'}]}),
        ZfEvent(id='f2', type='fanout.child.dispatched', payload={'fanout_id': 'fan', 'child_id': 'child'})]
    scoped = scope_events(events, request_id='r', run_id='run-a', tasks=[])
    assert len(scoped) == 2
    graph = build_sdlc_graph(sources(events=scoped))
    assert any(e['source'] == 'workflow:stage:build' and e['target'] == 'fanout:fan' for e in graph['edges'])
    assert next(n for n in graph['nodes'] if n['id'] == 'fanout:fan:child:child')['status'] == 'running'


def test_delivery_graph_reuses_currentness_checks_for_old_evidence():
    from tests.test_goal_coverage_graph import _task_map, _tasks, _verification_result, _admission_event
    event = ZfEvent(id='old', type='verify.passed', task_id='TASK-A', origin='worker', payload={
        'verification_result': _verification_result(task_map_generation='GEN-1', target_commit='old123')})
    source = sources(tasks=_tasks(), versions=[{'body': _task_map()}],
                     events=[(1, event), (2, _admission_event('old'))])
    source['scope']['run_id'] = 'RUN-1'
    graph = build_sdlc_graph(source)
    claim = next(n for n in graph['nodes'] if n['id'] == 'claim:CLAIM-A')
    assert claim['status'] == 'stale'
    assert 'delivery' in claim['views']
    assert graph['goal_coverage']['summary']['claims_with_current_results'] == 0
    result = next(n for n in graph['nodes'] if n['kind'] == 'verification_result')
    assert result['data']['coverage']['current'] is False
    assert any(e['source'] == result['id'] and e['target'] == 'task:TASK-A' for e in graph['edges'])


def test_activity_excerpts_do_not_copy_complete_provider_transcripts_into_graph():
    from zf.runtime.sdlc_graph import activity_event
    event = ZfEvent(type='codex.hook.post_tool_use', payload={'tool_response': 'x' * 500_000})
    excerpt = activity_event(event)
    assert excerpt['excerpted'] and len(excerpt['payload']['tool_response']) == 480
    graph = build_sdlc_graph(sources(events=[(1, event)]))
    assert 'payload' not in graph['timeline'][0]
    assert len(event.payload['tool_response']) == 500_000


@pytest.mark.parametrize('admission', ['task.pipeline.generation.admitted', 'task_map.materialization.committed', 'task_map.admitted'])
def test_ready_event_cannot_drop_a_previously_pinned_plan_digest(setup, admission):
    import hashlib
    import json
    client, service, binding = setup
    path = service.state_dir / 'artifacts' / 'graph-map.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({'feature_id': 'planner-feature', 'tasks': [{'task_id': 'T1'}],
                               'goal_claims': [{'id': 'G1', 'text': 'Product requirement'}]}))
    common = {'request_id': binding['request_id'], 'task_map_ref': 'artifacts/graph-map.json'}
    service.writer.append(ZfEvent(type='plan.approval.requested', payload={**common,
        'plan_id': 'P1', 'task_map_digest': hashlib.sha256(path.read_bytes()).hexdigest(),
        'task_map_generation': 'GEN-1', 'goal_id': 'runtime-goal'}))
    service.writer.append(ZfEvent(type='task_map.ready', payload=common))
    service.writer.append(ZfEvent(type=admission, payload=common))
    response = client.get(f"/api/projects/default/workflow-requests/{binding['request_id']}/graph")
    assert response.status_code == 200, response.text
    versions = response.json()['versions']
    assert len(versions) == 2
    assert versions[-1]['admitted']
    assert versions[-1]['digest'] == versions[0]['digest']
    assert versions[-1]['task_map_generation'] == 'GEN-1'
    assert response.json()['goal_coverage']['identity']['goal_id'] == 'runtime-goal'


@pytest.mark.parametrize('latest_status', ['settled', 'cancelled', 'superseded'])
def test_rework_updates_stage_without_erasing_failed_operation(latest_status):
    events = []
    for oid, terminal in [('first', 'failed'), ('retry', latest_status)]:
        payload = {'workflow_run_id': 'run-a', 'parent_stage_id': 'build',
                   'operation_id': oid, 'role_instance': 'worker'}
        events.extend([ZfEvent(type='workflow.operation.requested', payload=payload),
                       ZfEvent(type='workflow.operation.' + terminal, payload=payload, origin='kernel')])
    graph = build_sdlc_graph(sources(events=list(enumerate(events, 1))))
    states = {n['id']: n['status'] for n in graph['nodes']}
    assert states['workflow:stage:build'] == latest_status
    assert states['operation:first'] == 'failed'
    assert states['operation:retry'] == latest_status


@pytest.mark.parametrize(('result', 'quality', 'expected'), [
    ('quality_failed', 'failed', 'failed'), ('conflict', '', 'failed'),
    ('stale', '', 'stale'), ('updated', 'passed', 'accepted'), ('', '', 'unknown'),
])
def test_candidate_completed_uses_result_verdict_not_event_suffix(result, quality, expected):
    event = ZfEvent(id='candidate-result', type='candidate.integration.completed', origin='kernel',
                    payload={'status': result, 'quality_status': quality})
    graph = build_sdlc_graph(sources(events=[(1, event)]))
    assert next(n for n in graph['nodes'] if n['id'] == 'event:candidate-result')['status'] == expected
    assert graph['capabilities']['release']['status'] == 'unknown'


@pytest.mark.parametrize('latest_status', ['failed', 'completed', 'cancelled'])
def test_stage_uses_latest_fanout_verdict_even_when_child_operation_settled(latest_status):
    events = [
        ZfEvent(id='old', type='fanout.started', payload={'fanout_id': 'z-old', 'stage_id': 'build'}),
        ZfEvent(id='old-end', type='fanout.aggregate.completed', payload={'fanout_id': 'z-old', 'status': 'completed'}),
        ZfEvent(id='new', type='fanout.started', payload={'fanout_id': 'a-new', 'stage_id': 'build'}),
        ZfEvent(id='op', type='workflow.operation.requested', payload={'workflow_run_id': 'run-a', 'operation_id': 'O', 'parent_stage_id': 'build'}),
        ZfEvent(id='op-end', type='workflow.operation.settled', payload={'workflow_run_id': 'run-a', 'operation_id': 'O'}),
        ZfEvent(id='new-end', type='fanout.aggregate.completed', payload={'fanout_id': 'a-new', 'status': latest_status}),
    ]
    graph = build_sdlc_graph(sources(events=list(enumerate(events, 1))))
    states = {n['id']: n['status'] for n in graph['nodes']}
    assert states['workflow:stage:build'] == latest_status
    assert states['fanout:a-new'] == latest_status
    assert states['fanout:z-old'] == 'completed'


@pytest.mark.parametrize('event_type,status', [
    ('run.goal.completed', 'accepted'), ('run.completed', 'accepted'),
    ('run.goal.blocked', 'blocked'), ('run.failed', 'failed'),
    ('run.cancelled', 'cancelled'), ('run.abandoned', 'abandoned'),
])
def test_terminal_evidence_preserves_non_success_verdict(event_type, status):
    event = ZfEvent(id='terminal', type=event_type, origin='kernel')
    graph = build_sdlc_graph(sources(events=[(1, event)]))
    assert next(n for n in graph['nodes'] if n['id'] == 'event:terminal')['status'] == status


def test_run_terminal_and_release_never_imply_production_health():
    for event in [ZfEvent(type='candidate.integration.completed', origin='kernel'),
                  ZfEvent(type='ship.completed', origin='worker')]:
        assert build_sdlc_graph(sources(events=[(1, event)]))['capabilities']['release']['status'] == 'unknown'
    events = [ZfEvent(type='ship.completed', origin='kernel', payload={'commit': 'abc123'}),
              ZfEvent(type='provider.health.changed', payload={'status': 'healthy'}),
              ZfEvent(id='terminal', type='run.goal.completed', origin='kernel')]
    source = sources(events=list(enumerate(events, 1)))
    source['compiled']['nodes'].append({'node_id': 'terminal:done', 'label': 'Done'})
    graph = build_sdlc_graph(source)
    assert next(n for n in graph['nodes'] if n['id'] == 'workflow:terminal:done')['status'] == 'done'
    assert graph['capabilities']['release']['status'] == 'observed'
    assert graph['capabilities']['production']['status'] == 'unconnected'
    assert any(n['id'] == 'commit:abc123' for n in graph['nodes'])
    events[-1].origin = 'worker'
    assert next(n for n in build_sdlc_graph(source)['nodes'] if n['id'] == 'workflow:terminal:done')['status'] == 'pending'


def test_runtime_fanout_anchor_is_not_counted_as_product_work():
    from zf.core.task.schema import TaskContract
    anchor = Task(id='ANCHOR', title='Runtime anchor', contract=TaskContract(
        evidence_contract={'workflow_fanout_anchor': True}))
    graph = build_sdlc_graph(sources(tasks={anchor.id: anchor}))
    node = next(n for n in graph['nodes'] if n['id'] == 'task:ANCHOR')
    assert node['kind'] == 'run_anchor'
    assert 'modules' not in node['views'] and 'lifecycle' not in node['views']
