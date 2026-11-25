"""Recovery receipts survive slow, progressing Readers and controller ticks."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_run_manager_reader_rebuild import _waiting, consume, recommendation
from zf.core.events import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.artifact_read_ledger import ARTIFACT_READ_SCHEMA, append_artifact_read
from zf.runtime.artifact_read_requirements import required_reads_for_attempt
from zf.runtime.run_manager import _settle_pending_action_effects, _unresolved_action_effects


def _retry(tmp_path, dispatch=True):
    runtime, ctx, old, failure = _waiting(tmp_path)
    recommendation(runtime, ctx, old, failure)
    consume(runtime)
    if dispatch:
        intent = next(e for e in runtime.event_log.read_all() if e.type == "repair.action.requested")
        runtime.run_once(events=[intent])
    events = runtime.event_log.read_all()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    events = [replace(e, ts=cutoff.isoformat()) for e in events]
    child = next((e for e in events if e.type == "fanout.child.dispatched"), None)
    return runtime, events, child, cutoff


def _read(runtime, events, dispatch, stamp, **changes):
    p = dispatch.payload
    attempt = p["payload"]["attempt_id"]
    reqs = required_reads_for_attempt(events, attempt, state_dir=runtime.state_dir, operation_id=p["operation_id"])
    row = {**reqs[0], "schema_version": ARTIFACT_READ_SCHEMA, "status": "read",
           "workflow_run_id": p["workflow_run_id"], "attempt_id": attempt,
           "operation_id": p["operation_id"], "dispatch_id": p["dispatch_id"],
           "consumer_actor": p["role_instance"], "read_at": stamp.isoformat(),
           "returned_bytes": 300, "truncated": False}
    append_artifact_read(runtime.state_dir, {**row, **changes})


def test_slow_reader_extends_wait_only_on_new_scoped_required_content(tmp_path):
    runtime, events, child, _ = _retry(tmp_path)
    _read(runtime, events, child, datetime.now(timezone.utc))
    assert _settle_pending_action_effects(events, runtime.event_writer) == 0
    # Replay after a controller restart does not create a retry or accept the vote.
    assert _settle_pending_action_effects(events, runtime.event_writer) == 0
    assert not any(e.type == "run.manager.action.effect.passed" for e in runtime.event_log.read_all())


@pytest.mark.parametrize("fault", ["heartbeat_only", "repeat", "foreign_operation", "other_reader"])
def test_unrelated_reads_and_duplicate_pages_do_not_extend_effect(tmp_path, fault):
    runtime, events, child, cutoff = _retry(tmp_path)
    if fault == "repeat":
        _read(runtime, events, child, cutoff)
        _read(runtime, events, child, datetime.now(timezone.utc))
    elif fault != "heartbeat_only":
        key = "operation_id" if fault == "foreign_operation" else "consumer_actor"
        _read(runtime, events, child, datetime.now(timezone.utc), **{key: "other"})
    assert _settle_pending_action_effects(events, runtime.event_writer) == 1
    assert runtime.event_log.read_all()[-1].type == "run.manager.action.effect.failed"


def test_recent_read_cannot_extend_expired_lease(tmp_path):
    runtime, events, child, _ = _retry(tmp_path)
    _read(runtime, events, child, datetime.now(timezone.utc))
    store = TaskAttemptStore(runtime.state_dir / "task_attempts.json")
    store.expire(now_iso=datetime.now(timezone.utc).isoformat(), is_expired=lambda _: True)
    assert _settle_pending_action_effects(events, runtime.event_writer) == 1
    assert runtime.event_log.read_all()[-1].type == "run.manager.action.effect.failed"


@pytest.mark.parametrize("pause_run, settlements", [("this", 0), ("other", 1)])
def test_bound_reader_wait_uses_only_its_run_pause(pause_run, settlements, tmp_path):
    runtime, events, child, cutoff = _retry(tmp_path)
    run_id = child.payload["workflow_run_id"] if pause_run == "this" else "other"
    events.append(ZfEvent(type="run.paused", ts=(cutoff + timedelta(minutes=10)).isoformat(),
                          correlation_id=run_id, payload={"workflow_run_id": run_id}))
    events.append(ZfEvent(type="run.resumed", correlation_id=run_id, payload={"workflow_run_id": run_id}))
    assert _settle_pending_action_effects(events, runtime.event_writer) == settlements


def test_only_exact_late_reader_vote_corrects_timeout_once(tmp_path):
    runtime, events, child, _ = _retry(tmp_path)
    assert _settle_pending_action_effects(events, runtime.event_writer) == 1
    timeout = runtime.event_log.read_all()[-1]
    assert timeout.type == "run.manager.action.effect.failed"
    events.append(timeout)
    for changes in ({"child_id": "sibling"}, {"operation_id": "previous"}):
        stale = ZfEvent(type="fanout.child.completed", origin="kernel",
                        correlation_id=child.correlation_id, payload={**child.payload, **changes})
        events.append(stale)
        assert _settle_pending_action_effects(events, runtime.event_writer) == 0
    verdict = ZfEvent(type="fanout.child.completed", origin="kernel",
                      correlation_id=child.correlation_id, payload=child.payload)
    events.append(verdict)
    assert _settle_pending_action_effects(events, runtime.event_writer) == 1
    passed = runtime.event_log.read_all()[-1]
    assert passed.type == "run.manager.action.effect.passed" and passed.causation_id == verdict.id
    assert _unresolved_action_effects([*events, passed]) == ([], [])
    assert _settle_pending_action_effects([*events, passed], runtime.event_writer) == 0


def test_requested_but_never_dispatched_times_out(tmp_path):
    runtime, events, _, _ = _retry(tmp_path, dispatch=False)
    assert _settle_pending_action_effects(events, runtime.event_writer) == 1
    failed = runtime.event_log.read_all()[-1]
    assert failed.type == "run.manager.action.effect.failed"
    assert not failed.payload["observed_event_id"]
