"""Merged approval gates retain generation identity and artifact fallback."""
from types import SimpleNamespace

import pytest

from tests.test_plan_approval_identity import _check, _trigger
from tests.test_writer_fanout_runtime import _approval_orch


@pytest.mark.parametrize('field', ['task_map_generation', 'requirement_spec_digest'])
def test_changed_generation_requires_separate_approval_with_same_package(tmp_path, field):
    _, log, orch = _approval_orch(tmp_path, enabled=True)
    first, second = _trigger('a' * 64), _trigger('a' * 64)
    first.payload[field], second.payload[field] = 'version-1', 'version-2'
    assert not _check(orch, first)
    assert not _check(orch, second)
    requests = [e for e in log.read_all() if e.type == 'plan.approval.requested']
    assert len(requests) == 2
    assert requests[0].payload['plan_fingerprint'] != requests[1].payload['plan_fingerprint']


def test_plain_plan_artifact_revision_is_not_deduplicated(tmp_path):
    _, log, orch = _approval_orch(tmp_path, enabled=True)
    for digest in ['a' * 64, 'b' * 64]:
        event = _trigger('')
        event.payload['plan_artifact_digest'] = digest
        assert not _check(orch, event)
    assert len([e for e in log.read_all() if e.type == 'plan.approval.requested']) == 2


def test_same_package_in_separate_runs_keeps_separate_approvals(tmp_path):
    _, log, orch = _approval_orch(tmp_path, enabled=True)
    for run in ['run-1', 'run-2']:
        assert not orch._plan_approval_satisfied(
            stage_id='impl', trigger_event=_trigger('a' * 64), trace_id=run,
            loaded=SimpleNamespace(pdd_id='F-11111111', task_map_ref='map.json',
                                   task_items=[{'task_id': 'TASK-1'}]),
        )
    assert len([e for e in log.read_all() if e.type == 'plan.approval.requested']) == 2
