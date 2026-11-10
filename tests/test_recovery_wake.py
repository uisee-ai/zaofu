"""Recovery wakes use the actual watcher batch and retain worker idempotency."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_worker_action_requests import _request, _state
from zf.cli.watch_batch import make_watch_callbacks
from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator_types import OrchestratorDecision
from zf.runtime.wake_patterns import (
    BATCH_PROCESSED_EVENTS, LAYER2_NOISE_EVENTS, WakeRateLimiter,
    compute_effective_wake_patterns, wake_worthy,
)
from zf.runtime.watcher import EventWatcher


def _watcher(log, runtime, *, limiter=None):
    on_event, on_batch = make_watch_callbacks(
        orchestrator=runtime, wake_patterns=compute_effective_wake_patterns(ZfConfig()),
        wake_worthy_fn=wake_worthy, rate_limiter=limiter or WakeRateLimiter({}),
        simulation=False, event_log=log, stop_watcher=lambda: None,
    )
    return EventWatcher(log.path, on_event, on_batch_consumed=on_batch, event_log=log)


@pytest.mark.parametrize("event_type", ["worker.respawn.requested", "workflow.operation.redrive_admitted"])
def test_recovery_event_wakes_actual_batch_without_idle_tick(tmp_path: Path, event_type: str):
    log = EventLog(tmp_path / "events.jsonl")
    calls = []
    watcher = _watcher(log, SimpleNamespace(run_once=lambda **kw: calls.append(kw)))
    event = ZfEvent(type=event_type, origin="kernel")
    log.append(event)
    boundary = log.path.stat().st_size
    watcher.poll_once()
    watcher.poll_once()
    assert calls == [{"events": [event], "consumed_offset": boundary}]
    assert event_type in LAYER2_NOISE_EVENTS


def test_recovery_burst_coalesces_and_rate_limit_is_preserved(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    calls = []
    limiter = WakeRateLimiter({"worker.respawn.requested": 1})
    assert limiter.allow("worker.respawn.requested")
    watcher = _watcher(log, SimpleNamespace(run_once=lambda **kw: calls.append(kw)), limiter=limiter)
    log.append(ZfEvent(type="worker.respawn.requested"))
    watcher.poll_once()
    assert calls == []
    events = [ZfEvent(type="worker.respawn.requested"),
              ZfEvent(type="workflow.operation.redrive_admitted"),
              ZfEvent(type="workflow.operation.redrive_admitted")]
    for event in events:
        log.append(event)
    watcher.poll_once()
    assert calls == [{"events": events, "consumed_offset": log.path.stat().st_size}]


def test_respawn_request_is_consumed_once_and_completion_precedes_dispatch(tmp_path: Path):
    _state_dir, log, orch = _state(tmp_path)
    calls = []
    completion_seen_by_dispatch = []

    def respawn(role):
        calls.append(role.instance_id)
        return OrchestratorDecision(action="respawn", role=role.instance_id, reason="mock respawn")

    orch._respawn_instance = respawn
    orch._dispatch_ready = lambda: completion_seen_by_dispatch.append(any(
        event.type == "worker.respawn.completed" for event in log.read_all())) or []
    watcher = _watcher(log, orch)
    request = _request()
    log.append(request)
    watcher.poll_once()
    assert calls == ["dev"]
    assert completion_seen_by_dispatch == [True]
    completed = [e for e in log.read_all() if e.type == "worker.respawn.completed"]
    assert len(completed) == 1 and completed[0].causation_id == request.id
    # Replayed request still wakes the batch, but the durable consumer deduplicates.
    orch.run_once(events=[request])
    assert calls == ["dev"]


def test_internal_completion_is_batch_only(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    calls = []
    watcher = _watcher(log, SimpleNamespace(run_once=lambda **kw: calls.append(kw)))
    log.append(ZfEvent(type="worker.respawn.completed"))
    watcher.poll_once()
    assert calls == []
    assert "worker.respawn.completed" in BATCH_PROCESSED_EVENTS


@pytest.mark.parametrize("event_type", ["worker.respawn.requested", "workflow.operation.redrive_admitted"])
def test_recovery_wake_does_not_dispatch_default_layer2_agent(tmp_path: Path, event_type: str):
    _state_dir, log, orch = _state(tmp_path)
    orch.config.roles.append(RoleConfig(name="orchestrator", backend="mock", triggers=[]))
    before = [event.id for event in log.read_all()]
    orch._notify_orchestrator_agent(ZfEvent(type=event_type, origin="kernel"))
    assert orch.transport.sent == []
    assert [event.id for event in log.read_all()] == before
