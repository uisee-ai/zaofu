"""Hook attribution and terminal checks retain their evidence under telemetry filtering."""
from zf.autoresearch.failure_signals import completed_run_quiesced
from zf.cli.hook_context_events import hook_context_events
from zf.cli.hook_dispatch_context import _latest_active_dispatch_for_actor
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.run_scope import run_aliases
from zf.runtime.workflow_operation import WorkflowOperationService


def test_hook_view_preserves_dispatch_and_run_aliases_and_refreshes(tmp_path, monkeypatch):
    log = EventLog(tmp_path / 'events.jsonl')
    writer = EventWriter(log)
    writer.append(ZfEvent(type='run.goal.started', correlation_id='run', payload={'run_id': 'run'}))
    # A telemetry event can bind a trace alias, so it must remain in the view.
    writer.append(ZfEvent(type='codex.hook.post_tool_use', correlation_id='trace',
                          payload={'run_id': 'run', 'tool_response': 'x' * 2000}))
    for _ in range(30):
        writer.append(ZfEvent(type='codex.hook.post_tool_use', actor='dev',
                              payload={'tool_response': 'y' * 2000}))
    service = WorkflowOperationService(state_dir=tmp_path, event_log=log, event_writer=writer)
    op = service.ensure_operation(workflow_run_id='run', operation_id='op', operation_type='task-stage',
        task_id='T1', request={'task_pipeline_stage': 'impl', 'operation_generation': 1,
                               'task_map_generation': 'map', 'workspace_generation': 1, 'placement_epoch': 1})
    service.mark_started(operation_id='op', request_hash=op.request_hash, workflow_run_id='run', task_id='T1',
                         role_instance='dev', dispatch_id='dispatch', active_attempt_id='attempt')
    dispatch = writer.append(ZfEvent(type='task.pipeline.stage.dispatched', task_id='T1', payload={
        'operation_id': 'op', 'role_instance': 'dev', 'task_id': 'T1', 'dispatch_id': 'dispatch',
        'task_map_generation': 'map', 'workspace_generation': 1, 'placement_epoch': 1,
    }))
    full = log.read_all()
    calls = []
    decode = log.decode_line
    def counted(line):
        calls.append(1)
        return decode(line)
    monkeypatch.setattr(log, 'decode_line', counted)
    view = hook_context_events(log)
    decoded = len(calls)
    from zf.cli.hook_recv import _completed_tail_quiesced, _resolve_causation
    assert _completed_tail_quiesced(log) == completed_run_quiesced(full)
    assert _resolve_causation(tmp_path, 'dev', log) == dispatch.id
    assert len(calls) == decoded  # Both consumers share the exact snapshot.
    assert len(view) < len(full)
    assert run_aliases(view) == run_aliases(full)
    assert _latest_active_dispatch_for_actor(view, 'dev') == _latest_active_dispatch_for_actor(full, 'dev') == dispatch.id
    assert completed_run_quiesced(view) == completed_run_quiesced(full)
    before = len(view)
    writer.append(ZfEvent(type='run.completed', correlation_id='run', payload={'run_id': 'run'}))
    refreshed = hook_context_events(log)
    assert len(refreshed) == before + 1
    assert completed_run_quiesced(refreshed) == completed_run_quiesced(log.read_all())
