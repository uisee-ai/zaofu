"""Diagnosis failure and controller schema drift retain an actionable owner."""
import json
import subprocess

import pytest

from zf.core.state.session import SessionStore


def test_cleanup_does_not_require_business_schema_compatibility(tmp_path):
    from zf.autoresearch.runtime_cleanup import stop_experiment_runtime
    (tmp_path / 'zf.yaml').write_text('''kind: ZfConfig
apiVersion: zaofu.dev/v1
metadata: {name: child}
spec:
  project: {name: child, state_dir: owned-state}
  runtime:
    host_verification: {future_business_option: true}
''')
    SessionStore(tmp_path / 'owned-state/session.yaml').create(str(tmp_path))
    calls = []
    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)
    outcome = stop_experiment_runtime(tmp_path, runner=runner)
    assert outcome['status'] == 'stopped', outcome
    assert len(calls) == 1


@pytest.mark.parametrize('failure_class', ['diagnosis_timeout', 'missing_report', 'result_writeback_failed', 'cleanup_failed'])
def test_failed_diagnosis_is_consumed_and_sent_to_resident_once(tmp_path, monkeypatch, failure_class):
    from tests.test_run_manager_diagnosis_consumer import _setup, _tick
    state, log, writer, cfg, calls = _setup(tmp_path, monkeypatch)
    (state / 'projections/supervisor/snapshot.json').write_text('{}')
    writer.emit('run.manager.autoresearch.requested', payload={
        'request_id': 'ar-failed', 'recovery_case_id': 'original-case',
        'workflow_run_id': '', 'task_id': '', 'failure_class': 'stage_replan_cap_exhausted'})
    failure = writer.emit('autoresearch.loop.failed', payload={
        'loop_request_id': 'ar-failed', 'returncode': 124,
        'diagnosis': {'status': 'incomplete', 'failure_class': failure_class},
        'worktree_lifecycle': {'status': 'retained', 'reason': 'runtime_cleanup_failed'}})
    for _ in range(2):
        _tick(state, log, writer, cfg, tmp_path)
    events = log.read_all()
    consumed = [e for e in events if e.type == 'run.manager.autoresearch.consumed']
    assert len(consumed) == 1
    prompts = [e for e in events if e.type == 'run.manager.resident.prompted' and e.payload.get('reprompt')]
    assert len(prompts) == 1, [e.type for e in events]
    ref = prompts[0].payload['diagnosis_context']['recovery_context_ref']
    body = json.loads((state / ref['ref']).read_text())
    action = body['action']
    assert failure.id in action['source_event_ids']
    assert action['parent_recovery_case_id'] == 'original-case'
    assert action['recovery_phase'] == 'diagnosis_result'
    assert action['diagnosis']['failure_class'] == failure_class
    assert action['cleanup_pending'] is True
    assert not any(e.type == 'task.done' for e in events)


def test_ar_followup_waits_for_busy_resident_and_never_recurses_without_owner(tmp_path, monkeypatch):
    from tests.test_run_manager_diagnosis_consumer import _setup, _tick
    state, log, writer, cfg, _ = _setup(tmp_path, monkeypatch)
    (state / 'projections/supervisor/snapshot.json').write_text('{}')
    writer.emit('run.manager.autoresearch.requested', payload={'request_id': 'ar-failed'})
    writer.emit('autoresearch.loop.failed', payload={'loop_request_id': 'ar-failed',
        'diagnosis': {'status': 'incomplete', 'failure_class': 'missing_report'}})
    writer.emit('run.manager.resident.prompted', payload={'prompted': True, 'request_id': 'busy',
        'request_kind': 'recovery_diagnosis', 'expected_output': ['run.manager.agent.recommendation'],
        'briefing_path': str(state / 'briefings/resident.md')})
    _tick(state, log, writer, cfg, tmp_path)
    assert not any(e.type == 'run.manager.resident.prompted' and e.payload.get('reprompt') for e in log.read_all())
    cfg.runtime.run_manager.enabled = False
    _tick(state, log, writer, cfg, tmp_path)
    assert sum(e.type == 'run.manager.autoresearch.requested' for e in log.read_all()) == 1


