"""A delayed action and recovery sweep must not dispatch two planners."""
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from zf.core.config.schema import ZfConfig
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.orchestrator import Orchestrator
from zf.runtime import control_actions_candidate_rework as actions


def test_action_and_stale_kernel_snapshot_share_one_resynth(tmp_path: Path, monkeypatch):
    state = tmp_path / 'custom-state'
    state.mkdir()
    config = ZfConfig()
    config.workflow.admission_replan.enabled = True
    config.workflow.admission_replan.resynth_trigger = 'prd.scan.completed'
    writer = EventWriter(EventLog(state / 'events.jsonl'))
    service = ControlledActionService(state, writer, config=config, actor='operator')
    runtime = SimpleNamespace(state_dir=state, config=config, event_writer=writer)
    original = actions._build_resynth_event

    def overlap(payload, **kwargs):
        # The Web action has written its marker; the sweep sees that partial
        # snapshot before either consumer has appended its follow-through.
        candidate = original(payload, **kwargs)
        assert candidate is not None
        plan = SimpleNamespace(pdd_id='P1', trace_id=payload['trace_id'],
            source_event_id=payload['source_event_id'], attempt=1,
            source_event_type='integration.failed', flow_kind='prd')
        snapshot = writer.event_log.read_all()
        Orchestrator._maybe_resynth_on_replan(runtime, plan, snapshot)
        return candidate

    monkeypatch.setattr(actions, '_build_resynth_event', overlap)
    for run, failure in [('run-1', 'failure-1'), ('run-2', 'failure-1'), ('run-1', 'failure-2')]:
        payload = {'candidate_rework_action':'replan', 'pdd_id':'P1', 'trace_id':run,
            'workflow_run_id':run, 'flow_kind':'prd', 'source_event_id':failure,
            'source_event_type':'integration.failed', 'rework_attempt':1}
        result = service._candidate_rework_apply(
            requested=ZfEvent(type='web.action.requested', actor='web'),
            action='candidate-rework-apply', requested_action='candidate-rework-apply',
            payload=payload)
        assert result['ok']
    triggers = [e for e in writer.event_log.read_all() if e.type == 'prd.scan.completed']
    assert len(triggers) == 3
    assert len({(e.payload['trace_id'], e.payload['rework_of']) for e in triggers}) == 3


def test_concurrent_followthrough_is_atomic_and_new_attempt_is_allowed(tmp_path: Path):
    from zf.runtime.replan_resynth import append_replan_resynth_once

    barrier = Barrier(4)

    def append(attempt, wait=True):
        writer = EventWriter(EventLog(tmp_path / 'events.jsonl'))
        event = ZfEvent(type='prd.scan.completed', actor='zf-cli', payload={
            'workflow_run_id':'run-1', 'pdd_id':'P1',
            'rework_of':'failure-1', 'rework_attempt':attempt})
        if wait:
            barrier.wait(timeout=5)
        return append_replan_resynth_once(writer, tmp_path, event).id

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(append, [1] * 4))
    assert len(set(ids)) == 1
    assert append(2, wait=False) not in ids
    assert len(EventLog(tmp_path / 'events.jsonl').read_all()) == 2
