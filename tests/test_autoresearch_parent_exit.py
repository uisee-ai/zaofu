from types import SimpleNamespace
from unittest.mock import Mock

from zf.core.events import EventLog, EventWriter
from zf.runtime.autoresearch_parent_exit import finish_resident_on_parent_exit


def test_continue_policy_hands_off_only_after_goal_completion(tmp_path, monkeypatch):
    writer = EventWriter(EventLog(tmp_path / 'events.jsonl'))
    config = SimpleNamespace(runtime=SimpleNamespace(autoresearch_resident=SimpleNamespace(on_parent_terminal='continue')))
    sidecar = SimpleNamespace(log_handle=Mock(), lock_handle=Mock(), process=SimpleNamespace(pid=44))
    stop = Mock()
    monkeypatch.setattr('zf.runtime.autoresearch_parent_exit.stop_autoresearch_resident_sidecar', stop)
    writer.emit('run.goal.completed', payload={'workflow_run_id':'r1'})
    finish_resident_on_parent_exit(sidecar, config=config, event_log=writer.event_log)
    stop.assert_not_called()
    sidecar.lock_handle.close.assert_called_once()
    writer.emit('loop.shutdown_requested')
    finish_resident_on_parent_exit(sidecar, config=config, event_log=writer.event_log)
    stop.assert_called_once()


def test_preserve_does_not_delete_checkpoint_or_candidate(tmp_path, monkeypatch):
    writer = EventWriter(EventLog(tmp_path / 'events.jsonl'))
    config = SimpleNamespace(runtime=SimpleNamespace(autoresearch_resident=SimpleNamespace(on_parent_terminal='preserve')))
    stop = Mock()
    monkeypatch.setattr('zf.runtime.autoresearch_parent_exit.stop_autoresearch_resident_sidecar', stop)
    finish_resident_on_parent_exit(SimpleNamespace(), config=config, event_log=writer.event_log)
    stop.assert_called_once()