def test_owned_observer_failure_is_not_empty_healthy_observation(tmp_path):
    from zf.autoresearch.child_supervision import child_wait
    (tmp_path / 'zf.yaml').write_text('project: {name: child, state_dir: runtime}\nruntime: {host_verification: {unknown_config: true}}\n')
    SessionStore(tmp_path / 'runtime/session.yaml').create(str(tmp_path))
    outcome = child_wait(tmp_path)
    assert outcome['failure_class'] == 'child_observer_unavailable'
    assert outcome['reproduction_status'] == 'incomplete'
    assert outcome['diagnosis_owner'] == 'run_manager'


@pytest.mark.parametrize('kind', ['multiple', 'outside', 'symlink'])
def test_cleanup_ambiguous_or_foreign_identity_never_stops_any_runtime(tmp_path, kind):
    from zf.autoresearch.runtime_cleanup import stop_experiment_runtime
    path = tmp_path / 'zf.yaml'
    path.write_text('project: {name: child, state_dir: runtime}\n')
    if kind == 'multiple': path.write_text('kind: ZfConfig\nspec: {project: {state_dir: a}}\n---\nkind: ZfConfig\nspec: {project: {state_dir: b}}\n')
    if kind == 'outside': path.write_text('project: {name: child, state_dir: ../foreign}\n')
    if kind == 'symlink': (tmp_path / 'runtime').symlink_to(tmp_path.parent)
    calls = []
    outcome = stop_experiment_runtime(tmp_path, runner=lambda *a, **k: calls.append(a))
    assert outcome['status'] == 'retained'
    assert not calls


def test_completed_diagnosis_is_not_replayed_as_old_failure(tmp_path):
    from zf.core.events.model import ZfEvent
    from zf.runtime.autoresearch_failure_handoff import pending_autoresearch_followups
    events = [ZfEvent(type='run.manager.autoresearch.requested', payload={'request_id': 'one'}),
              ZfEvent(type='autoresearch.loop.failed', payload={'run_manager_request_id': 'one'})]
    assert len(pending_autoresearch_followups(events)) == 1
    events.append(ZfEvent(type='autoresearch.loop.completed', payload={'run_manager_request_id': 'one'}))
    assert not pending_autoresearch_followups(events)


def test_cleanup_reaches_fresh_cli_with_new_schema_and_owned_state(tmp_path, monkeypatch):
    from argparse import Namespace
    from zf.cli import stop
    from zf.autoresearch.runtime_cleanup import stop_experiment_runtime
    monkeypatch.chdir(tmp_path)
    for key in ('ZF_PROJECT_ROOT', 'ZF_STATE_DIR', 'ZF_CONFIG'):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / 'zf.yaml').write_text('project: {name: child, state_dir: owned}\nroles: []\n'
        'runtime: {host_verification: {enabled: false, recollect_commands: []}}\n')
    SessionStore(tmp_path / 'owned/session.yaml').create(str(tmp_path))
    calls = []
    class Shutdown:
        def __init__(self, state, transport, **kwargs):
            assert state == tmp_path / 'owned'
            assert kwargs['config'].runtime.host_verification.recollect_commands == []
        def execute_fast(self):
            calls.append('owned shutdown')
            return []
    monkeypatch.setattr(stop, 'make_transport', lambda _: object())
    monkeypatch.setattr(stop, 'GracefulShutdown', Shutdown)
    def runner(command, **kwargs):
        code = stop.run(Namespace(force=False, fast=True, include_run_manager=True,
            preserve_run_manager=False, clean_workdirs=False, resume_cleanup=False))
        return subprocess.CompletedProcess(command, code)
    assert stop_experiment_runtime(tmp_path, runner=runner)['status'] == 'stopped'
    assert calls == ['owned shutdown']


def test_nested_ar_prompt_retains_exact_dispatch_locators(tmp_path):
    from zf.runtime.injection import build_task_prompt
    root = tmp_path / 'parent-state/repair-worktrees/request/child-state'
    briefing = root / 'briefings/plan-critic.md'
    prompt = build_task_prompt('plan-critic', briefing, 'fanout_synth')
    assert str(briefing) in prompt
    assert str(root / 'instructions/plan-critic.md') in prompt
    assert 'do not reconstruct' in prompt
    assert 'original dispatch' in prompt


