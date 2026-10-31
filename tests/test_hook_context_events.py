"""Hook attribution and terminal checks retain their evidence under telemetry filtering."""
from zf.autoresearch.failure_signals import completed_run_quiesced
from zf.cli.hook_context_events import HookEventWriter, hook_context_events
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


def test_hook_writer_finds_last_parent_even_if_it_is_filtered_telemetry(tmp_path, monkeypatch):
    log = EventLog(tmp_path / 'events.jsonl')
    log.append(ZfEvent(id='evt-target', type='task.dispatched', correlation_id='old'))
    log.append(ZfEvent(type='codex.hook.post_tool_use', payload={'tool_response': 'x' * 10000}))
    log.append(ZfEvent(id='evt-target', type='codex.hook.post_tool_use', correlation_id='new'))
    expected = next(e for e in reversed(log.read_all()) if e.id == 'evt-target')
    log.index = None
    monkeypatch.setattr(log, 'read_all', lambda: (_ for _ in ()).throw(AssertionError('full read')))
    writer = HookEventWriter(log)
    assert writer._event_by_id('evt-target') == expected
    enriched = writer._enrich(ZfEvent(type='codex.hook.pre_tool_use', causation_id='evt-target'))
    assert enriched.correlation_id == 'new'


def test_hook_writer_verifies_signed_parent_events(tmp_path):
    from zf.core.security.signing import EventSigner

    log = EventLog(tmp_path / 'events.jsonl', signer=EventSigner(b'secret'))
    log.append(ZfEvent(id='evt-target', type='task.dispatched', correlation_id='run'))
    expected = log.read_all()[0]
    log.index = None
    assert HookEventWriter(log)._event_by_id('evt-target') == expected


def test_hook_writer_uses_full_read_for_json_escaped_event_ids(tmp_path, monkeypatch):
    log = EventLog(tmp_path / 'events.jsonl')
    parent = ZfEvent(id='evt/target', type='task.dispatched', correlation_id='run')
    log.append(parent)
    log.index = None
    calls = []
    read_all = log.read_all
    monkeypatch.setattr(log, 'read_all', lambda: calls.append(True) or read_all())
    assert HookEventWriter(log)._event_by_id(parent.id) == parent
    assert calls == [True]


def test_reverse_parent_handles_escaped_ids_duplicates_partial_tail_and_large_lines(tmp_path):
    path = tmp_path / 'events.jsonl'
    log = EventLog(path)
    log.index = None
    with path.open('w') as f:
        f.write(ZfEvent(id='evt-target', type='old').to_json() + '\n')
        f.write(ZfEvent(type='noise', payload={'output': 'x' * (1024 * 1024 + 50)}).to_json() + '\n')
        f.write('{"type":"new","id":"evt-\\u0074arget","ts":"2026-10-31T00:00:00Z","payload":{}}\n')
        f.write('{"type":"partial","id":"evt-target"')
    expected = next(e for e in reversed(log.read_all()) if e.id == 'evt-target')
    assert expected.type == 'new'
    assert HookEventWriter(log)._event_by_id('evt-target') == expected


def test_reverse_parent_looks_in_archives_and_handles_rotation(tmp_path, monkeypatch):
    path = tmp_path / 'events.jsonl'
    archive = tmp_path / 'events'
    archive.mkdir()
    (archive / '2026-10-30.jsonl').write_text(ZfEvent(id='evt-target', type='archived').to_json() + '\n')
    path.write_text(ZfEvent(id='other', type='current').to_json() + '\n')
    log = EventLog(path)
    log.index = None
    assert HookEventWriter(log)._event_by_id('evt-target').type == 'archived'
    from zf.cli import hook_context_events as module
    original = module.list_archives
    calls = []
    def changed(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            path.rename(archive / '2026-10-31.jsonl')
            path.write_text(ZfEvent(id='evt-target', type='new-current').to_json() + '\n')
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'list_archives', changed)
    assert HookEventWriter(log)._event_by_id('evt-target').type == 'new-current'


def test_reverse_parent_does_not_decode_unrelated_telemetry(tmp_path, monkeypatch):
    log = EventLog(tmp_path / 'events.jsonl')
    log.index = None
    log.append(ZfEvent(id='evt-target', type='task.dispatched'))
    for _ in range(100):
        log.append(ZfEvent(type='codex.hook.post_tool_use', payload={'output': 'x' * 10000}))
    decode = log.decode_line
    calls = []
    monkeypatch.setattr(log, 'decode_line', lambda line: calls.append(line) or decode(line))
    assert HookEventWriter(log)._event_by_id('evt-target').id == 'evt-target'
    assert len(calls) == 1


def test_workdir_guard_preserves_write_boundary_without_loading_full_ledger(tmp_path, monkeypatch):
    from zf.cli import hook_workdir_guard

    state = tmp_path / '.zf'
    assigned = state / 'workdirs' / 'dev' / 'project'
    assigned.mkdir(parents=True)
    log = EventLog(state / 'events.jsonl')
    writer = EventWriter(log)
    log.append(ZfEvent(type='codex.hook.post_tool_use', payload={'tool_response': 'x' * 10000}))
    monkeypatch.setattr(log, 'read_all', lambda: (_ for _ in ()).throw(AssertionError('full read')))
    monkeypatch.setattr(hook_workdir_guard, 'is_authorized_result_scratch_target', lambda *a, **k: False)

    def check(target):
        return hook_workdir_guard.evaluate_workdir_write_guard(
            state_dir=state, project_root=tmp_path, event_type='codex.hook.pre_tool_use',
            actor='dev', event_payload={'cwd': str(assigned), 'tool_name': 'Write',
                                        'tool_input': {'file_path': str(target)}},
            event_log=log, event_writer=writer, causation_id=None,
            should_check_actor=lambda actor: True, active_task_id_for_actor=lambda *args: '',
        )

    assert check(assigned / 'inside.py') == 0
    assert check(tmp_path / 'outside.py') == 2


def test_workdir_guard_skips_ledger_for_read_only_tools(tmp_path, monkeypatch):
    from zf.cli import hook_context_events as module, hook_workdir_guard
    log = EventLog(tmp_path / 'events.jsonl')
    monkeypatch.setattr(module, 'hook_context_events',
                        lambda *_: (_ for _ in ()).throw(AssertionError('unneeded ledger read')))
    for name, args in [('Read', {'file_path': '/outside/file'}),
                       ('Bash', {'command': 'zf artifact read --required'})]:
        assert hook_workdir_guard.evaluate_workdir_write_guard(
            state_dir=tmp_path, project_root=tmp_path, event_type='codex.hook.pre_tool_use',
            actor='dev', event_payload={'tool_name': name, 'tool_input': args},
            event_log=log, event_writer=EventWriter(log), causation_id=None,
            should_check_actor=lambda actor: True, active_task_id_for_actor=lambda *args: '',
        ) == 0
