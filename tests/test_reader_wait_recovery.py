"""Reader waits are workflow obligations even after product Tasks finish."""
from datetime import datetime, timezone, timedelta
from pathlib import Path

from zf.core.events import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.run_manager_router import build_no_progress_projection
from zf.runtime.run_manager import _resident_diagnosis_reprompt_action, _effect_event_matches_scope


def wait_event(**changes):
    return ZfEvent(type="fanout.child.dispatch_deferred", origin="kernel", correlation_id="run-1",
        payload={"fanout_id": "f1", "child_id": "reader", "stage_id": "verify",
                 "reason": "reader_environment_busy", "target_ref": "abc", **changes})


def test_wait_counts_and_clears_only_on_matching_progress():
    events = [wait_event() for _ in range(3)]
    assert build_no_progress_projection(events)["status"] == "tripped"
    events.append(ZfEvent(type="fanout.child.dispatched", correlation_id="other",
                         payload={"fanout_id": "f1", "child_id": "reader"}))
    assert build_no_progress_projection(events)["status"] == "tripped"
    events.append(ZfEvent(type="fanout.child.dispatched", correlation_id="run-1",
                         payload={"fanout_id": "f1", "child_id": "reader"}))
    assert build_no_progress_projection(events)["status"] == "clear"


def test_reader_wait_exhaustion_after_restart_reaches_rm_with_done_task(tmp_path, monkeypatch):
    from tests.test_reader_target_pinning import _state, _trigger
    from zf.runtime.workdirs import WorkdirManager
    from zf.runtime.environment_usage import EnvironmentInUse
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.run_manager import build_run_manager_projection
    head, state, log, transport, runtime = _state(tmp_path, workdirs=True)
    TaskStore(state / 'kanban.json').add(Task(id='T', title='done', status='done'))
    monkeypatch.setattr(WorkdirManager, 'pin_reader_target', lambda *_: (_ for _ in ()).throw(EnvironmentInUse('busy')))
    _trigger(runtime, log, target_ref=head)
    wait = next(e for e in log.read_all() if e.type == 'fanout.child.dispatch_deferred')
    restarted = Orchestrator(state, runtime.config, transport, project_root=tmp_path)
    monkeypatch.setattr(restarted, '_now', lambda: datetime.fromisoformat(wait.ts).timestamp() + 3601)
    restarted.run_once(events=[])
    events = log.read_all()
    failed = [e for e in events if e.type == 'fanout.child.failed'
              and e.payload.get('failure_class') == 'reader_environment_wait_exhausted']
    assert len(failed) == 1
    assert not transport.sent
    restarted.run_once(events=[])
    assert len([e for e in log.read_all() if e.type == 'fanout.child.failed']) == 1
    projection = build_run_manager_projection(state, events=log.read_all(), config=runtime.config, project_root=tmp_path)
    actions = [a for a in projection['pending_actions'] if a.get('source_event_id') == failed[0].id]
    assert len(actions) == 1
    assert actions[0]['owner_route'] == 'run_manager'
    assert actions[0]['fanout_id'] == wait.payload['fanout_id']
    from zf.runtime.run_manager import run_manager_tick
    from zf.core.events import EventWriter
    briefing = state / 'briefings/run-manager.md'
    briefing.parent.mkdir(exist_ok=True)
    briefing.write_text('Diagnose bound Reader failure')
    resident = {'status':'observing', 'instance_id':'run-manager', 'tmux_session':'isolated',
                'briefing_path':str(briefing), 'session_mode':'dedicated'}
    monkeypatch.setattr('zf.runtime.run_manager.build_resident_agent_projection', lambda *a, **k: resident)
    monkeypatch.setattr('zf.runtime.run_manager._resident_pane_display', lambda _: {'ok':True, 'current_command':'codex', 'current_path':str(tmp_path)})
    monkeypatch.setattr('zf.runtime.run_manager._refresh_resident_briefing', lambda **k: False)
    prompts = []
    monkeypatch.setattr('zf.runtime.run_manager._send_tmux_prompt', lambda target, prompt, **k: prompts.append(prompt) or {'ok':True})
    run_manager_tick(state_dir=state, writer=EventWriter(log), config=runtime.config,
                     event_log=log, project_root=tmp_path, spawn_repairs=False)
    assert len(prompts) == 1
    assert failed[0].id in prompts[0]


def test_diagnosis_effect_requires_own_recommendation_not_task_completion():
    action = {'action': 'diagnose-attention', 'checkpoint_id': 'failure-diagnosis:e1',
              'owner_route': 'run_manager', 'task_id': 'T', 'workflow_run_id': 'r',
              'source_event_id': 'e1', 'suggested_route': 'run_manager_resident_agent', 'expected_output': ['run.manager.agent.recommendation']}
    resident = {'status':'observing','tmux_session':'test','briefing_path':'test.md','instance_id':'run-manager'}
    reprompt = _resident_diagnosis_reprompt_action(action, resident)
    assert reprompt['effect_expected_events'] == ['run.manager.agent.recommendation']
    own = ZfEvent(type='run.manager.agent.recommendation', actor='run-manager', task_id='T',
                  correlation_id='r', payload={'request_id':reprompt['request_id']})
    assert _effect_event_matches_scope(own, reprompt)
    foreign = ZfEvent(type=own.type, actor='run-manager', task_id='T', correlation_id='r',
                      payload={'request_id':'foreign'})
    assert not _effect_event_matches_scope(foreign, reprompt)


def test_diagnosis_effect_completion_does_not_close_recovery_case():
    from zf.runtime.recovery_case import build_recovery_case_projection
    event = ZfEvent(type="run.manager.action.effect.passed", payload={
        "recovery_case_id": "case", "action": "resident-agent-reprompt",
        "request_kind": "recovery_diagnosis", "request_id": "request"})
    assert build_recovery_case_projection([event])["cases"][0]["status"] == "diagnosing"


def test_explicit_stop_excludes_downtime_without_resetting_wait(tmp_path):
    from types import SimpleNamespace
    from zf.core.events import EventLog, EventWriter
    from zf.runtime.reader_wait_recovery import exhaust_reader_wait
    log = EventLog(tmp_path/'events.jsonl'); writer = EventWriter(log)
    start = datetime.now(timezone.utc)
    base = {'fanout_id':'f','child_id':'c','run_id':'attempt','reason':'reader_environment_busy'}
    writer.append(ZfEvent(type='fanout.child.dispatch_deferred', ts=start.isoformat(), payload=base))
    writer.append(ZfEvent(type='loop.stopped', ts=(start+timedelta(seconds=100)).isoformat()))
    writer.append(ZfEvent(type='loop.resume_requested', ts=(start+timedelta(seconds=3700)).isoformat()))
    runtime = SimpleNamespace(event_log=log, event_writer=writer, _now=lambda: (start+timedelta(seconds=3800)).timestamp())
    args = dict(role=SimpleNamespace(instance_id='r',stuck_threshold_seconds=300),
                context=SimpleNamespace(fanout_id='f',trace_id='run',stage_id='verify'),
                child=SimpleNamespace(child_id='c'),run_id='attempt',target_ref='head',causation_id='source')
    assert not exhaust_reader_wait(runtime, **args)
    runtime._now=lambda: (start+timedelta(seconds=3901)).timestamp()
    assert exhaust_reader_wait(runtime, **args)