@pytest.mark.parametrize("reflection", ["wait", "failed", "unavailable"])
def test_unresolved_followup_escalates_with_actual_reflection_evidence(tmp_path, reflection):
    from tests.test_run_manager import _reflect_config, _state
    from tests.test_run_manager_reflection import _wait
    from zf.core.events import EventWriter
    from zf.runtime.run_manager import run_manager_tick

    state, log, _ = _state(tmp_path)
    writer = EventWriter(log, default_origin="kernel")
    config = _reflect_config()
    config.runtime.run_manager.resident_agent.enabled = False
    config.runtime.run_manager.reflect.enabled = reflection != "unavailable"
    run = "test-run"
    original = writer.emit("run.manager.autoresearch.requested", actor="run-manager",
        correlation_id=run, payload={"request_id": "ar-owner", "workflow_run_id": run})
    problem = writer.emit("autoresearch.loop.failed", correlation_id=run, payload={
        "run_manager_request_id": "ar-owner", "workflow_run_id": run,
        "diagnosis": {"status": "incomplete", "failure_class": "missing_report"}})
    calls = []

    def decide(prompt, **kwargs):
        calls.append(1)
        if reflection == "failed":
            raise RuntimeError("fixture provider failure")
        return {"verdict": "best_so_far", "recommended_route": "wait"}

    def tick():
        run_manager_tick(state_dir=state, writer=writer, config=config, event_log=log,
                         project_root=tmp_path, spawn_repairs=False,
                         reflect_fn=decide if reflection != "unavailable" else None)

    tick()
    if reflection != "unavailable":
        _wait(lambda: any(e.type == "run.manager.reflect.completed" for e in log.read_all()))
    tick()
    tick()
    events = log.read_all()
    blocked, = [e for e in events if e.type == "run.manager.action.blocked"
                and e.payload.get("failure_class") == "autoresearch_diagnosis_incomplete"]
    human, = [e for e in events if e.type == "human.escalate" and e.causation_id == blocked.id]
    assert human.payload["reason"] == blocked.payload["reason"]
    assert original.id in human.payload["source_event_ids"] and problem.id in human.payload["source_event_ids"]
    assert sum(e.type == "run.manager.autoresearch.requested" for e in events) == 1
    assert not any(e.type == "run.manager.action.applied" for e in events)
    if reflection == "unavailable":
        assert not calls
        assert "available Run Manager decision owner" in human.payload["reason"]
        return
    assert len(calls) == 1
    requested, = [e for e in events if e.type == "run.manager.reflect.requested"]
    receipt, = [e for e in events if e.type == "run.manager.reflect.completed"]
    assert blocked.causation_id == receipt.id
    for event in (blocked, human):
        assert {requested.id, receipt.id} <= set(event.payload["source_event_ids"])
    phrase = "recommended waiting" if reflection == "wait" else "reflection failed"
    assert phrase in human.payload["reason"]


@pytest.mark.parametrize("mismatch", [
    "workflow_run_id", "checkpoint_id", "operation_id", "request_id", "source_event_ids",
    "apply_policy", "actor", "origin", "correlation_id", "reordered",
])
def test_followup_escalation_cannot_attribute_an_unbound_reflection(mismatch):
    from zf.core.events.model import ZfEvent
    from zf.runtime.autoresearch_failure_handoff import followup_escalation

    request_id = "rmrf-test"
    p = {"workflow_run_id": "run-1", "checkpoint_id": "cp-1", "request_id": request_id,
         "operation_id": "wop-" + request_id, "source_event_ids": ["source"],
         "apply_policy": "proposal_only"}
    attrs = {"actor": "run-manager", "origin": "kernel", "correlation_id": request_id}
    request = ZfEvent(type="run.manager.reflect.requested", **attrs, payload=p.copy())
    completion = {**p, "recommended_route": "wait"}
    if mismatch in attrs:
        attrs[mismatch] = "unrelated"
    elif mismatch != "reordered":
        completion[mismatch] = ["other"] if mismatch == "source_event_ids" else "unrelated"
    receipt = ZfEvent(type="run.manager.reflect.completed", **attrs, payload=completion)
    events = [receipt, request] if mismatch == "reordered" else [request, receipt]
    action, reason, cause = followup_escalation(p, events, request_id=request_id, cause="tick")
    assert "no matching outcome" in reason
    assert cause == request.id and action["source_event_ids"] == ["source", request.id]
