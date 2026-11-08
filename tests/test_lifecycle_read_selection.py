"""Lifecycle reads keep attribution while avoiding unrelated event bodies."""
from types import SimpleNamespace

import pytest

from zf.core.events import log as log_module
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.lifecycle_evidence_queries import LifecycleEvidenceQueriesMixin
from zf.runtime.lifecycle_observation import LifecycleObservationMixin


class Host(LifecycleEvidenceQueriesMixin, LifecycleObservationMixin):
    def __init__(self, log, tasks=()):
        self.event_log = log
        self.task_store = SimpleNamespace(list_all=lambda: list(tasks))
        self.primary = None

    def _active_task_for_instance(self, instance_id):
        return self.primary


def test_dispatch_lookup_selects_bodies_and_observes_new_dispatch(tmp_path, monkeypatch):
    log = EventLog(tmp_path / 'events.jsonl')
    host = Host(log)
    first = ZfEvent(type='task.dispatched', task_id='T', payload={'body': {'value': 1}})
    log.append(first)
    log.append(ZfEvent(type='agent.text', payload={'unrelated_body': ['transcript'] * 2000}))
    copied = []
    original = log_module.deepcopy

    def observe(payload):
        if isinstance(payload, dict) and 'unrelated_body' in payload:
            copied.append(payload)
        return original(payload)

    monkeypatch.setattr(log_module, 'deepcopy', observe)
    assert host._latest_dispatch_event_for_task('T').to_json() == first.to_json()
    latest = ZfEvent(type='fanout.child.dispatched', payload={'task_id': 'T', 'body': {'value': 2}})
    log.append(latest)
    log.append(ZfEvent(type='task.dispatched', task_id='OTHER', payload={'task_id': 'T'}))
    actual = host._latest_dispatch_event_for_task('T')
    assert actual.to_json() == latest.to_json()
    actual.payload['body']['value'] = 99
    assert host._latest_dispatch_event_for_task('T').to_json() == latest.to_json()
    assert host._latest_dispatch_event_for_task('absent') is None
    assert copied == []


def test_idle_usage_and_primary_assignment_need_no_fallback_event_read():
    class NoRead:
        def read_days(self, *args, **kwargs):
            raise AssertionError('unrelated fallback must not read the ledger')

    # An exception would be swallowed by the old best-effort method, so
    # record attempts as well as checking the externally visible result.
    reads = []
    log = NoRead()
    original = log.read_days
    def read(*args, **kwargs):
        reads.append(True)
        return original(*args, **kwargs)
    log.read_days = read
    tasks = [SimpleNamespace(id='other', status='in_progress', assigned_to='other'),
             SimpleNamespace(id='done', status='done', assigned_to='dev')]
    host = Host(log, tasks)
    role = SimpleNamespace(instance_id='dev-1', name='dev')
    assert host._active_task_id_for_usage_role(role) == ''
    host.primary = SimpleNamespace(id='primary')
    assert host._active_task_id_for_usage_role(role) == 'primary'
    assert reads == []


@pytest.mark.parametrize('assigned', ['dev', 'dev-1'])
@pytest.mark.parametrize('status', ['in_progress', 'review', 'testing'])
def test_usage_keeps_complete_fanout_terminal_and_resume_context(tmp_path, assigned, status):
    log = EventLog(tmp_path / 'events.jsonl')
    host = Host(log, [SimpleNamespace(id='T', status=status, assigned_to=assigned)])
    role = SimpleNamespace(instance_id='dev-1', name='dev')
    log.append(ZfEvent(type='run.started', payload={'run_id': 'R'}))
    log.append(ZfEvent(type='run.started', payload={'run_id': 'OTHER'}))
    log.append(ZfEvent(type='workflow.invoke.requested', payload={
        'workflow_run_id': 'alias', 'run_id': 'R',
    }))
    def dispatched(child):
        return ZfEvent(type='fanout.child.dispatched', task_id='T', payload={
            'workflow_run_id': 'alias', 'fanout_id': 'F', 'child_id': child,
            'run_id': child, 'role_instance': 'dev-1',
        })
    log.append(dispatched('first'))
    assert host._active_task_id_for_usage_role(role) == 'T'
    log.append(ZfEvent(type='run.goal.blocked', payload={'workflow_run_id': 'R'}))
    assert host._active_task_id_for_usage_role(role) == ''
    log.append(ZfEvent(type='run.goal.updated', payload={'workflow_run_id': 'R', 'status': 'active'}))
    # Reopening does not revive the old fanout epoch; a fresh dispatch does.
    assert host._active_task_id_for_usage_role(role) == ''
    log.append(dispatched('second'))
    assert host._active_task_id_for_usage_role(role) == 'T'
    log.append(ZfEvent(type='fanout.child.completed', task_id='T', payload={
        'workflow_run_id': 'alias', 'fanout_id': 'F', 'child_id': 'second',
        'run_id': 'second', 'role_instance': 'dev-1',
    }))
    assert host._active_task_id_for_usage_role(role) == ''
