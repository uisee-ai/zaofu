"""A mechanical dependency replacement is not a new semantic task contract."""
from copy import deepcopy

import pytest

from zf.core.events import EventLog, ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.gap_plan_baseline import gap_plan_amend_baseline
from zf.runtime.plan_artifact_package import package_event_payload, write_plan_artifact_package
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from tests.test_gap_plan_baseline import _two_packages, _task_delta


def _case(tmp_path, fault=""):
    args, _, old, new = _two_packages(tmp_path)
    state = args['state_dir']
    def map_port(package):
        return next(p for group in ('produced', 'inherited') for p in package[group] if p['logical_name'] == 'task_map')
    before = deepcopy(hydrate_sidecar_ref(state, map_port(old)).payload)
    before['tasks'][0].update(blocked_by=['DEP'], wave=2)
    dep = {'task_id': 'DEP', 'title': 'Dependency', 'wave': 1, 'blocked_by': [],
           'allowed_paths': ['dependency/'], 'acceptance': ['Dependency works'],
           'acceptance_criteria': ['Dependency works'], 'verification': ['pytest']}
    before['tasks'].append(dep)
    after = deepcopy(before)
    after['tasks'][0].update(blocked_by=['DEP-A', 'DEP-B'], wave=3)
    a = {**deepcopy(dep), 'task_id': 'DEP-A', 'title': 'Dependency contract', 'allowed_paths': ['dependency/a/'], 'base_commit': 'a' * 40,
         'source_refs': ['git:' + 'a' * 40],
         'payload': {'supersedes_task_ids': ['DEP']}, 'evidence_contract': {'supersedes_task_ids': ['DEP']}}
    b = {**deepcopy(dep), 'task_id': 'DEP-B', 'title': 'Dependency implementation', 'allowed_paths': ['dependency/b/'], 'base_commit': 'a' * 40,
         'source_refs': ['git:' + 'a' * 40],
         'blocked_by': ['DEP-A'], 'wave': 2,
         'payload': {'supersedes_task_ids': ['DEP']}, 'evidence_contract': {'supersedes_task_ids': ['DEP']}}
    after['tasks'] = [after['tasks'][0], a, b]
    after['amend'] = {'kind': 'goal_gap', 'gap_task_ids': ['DEP-A', 'DEP-B'], 'superseded_task_ids': ['DEP']}
    if fault == 'drop': after['tasks'][0]['blocked_by'] = ['DEP-A']
    elif fault == 'add': after['tasks'][0]['blocked_by'].append('UNRELATED')
    elif fault == 'wave': after['tasks'][0]['wave'] = 99
    elif fault == 'semantic': after['tasks'][0]['acceptance'].append('new behavior')
    elif fault == 'no_amend': after.pop('amend')
    elif fault == 'wrong_predecessor': b['payload']['supersedes_task_ids'] = ['OTHER']
    elif fault == 'conflicting_proof': b['evidence_contract']['supersedes_task_ids'] = ['OTHER']
    elif fault == 'retained_predecessor': after['tasks'].append(dep)
    events = []
    for package, body in [(old, before), (new, after)]:
        desc = write_immutable_json_sidecar(state, body, root='fixtures', kind='task_map', schema_version='v1', created_by='test')
        map_port(package).update(ref=desc['ref'], sha256=desc['sha256'])
        if events:
            prev = events[-1].payload
            package.update(supersedes_package_ref=prev['package_ref'], supersedes_package_digest=prev['package_digest'])
            for port in package['inherited']:
                port.update(source_package_ref=prev['package_ref'], source_package_digest=prev['package_digest'])
        desc = write_plan_artifact_package(state, package)
        events.append(ZfEvent(type='plan.artifact_package.admitted', correlation_id='run-1',
                              payload=package_event_payload(package, desc, status='admitted')))
    return args, events, new


def test_old_task_recovery_keeps_mechanically_replaced_dependencies(tmp_path):
    args, events, _ = _case(tmp_path)
    ref, generation = gap_plan_amend_baseline(state_dir=args['state_dir'], events=events,
        payload={'workflow_run_id': 'run-1', 'task_map_generation': 'g1'},
        fallback_ref='r1.json', required=True, gap_tasks=[_task_delta()])
    assert generation == 'g2'
    assert ref != 'r1.json'


@pytest.mark.parametrize('fault', ['drop', 'add', 'wave', 'semantic', 'no_amend',
                                  'wrong_predecessor', 'conflicting_proof', 'retained_predecessor'])
def test_unproven_dependency_drift_still_rejects(tmp_path, fault):
    args, events, _ = _case(tmp_path, fault)
    with pytest.raises(ValueError):
        gap_plan_amend_baseline(state_dir=args['state_dir'], events=events,
            payload={'workflow_run_id': 'run-1', 'task_map_generation': 'g1'},
            fallback_ref='r1.json', required=True, gap_tasks=[_task_delta()])


@pytest.mark.parametrize('intermediate_edit', [False, True])
def test_intermediate_contract_change_cannot_hide_behind_restored_latest_map(tmp_path, intermediate_edit):
    from zf.runtime.gap_dependency_continuity import task_has_only_replayed_dependency_changes
    from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package

    args, events, _ = _case(tmp_path)
    maps = []
    for event in events:
        package = hydrate_plan_artifact_package(args['state_dir'], {
            'ref': event.payload['package_ref'], 'sha256': event.payload['package_digest']})
        port = next(p for p in package['produced'] if p['logical_name'] == 'task_map')
        maps.append(hydrate_sidecar_ref(args['state_dir'], port).payload)
    intermediate = deepcopy(maps[-1])
    if intermediate_edit:
        intermediate['tasks'][0]['acceptance'].append('Temporary semantic drift')
    assert task_has_only_replayed_dependency_changes(
        'T1', [maps[0], intermediate, maps[-1]]) is not intermediate_edit


def test_real_gap_bridge_retains_dependency_successors(tmp_path, monkeypatch):
    from tests.test_module_gap_plan_runtime import _config, _RecordingTransport
    from zf.runtime.orchestrator import Orchestrator
    import json
    args, events, _ = _case(tmp_path)
    state = args['state_dir']; log = EventLog(state / 'events.jsonl')
    for event in events: log.append(event)
    orch = Orchestrator(state, _config(state), _RecordingTransport()); ready = []
    monkeypatch.setattr(orch, '_maybe_start_writer_fanout', ready.append)
    event = ZfEvent(type='flow.gap_plan.ready', correlation_id='run-1', payload={
        'workflow_run_id': 'run-1', 'pdd_id': 'GOAL-1', 'flow_kind': 'prd',
        'task_map_generation': 'g1', 'task_map_ref': 'r1.json', 'gap_tasks': [_task_delta()]})
    result = orch._bridge_gap_plan_ready_to_task_map(event)
    assert result.action == 'bridge', result
    body = json.loads((state / ready[0].payload['task_map_ref']).read_text())
    successor = next(t for t in body['tasks'] if t['task_id'] == 'T1-FIX')
    assert successor['blocked_by'] == ['DEP-A', 'DEP-B']
    assert {t['task_id'] for t in body['tasks']} == {'T1-FIX', 'DEP-A', 'DEP-B'}
