"""Taskless amendment rejection must reach the bounded Kernel reader route."""
from types import SimpleNamespace

import pytest

from tests.integration.test_context_recycle_e2e import _FakeTransport
from tests.test_gap_admission_recovery import config, history
from zf.cli.watch_batch import make_watch_callbacks
from zf.core.config.schema import RoleConfig
from zf.core.events import EventLog
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.wake_patterns import compute_effective_wake_patterns
from zf.runtime.watcher import EventWatcher


@pytest.mark.parametrize("layer2", [False, True])
@pytest.mark.parametrize("attempt", [0, 2])
@pytest.mark.parametrize("entry", ["run_once", "watcher"])
def test_taskless_rejection_reaches_kernel_repair_or_cap(tmp_path, monkeypatch, layer2, attempt, entry):
    state = tmp_path / "state"
    state.mkdir()
    (state / "kanban.json").write_text("[]\n")
    (state / "feature_list.json").write_text("[]\n")
    log = EventLog(state / "events.jsonl")
    events, failure = history(attempt=attempt)
    for event in events[:-1]:
        log.append(event)
    cfg = config()
    if layer2:
        cfg.roles = [RoleConfig(name="orchestrator", backend="mock")]
    runtime = Orchestrator(state, cfg, _FakeTransport())
    # Seeded history was already consumed; exercise only the new rejection.
    runtime.session_store.create(str(tmp_path))
    runtime._persist_offset(log.current_offset())
    # Isolate the unrelated provider dispatch edge; event admission and actual
    # run_once -> reactor -> bounded replan/escalation are not replaced.
    monkeypatch.setattr(runtime, "_dispatch_ready", lambda: [])
    notifications = []
    monkeypatch.setattr(runtime, "_notify_orchestrator_agent", notifications.append)
    on_event, on_batch = make_watch_callbacks(
        orchestrator=runtime, wake_patterns=compute_effective_wake_patterns(cfg),
        wake_worthy_fn=lambda _: True, rate_limiter=SimpleNamespace(allow=lambda _: True),
        simulation=False, event_log=log, stop_watcher=lambda: None,
    )
    watcher = EventWatcher(log.path, on_event, on_batch_consumed=on_batch, event_log=log)
    rejection = events[-1]
    log.append(rejection)
    assert rejection.task_id is None
    if entry == "watcher":
        watcher.poll_once()
    else:
        runtime.run_once(events=[rejection], consumed_offset=log.current_offset())
    rows = log.read_all()
    if attempt == 0:
        retries = [e for e in rows if e.type == "flow.discovery.requested" and e.causation_id == failure.id]
        assert len(retries) == 1
        assert retries[0].payload["gap_admission_repair"]["rejection_event_id"] == rejection.id
        assert retries[0].payload["rework_attempt"] == 1
    else:
        escalations = [e for e in rows if e.type == "human.escalate"]
        assert len(escalations) == 1
        assert escalations[0].payload["failure_class"] == "stage_replan_cap_exhausted"
        assert any(e.type == "run.goal.blocked" for e in rows)
        assert not any(e.type == "flow.discovery.requested" and e.causation_id == failure.id for e in rows)
    before = [(e.id, e.type) for e in rows if e.type in {"flow.discovery.requested", "human.escalate", "run.goal.blocked"}]
    runtime.run_once(events=[rejection], consumed_offset=log.current_offset())
    # The replay tail also contains newly emitted follow-up events. They may
    # notify Layer 2; the mechanical rejection/failure must remain Kernel-owned.
    assert not any(e.id in {rejection.id, failure.id} or e.type == "task_map.amend.failed"
                   for e in notifications)
    assert [(e.id, e.type) for e in log.read_all()
            if e.type in {"flow.discovery.requested", "human.escalate", "run.goal.blocked"}] == before
