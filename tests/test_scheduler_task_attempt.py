from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowTaskAttemptConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.session import SessionStore
from zf.core.state.task_attempts import (
    TaskAttemptLimitError,
    TaskAttemptStore,
    TaskAttemptStoreError,
)
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.task_attempt_runtime import (
    TaskAttemptDeliveryClaimedError,
    dispatch_attempt_payload,
    mark_task_attempt_sent,
    prepare_task_attempt,
    reconcile_task_attempts,
    renew_task_attempt_lease,
    settle_task_attempt_result,
    task_operation_id,
    validate_task_attempt_result,
)
from zf.runtime.transport import DispatchContext
from zf.runtime.workflow_operation import WorkflowOperationService


class _RecordingTransport:
    def __init__(self, state_dir: Path, *, fail: bool = False) -> None:
        self.state_dir = state_dir
        self.fail = fail
        self.contexts = []
        self.observed_status = ""
        self.observed_started = False
        self.observed_briefing = ""

    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        self.contexts.append(context)
        current = TaskAttemptStore(
            self.state_dir / "task_attempts.json"
        ).current_for_task(str(context.task_id or ""))
        self.observed_status = str((current or {}).get("status") or "")
        self.observed_started = any(
            event.type == "task.attempt.started"
            and event.payload.get("attempt_id") == context.attempt_id
            for event in EventLog(self.state_dir / "events.jsonl").read_all()
        )
        self.observed_briefing = briefing_path.read_text(encoding="utf-8")
        if self.fail:
            raise RuntimeError("transport unavailable")

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _runtime(
    tmp_path: Path,
    *,
    mode: str = "shadow",
    max_attempts: int = 3,
    fail_transport: bool = False,
) -> tuple[Orchestrator, _RecordingTransport, Path]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir(exist_ok=True)
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    SessionStore(state_dir / "session.yaml").create(
        project_root=str(tmp_path),
    )
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="attempt test",
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id="disp-1",
        contract=TaskContract(
            evidence_contract={"workflow_run_id": "RUN-1"},
        ),
    ))
    config = ZfConfig(
        project=ProjectConfig(name="attempt-test"),
        roles=[
            RoleConfig(
                name="dev",
                instance_id="dev",
                backend="mock",
                publishes=["dev.build.done", "dev.blocked"],
            ),
        ],
        workflow=WorkflowConfig(
            task_attempt=WorkflowTaskAttemptConfig(
                mode=mode,
                max_attempts=max_attempts,
            ),
        ),
    )
    transport = _RecordingTransport(state_dir, fail=fail_transport)
    return Orchestrator(state_dir, config, transport), transport, state_dir


def _send(
    runtime: Orchestrator,
    state_dir: Path,
    *,
    dispatch_id: str,
):
    task = runtime.task_store.update(
        "TASK-1",
        status="in_progress",
        active_dispatch_id=dispatch_id,
    )
    assert task is not None
    runtime._remember_dispatch_id("TASK-1", dispatch_id)
    briefing = state_dir / "briefings" / f"{dispatch_id}.md"
    briefing.parent.mkdir(parents=True, exist_ok=True)
    briefing.write_text("# Task\n", encoding="utf-8")
    role = runtime.config.roles[0]
    context = runtime._dispatch_context(
        role=role,
        briefing_path=briefing,
        task_id="TASK-1",
    )
    return runtime._send_transport_task(
        role.instance_id,
        briefing,
        "read briefing",
        context,
    )


def test_dispatch_persists_attempt_before_transport_and_closes_identity(
    tmp_path: Path,
) -> None:
    runtime, transport, state_dir = _runtime(tmp_path)

    context = _send(runtime, state_dir, dispatch_id="disp-1")

    assert context is not None
    assert transport.observed_status == "delivering"
    assert transport.observed_started is True
    assert context.attempt_id in transport.observed_briefing
    assert context.lease_id in transport.observed_briefing
    assert context.run_id == "RUN-1"
    current = TaskAttemptStore(
        state_dir / "task_attempts.json"
    ).current(run_id="RUN-1", task_id="TASK-1")
    assert current is not None
    assert current["status"] == "sent"
    assert dispatch_attempt_payload(context) == {
        "workflow_run_id": "RUN-1",
        "run_id": "RUN-1",
        "operation_id": task_operation_id(
            run_id="RUN-1",
            task_id="TASK-1",
            role_name="dev",
        ),
        "attempt_id": context.attempt_id,
        "lease_id": context.lease_id,
        "dispatch_id": "disp-1",
    }


def test_enforce_rejects_stale_attempt_lease_run_and_dispatch(
    tmp_path: Path,
) -> None:
    runtime, _, state_dir = _runtime(tmp_path, mode="enforce")
    first = _send(runtime, state_dir, dispatch_id="disp-1")
    second = _send(runtime, state_dir, dispatch_id="disp-2")
    assert first is not None and second is not None
    task = runtime.task_store.get("TASK-1")
    assert task is not None

    stale = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload=dispatch_attempt_payload(first),
    )
    reason = validate_task_attempt_result(runtime, stale, task=task)

    assert "attempt_id_mismatch" in reason
    assert "lease_id_mismatch" in reason
    assert "dispatch_id_mismatch" in reason
    decision = runtime._reject_invalid_lifecycle_event(stale)
    assert decision is not None
    assert "TaskAttempt identity" in decision.reason
    wrong_run = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={
            **dispatch_attempt_payload(second),
            "workflow_run_id": "RUN-WRONG",
        },
    )
    assert "workflow_run_id_mismatch" in validate_task_attempt_result(
        runtime,
        wrong_run,
        task=task,
    )
    other = Task(
        id="TASK-2",
        title="other",
        status="in_progress",
        assigned_to="dev",
        contract=TaskContract(
            evidence_contract={"workflow_run_id": "RUN-2"},
        ),
    )
    runtime.task_store.add(other)
    wrong_task = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-2",
        payload=dispatch_attempt_payload(second),
    )
    assert "current_attempt_missing" in validate_task_attempt_result(
        runtime,
        wrong_task,
        task=other,
    )
    fanout_stale = ZfEvent(
        type="dev.build.done",
        actor="orchestrator",
        origin="worker",
        task_id="TASK-1",
        payload={
            **dispatch_attempt_payload(first),
            "fanout_id": "fanout-1",
            "child_id": "child-1",
        },
    )
    fanout_decision = runtime._reject_invalid_lifecycle_event(fanout_stale)
    assert fanout_decision is not None
    assert "TaskAttempt identity" in fanout_decision.reason


def test_enforce_does_not_renew_lease_with_partial_or_stale_identity(
    tmp_path: Path,
) -> None:
    runtime, _, state_dir = _runtime(tmp_path, mode="enforce")
    context = _send(runtime, state_dir, dispatch_id="disp-1")
    assert context is not None
    store = TaskAttemptStore(state_dir / "task_attempts.json")
    before = store.current(run_id="RUN-1", task_id="TASK-1")
    assert before is not None
    heartbeat = ZfEvent(
        type="worker.heartbeat",
        actor="dev",
        task_id="TASK-1",
        payload={
            **dispatch_attempt_payload(context),
            "lease_id": "lease-stale",
        },
    )

    renew_task_attempt_lease(runtime, heartbeat)

    after = store.current(run_id="RUN-1", task_id="TASK-1")
    assert after is not None
    assert after["lease_expires_at"] == before["lease_expires_at"]
    rejected = next(
        event
        for event in runtime.event_log.read_all()
        if event.type == "task.attempt.result_rejected"
        and event.payload.get("source_event_id") == heartbeat.id
    )
    assert "lease_renewal:lease_id_mismatch" in rejected.payload["reason"]


@pytest.mark.parametrize("event_type", ["worker.heartbeat", "agent.usage"])
@pytest.mark.parametrize("terminal_status", ["failed", "succeeded", "expired"])
def test_terminal_attempt_does_not_emit_heartbeat_for_late_activity(
    tmp_path: Path, event_type: str, terminal_status: str,
) -> None:
    runtime, _, state_dir = _runtime(tmp_path, mode="shadow")
    context = _send(runtime, state_dir, dispatch_id="disp-1")
    assert context is not None
    store = TaskAttemptStore(state_dir / "task_attempts.json")
    store.update(context.attempt_id, status=terminal_status, updated_at="2026-11-01T00:00:00+00:00")
    before = store.get(context.attempt_id)
    source = ZfEvent(
        type=event_type, actor="dev", task_id="TASK-1",
        payload=dispatch_attempt_payload(context),
    )

    renew_task_attempt_lease(runtime, source)

    assert store.get(context.attempt_id) == before
    assert not any(
        event.type == "task.attempt.heartbeat"
        and event.payload.get("source_event_id") == source.id
        for event in runtime.event_log.read_all()
    )


def test_attempt_finishing_during_heartbeat_does_not_emit_renewal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _, state_dir = _runtime(tmp_path, mode="shadow")
    context = _send(runtime, state_dir, dispatch_id="disp-1")
    assert context is not None
    original = TaskAttemptStore.renew_lease

    def settle_before_renew(self, attempt_id, **kwargs):
        self.update(attempt_id, status="failed", updated_at="2026-11-01T00:00:00+00:00")
        return original(self, attempt_id, **kwargs)

    monkeypatch.setattr(TaskAttemptStore, "renew_lease", settle_before_renew)
    source = ZfEvent(
        type="agent.usage", actor="dev", task_id="TASK-1",
        payload=dispatch_attempt_payload(context),
    )
    renew_task_attempt_lease(runtime, source)

    store = TaskAttemptStore(state_dir / "task_attempts.json")
    assert store.get(context.attempt_id)["status"] == "failed"
    assert not any(
        event.type == "task.attempt.heartbeat"
        and event.payload.get("source_event_id") == source.id
        for event in runtime.event_log.read_all()
    )


def test_disk_usage_renews_unique_reader_attempt_without_task_store_row(
    tmp_path: Path,
) -> None:
    runtime, _, state_dir = _runtime(tmp_path, mode="enforce")
    briefing = state_dir / "briefings" / "reader.md"
    briefing.parent.mkdir(parents=True, exist_ok=True)
    briefing.write_text("# Reader\n", encoding="utf-8")
    prepared = prepare_task_attempt(
        runtime,
        context=DispatchContext(
            task_id="ISSUE-READER-1",
            role_name="dev",
            instance_id="dev",
            dispatch_id="disp-reader",
            run_id="RUN-READER",
            operation_id="op-reader",
        ),
        briefing_path=briefing,
    )
    assert prepared is not None
    mark_task_attempt_sent(runtime, prepared)
    store = TaskAttemptStore(state_dir / "task_attempts.json")
    store.renew_lease(
        prepared.context.attempt_id,
        updated_at="2000-01-01T00:00:00+00:00",
        lease_expires_at="2000-01-01T00:00:01+00:00",
    )

    runtime._synthesize_agent_usage(
        runtime.config.roles[0],
        SimpleNamespace(
            timestamp=1234,
            raw={"input_tokens": 100, "output_tokens": 50},
            effective_input_tokens=100,
            output_tokens=50,
            ratio=0.1,
            model_context_window=200000,
            model="gpt-5.5-codex",
        ),
    )

    usage = next(
        event
        for event in runtime.event_log.read_all()
        if event.type == "agent.usage"
    )
    assert usage.task_id == "ISSUE-READER-1"
    assert usage.correlation_id == "RUN-READER"
    assert {
        key: usage.payload[key]
        for key in (
            "workflow_run_id",
            "operation_id",
            "attempt_id",
            "lease_id",
            "dispatch_id",
        )
    } == {
        "workflow_run_id": "RUN-READER",
        "operation_id": "op-reader",
        "attempt_id": prepared.context.attempt_id,
        "lease_id": prepared.context.lease_id,
        "dispatch_id": "disp-reader",
    }
    current = store.current(
        run_id="RUN-READER",
        task_id="ISSUE-READER-1",
    )
    assert current is not None
    assert current["lease_expires_at"] != "2000-01-01T00:00:01+00:00"
    assert any(
        event.type == "task.attempt.heartbeat"
        and event.payload.get("source_event_id") == usage.id
        for event in runtime.event_log.read_all()
    )


def test_disk_usage_does_not_guess_between_active_reader_attempts(
    tmp_path: Path,
) -> None:
    runtime, _, state_dir = _runtime(tmp_path, mode="enforce")
    for ordinal in (1, 2):
        briefing = state_dir / "briefings" / f"reader-{ordinal}.md"
        briefing.parent.mkdir(parents=True, exist_ok=True)
        briefing.write_text("# Reader\n", encoding="utf-8")
        prepared = prepare_task_attempt(
            runtime,
            context=DispatchContext(
                task_id=f"ISSUE-READER-{ordinal}",
                role_name="dev",
                instance_id="dev",
                dispatch_id=f"disp-reader-{ordinal}",
                run_id="RUN-READER",
                operation_id=f"op-reader-{ordinal}",
            ),
            briefing_path=briefing,
        )
        assert prepared is not None
        mark_task_attempt_sent(runtime, prepared)

    runtime._synthesize_agent_usage(
        runtime.config.roles[0],
        SimpleNamespace(
            timestamp=1234,
            raw={"input_tokens": 100, "output_tokens": 50},
            effective_input_tokens=100,
            output_tokens=50,
            ratio=0.1,
            model_context_window=200000,
            model="gpt-5.5-codex",
        ),
    )

    usage = next(
        event
        for event in runtime.event_log.read_all()
        if event.type == "agent.usage"
    )
    assert usage.task_id is None
    assert usage.payload.get("task_id") == ""
    assert "attempt_id" not in usage.payload
    assert not any(
        event.type == "task.attempt.heartbeat"
        for event in runtime.event_log.read_all()
    )


def test_shadow_allows_legacy_result_but_records_mismatch(
    tmp_path: Path,
) -> None:
    runtime, _, state_dir = _runtime(tmp_path, mode="shadow")
    _send(runtime, state_dir, dispatch_id="disp-1")
    task = runtime.task_store.get("TASK-1")
    assert task is not None
    event = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={"dispatch_id": "disp-1"},
    )

    assert validate_task_attempt_result(runtime, event, task=task) == ""
    assert any(
        row.type == "task.attempt.shadow_mismatch"
        and row.payload.get("source_event_id") == event.id
        for row in runtime.event_log.read_all()
    )
    settle_task_attempt_result(runtime, event)
    current = TaskAttemptStore(
        state_dir / "task_attempts.json"
    ).current(run_id="RUN-1", task_id="TASK-1")
    assert current is not None
    assert current["status"] == "sent"
    assert not any(
        row.type == "task.attempt.succeeded"
        and row.payload.get("source_event_id") == event.id
        for row in runtime.event_log.read_all()
    )


def test_transport_failure_schedules_one_retry_and_success_resets_series(
    tmp_path: Path,
) -> None:
    runtime, transport, state_dir = _runtime(
        tmp_path,
        mode="enforce",
        max_attempts=2,
        fail_transport=True,
    )
    with pytest.raises(RuntimeError, match="transport unavailable"):
        _send(runtime, state_dir, dispatch_id="disp-1")
    first = TaskAttemptStore(
        state_dir / "task_attempts.json"
    ).current(run_id="RUN-1", task_id="TASK-1")
    assert first is not None
    assert first["status"] == "failed"
    assert first["failure_class"] == "transport_delivery"
    assert first["retryable"] is True
    assert first["recovery_owner"] == "scheduler"

    transport.fail = False
    second_context = _send(runtime, state_dir, dispatch_id="disp-2")
    assert second_context is not None
    rows = TaskAttemptStore(state_dir / "task_attempts.json").rows()
    assert len(rows) == 2
    assert {row["ordinal"] for row in rows} == {1, 2}
    assert next(row for row in rows if row["ordinal"] == 1)["status"] == "superseded"
    superseded = next(
        row
        for row in runtime.event_log.read_all()
        if row.type == "task.attempt.superseded"
        and row.payload.get("attempt_id") == first["attempt_id"]
    )
    assert superseded.payload["dispatch_id"] == first["dispatch_id"]
    assert superseded.payload["lease_id"] == first["lease_id"]
    assert superseded.payload["superseded_by"] == second_context.attempt_id
    retry_events = [
        row
        for row in runtime.event_log.read_all()
        if row.type == "task.attempt.retry_scheduled"
    ]
    assert len(retry_events) == 1

    runtime.event_writer.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-1",
        payload=dispatch_attempt_payload(second_context),
    ))
    settle_task_attempt_result(runtime, ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload=dispatch_attempt_payload(second_context),
    ))
    third_context = _send(runtime, state_dir, dispatch_id="disp-3")
    assert third_context is not None
    third = TaskAttemptStore(
        state_dir / "task_attempts.json"
    ).current(run_id="RUN-1", task_id="TASK-1")
    assert third is not None
    assert third["series"] == 2
    assert third["ordinal"] == 1


def test_repeated_transport_failures_deadletter_at_retry_cap(
    tmp_path: Path,
) -> None:
    runtime, _, state_dir = _runtime(
        tmp_path,
        mode="enforce",
        max_attempts=2,
        fail_transport=True,
    )
    for dispatch_id in ("disp-1", "disp-2"):
        with pytest.raises(RuntimeError, match="transport unavailable"):
            _send(runtime, state_dir, dispatch_id=dispatch_id)

    with pytest.raises(TaskAttemptLimitError):
        _send(runtime, state_dir, dispatch_id="disp-3")
    assert runtime.task_store.get("TASK-1").status == "blocked"
    assert sum(
        event.type == "task.attempt.deadlettered"
        for event in runtime.event_log.read_all()
    ) == 1


def test_expired_lease_requeues_once_and_deadletters_at_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _, state_dir = _runtime(
        tmp_path,
        mode="enforce",
        max_attempts=1,
    )
    _send(runtime, state_dir, dispatch_id="disp-1")
    monkeypatch.setattr(
        "zf.runtime.task_attempt_runtime._expired",
        lambda value: True,
    )

    assert reconcile_task_attempts(runtime) == 1
    assert reconcile_task_attempts(runtime) == 0
    current = TaskAttemptStore(
        state_dir / "task_attempts.json"
    ).current(run_id="RUN-1", task_id="TASK-1")
    assert current is not None
    assert current["status"] == "deadlettered"
    assert runtime.task_store.get("TASK-1").status == "blocked"
    assert sum(
        event.type == "task.attempt.deadlettered"
        for event in runtime.event_log.read_all()
    ) == 1


def test_restart_does_not_recast_or_redeliver_claimed_attempt(
    tmp_path: Path,
) -> None:
    runtime, _, state_dir = _runtime(tmp_path, mode="enforce")
    role = runtime.config.roles[0]
    briefing = state_dir / "briefings" / "disp-1.md"
    briefing.parent.mkdir(parents=True, exist_ok=True)
    briefing.write_text("# Task\n", encoding="utf-8")
    context = runtime._dispatch_context(
        role=role,
        briefing_path=briefing,
        task_id="TASK-1",
    )
    prepared = prepare_task_attempt(
        runtime,
        context=context,
        briefing_path=briefing,
    )
    assert prepared is not None

    restarted = Orchestrator(
        state_dir,
        runtime.config,
        _RecordingTransport(state_dir),
    )
    restarted_context = restarted._dispatch_context(
        role=role,
        briefing_path=briefing,
        task_id="TASK-1",
    )
    with pytest.raises(TaskAttemptDeliveryClaimedError):
        prepare_task_attempt(
            restarted,
            context=restarted_context,
            briefing_path=briefing,
        )
    assert len(TaskAttemptStore(state_dir / "task_attempts.json").rows()) == 1


def test_restart_does_not_redeliver_sent_attempt(
    tmp_path: Path,
) -> None:
    runtime, _, state_dir = _runtime(tmp_path, mode="enforce")
    _send(runtime, state_dir, dispatch_id="disp-1")
    briefing = state_dir / "briefings" / "disp-1.md"

    restarted_transport = _RecordingTransport(state_dir)
    restarted = Orchestrator(
        state_dir,
        runtime.config,
        restarted_transport,
    )
    restarted_context = restarted._dispatch_context(
        role=runtime.config.roles[0],
        briefing_path=briefing,
        task_id="TASK-1",
    )

    with pytest.raises(TaskAttemptDeliveryClaimedError):
        restarted._send_transport_task(
            "dev",
            briefing,
            "read briefing",
            restarted_context,
        )

    assert restarted_transport.contexts == []
    rows = TaskAttemptStore(state_dir / "task_attempts.json").rows()
    assert len(rows) == 1
    assert rows[0]["status"] == "sent"


def test_current_for_task_prefers_the_only_active_run_attempt(
    tmp_path: Path,
) -> None:
    store = TaskAttemptStore(tmp_path / "task_attempts.json")
    first = store.ensure_for_dispatch(
        run_id="RUN-OLD",
        task_id="TASK-SHARED",
        dispatch_id="dispatch-old",
        role="dev",
        instance_id="dev-1",
        operation_id="operation-old",
        briefing_ref="old.md",
        created_at="2026-07-26T00:00:00+00:00",
        lease_expires_at="2026-07-26T01:00:00+00:00",
        max_attempts=3,
    ).attempt
    store.update(
        first["attempt_id"],
        status="succeeded",
        updated_at="2026-07-26T00:10:00+00:00",
    )
    second = store.ensure_for_dispatch(
        run_id="RUN-NEW",
        task_id="TASK-SHARED",
        dispatch_id="dispatch-new",
        role="dev",
        instance_id="dev-2",
        operation_id="operation-new",
        briefing_ref="new.md",
        created_at="2026-07-26T00:20:00+00:00",
        lease_expires_at="2026-07-26T01:20:00+00:00",
        max_attempts=3,
    ).attempt

    current = store.current_for_task("TASK-SHARED")

    assert current is not None
    assert current["attempt_id"] == second["attempt_id"]
    assert current["run_id"] == "RUN-NEW"


def test_shadow_mode_never_blocks_legacy_dispatch_at_attempt_cap(
    tmp_path: Path,
) -> None:
    runtime, transport, state_dir = _runtime(
        tmp_path,
        mode="shadow",
        max_attempts=1,
    )

    first = _send(runtime, state_dir, dispatch_id="disp-1")
    second = _send(runtime, state_dir, dispatch_id="disp-2")

    assert first is not None and second is not None
    assert len(transport.contexts) == 2
    current = TaskAttemptStore(
        state_dir / "task_attempts.json"
    ).current(run_id="RUN-1", task_id="TASK-1")
    assert current is not None
    assert current["ordinal"] == 2
    assert not any(
        event.type == "task.attempt.deadlettered"
        for event in runtime.event_log.read_all()
    )


def test_retry_budget_is_scoped_to_logical_role(tmp_path: Path) -> None:
    store = TaskAttemptStore(tmp_path / "task_attempts.json")
    first = store.ensure_for_dispatch(
        run_id="RUN-1",
        task_id="TASK-1",
        dispatch_id="dispatch-dev-1",
        role="dev",
        instance_id="dev-1",
        operation_id="operation-1",
        briefing_ref="dev.md",
        created_at="2026-07-26T00:00:00+00:00",
        lease_expires_at="2026-07-26T01:00:00+00:00",
        max_attempts=2,
    ).attempt
    store.update(
        first["attempt_id"],
        status="failed",
        updated_at="2026-07-26T00:05:00+00:00",
    )

    verify = store.ensure_for_dispatch(
        run_id="RUN-1",
        task_id="TASK-1",
        dispatch_id="dispatch-test-1",
        role="test",
        instance_id="test-1",
        operation_id="operation-1",
        briefing_ref="test.md",
        created_at="2026-07-26T00:10:00+00:00",
        lease_expires_at="2026-07-26T01:10:00+00:00",
        max_attempts=2,
    ).attempt
    rework = store.ensure_for_dispatch(
        run_id="RUN-1",
        task_id="TASK-1",
        dispatch_id="dispatch-dev-2",
        role="dev",
        instance_id="dev-2",
        operation_id="operation-1",
        briefing_ref="rework.md",
        created_at="2026-07-26T00:20:00+00:00",
        lease_expires_at="2026-07-26T01:20:00+00:00",
        max_attempts=2,
    ).attempt

    assert verify["ordinal"] == 1
    assert rework["ordinal"] == 2


def test_fanout_attempt_lanes_remain_independent_for_one_parent_task(
    tmp_path: Path,
) -> None:
    runtime, _, state_dir = _runtime(tmp_path, mode="enforce")

    def prepare_lane(
        *,
        operation_id: str,
        role_name: str,
        dispatch_id: str,
    ):
        briefing = state_dir / "briefings" / f"{dispatch_id}.md"
        briefing.parent.mkdir(parents=True, exist_ok=True)
        briefing.write_text("# Fanout child\n", encoding="utf-8")
        prepared = prepare_task_attempt(
            runtime,
            context=DispatchContext(
                run_id="RUN-1",
                task_id="TASK-1",
                role_name=role_name,
                instance_id=role_name,
                dispatch_id=dispatch_id,
                operation_id=operation_id,
                briefing_path=briefing,
            ),
            briefing_path=briefing,
        )
        assert prepared is not None
        return prepared

    reader = prepare_lane(
        operation_id="operation-reader",
        role_name="reader",
        dispatch_id="dispatch-reader-1",
    )
    critic = prepare_lane(
        operation_id="operation-critic",
        role_name="critic",
        dispatch_id="dispatch-critic-1",
    )
    store = TaskAttemptStore(state_dir / "task_attempts.json")

    assert store.current(run_id="RUN-1", task_id="TASK-1") is None
    assert store.current_for_attempt(
        task_id="TASK-1",
        attempt_id=str(reader.context.attempt_id),
    )["attempt_id"] == reader.context.attempt_id
    assert store.current_for_attempt(
        task_id="TASK-1",
        attempt_id=str(critic.context.attempt_id),
    )["attempt_id"] == critic.context.attempt_id
    task = runtime.task_store.get("TASK-1")
    assert task is not None
    for prepared in (reader, critic):
        assert validate_task_attempt_result(
            runtime,
            ZfEvent(
                type="dev.build.done",
                actor=str(prepared.context.role_name or ""),
                task_id="TASK-1",
                payload=dispatch_attempt_payload(prepared.context),
            ),
            task=task,
        ) == ""

    store.update(
        str(reader.context.attempt_id),
        status="failed",
        updated_at="2026-07-26T00:30:00+00:00",
    )
    reader_retry = prepare_lane(
        operation_id="operation-reader",
        role_name="reader",
        dispatch_id="dispatch-reader-2",
    )

    assert store.get(str(reader.context.attempt_id))["status"] == "superseded"
    assert store.get(str(critic.context.attempt_id))["status"] == "delivering"
    assert store.current_for_attempt(
        task_id="TASK-1",
        attempt_id=str(reader.context.attempt_id),
    )["attempt_id"] == reader_retry.context.attempt_id
    assert validate_task_attempt_result(
        runtime,
        ZfEvent(
            type="dev.build.done",
            actor="critic",
            task_id="TASK-1",
            payload=dispatch_attempt_payload(critic.context),
        ),
        task=task,
    ) == ""
    stale_reason = validate_task_attempt_result(
        runtime,
        ZfEvent(
            type="dev.build.done",
            actor="reader",
            task_id="TASK-1",
            payload=dispatch_attempt_payload(reader.context),
        ),
        task=task,
    )
    assert "attempt_id_mismatch" in stale_reason


def test_retry_order_survives_sorted_json_reload(tmp_path: Path) -> None:
    store = TaskAttemptStore(tmp_path / "task_attempts.json")
    dispatches = [f"dispatch-{index}" for index in range(20)]
    ordered = sorted(
        dispatches,
        key=lambda dispatch_id: hashlib.sha256(
            f"RUN-1|TASK-1|{dispatch_id}".encode("utf-8")
        ).hexdigest()[:20],
    )
    older_dispatch = ordered[-1]
    newer_dispatch = ordered[0]
    first = store.ensure_for_dispatch(
        run_id="RUN-1",
        task_id="TASK-1",
        dispatch_id=older_dispatch,
        role="dev",
        instance_id="dev",
        operation_id="operation-1",
        briefing_ref="first.md",
        created_at="2026-07-26T00:00:00+00:00",
        lease_expires_at="2026-07-26T01:00:00+00:00",
        max_attempts=3,
    ).attempt
    store.update(
        first["attempt_id"],
        status="failed",
        updated_at="2026-07-26T00:05:00+00:00",
    )
    second = store.ensure_for_dispatch(
        run_id="RUN-1",
        task_id="TASK-1",
        dispatch_id=newer_dispatch,
        role="dev",
        instance_id="dev",
        operation_id="operation-1",
        briefing_ref="second.md",
        created_at="2026-07-26T00:10:00+00:00",
        lease_expires_at="2026-07-26T01:10:00+00:00",
        max_attempts=3,
    ).attempt
    store.update(
        second["attempt_id"],
        status="succeeded",
        updated_at="2026-07-26T00:15:00+00:00",
    )

    third = TaskAttemptStore(store.path).ensure_for_dispatch(
        run_id="RUN-1",
        task_id="TASK-1",
        dispatch_id="dispatch-next-series",
        role="dev",
        instance_id="dev",
        operation_id="operation-1",
        briefing_ref="third.md",
        created_at="2026-07-26T00:20:00+00:00",
        lease_expires_at="2026-07-26T01:20:00+00:00",
        max_attempts=3,
    ).attempt

    assert third["series"] == 2
    assert third["ordinal"] == 1


def test_empty_store_keys_are_validated_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "task_attempts.json"
    path.write_text(
        '{"schema_version":"task-attempt-store.v1","revision":1,'
        '"attempts":{"":[]},"current":{}}\n',
        encoding="utf-8",
    )

    with pytest.raises(TaskAttemptStoreError, match="row must be an object"):
        TaskAttemptStore(path).load()


def test_current_scope_mismatch_is_validated_fail_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "task_attempts.json"
    store = TaskAttemptStore(path)
    attempt = store.ensure_for_dispatch(
        run_id="RUN-1",
        task_id="TASK-1",
        dispatch_id="dispatch-1",
        role="dev",
        instance_id="dev",
        operation_id="operation-1",
        briefing_ref="briefing.md",
        created_at="2026-07-26T00:00:00+00:00",
        lease_expires_at="2026-07-26T01:00:00+00:00",
        max_attempts=3,
    ).attempt
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["current"] = {
        "RUN-WRONG::TASK-1::tak-wrong": attempt["attempt_id"],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TaskAttemptStoreError, match="current scope is invalid"):
        store.load()


def test_corrupt_canonical_store_fails_closed_before_transport(
    tmp_path: Path,
) -> None:
    runtime, transport, state_dir = _runtime(tmp_path, mode="enforce")
    store_path = state_dir / "task_attempts.json"
    store_path.write_text("{not-json\n", encoding="utf-8")

    with pytest.raises(TaskAttemptStoreError, match="not valid JSON"):
        _send(runtime, state_dir, dispatch_id="disp-1")

    assert transport.contexts == []
    assert store_path.read_text(encoding="utf-8") == "{not-json\n"


def test_accepted_result_settles_attempt_and_shadow_compares(
    tmp_path: Path,
) -> None:
    runtime, _, state_dir = _runtime(tmp_path)
    context = _send(runtime, state_dir, dispatch_id="disp-1")
    assert context is not None
    runtime.event_writer.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-1",
        payload=dispatch_attempt_payload(context),
    ))
    result = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload=dispatch_attempt_payload(context),
    )

    settle_task_attempt_result(runtime, result)

    current = TaskAttemptStore(
        state_dir / "task_attempts.json"
    ).current(run_id="RUN-1", task_id="TASK-1")
    assert current is not None
    assert current["status"] == "succeeded"
    comparison = [
        event
        for event in runtime.event_log.read_all()
        if event.type == "task.attempt.shadow.compared"
    ][-1]
    assert comparison.payload["match"] is True


def test_child_transport_events_wait_for_operation_admission(
    tmp_path: Path,
) -> None:
    runtime, _, state_dir = _runtime(tmp_path)
    context = _send(runtime, state_dir, dispatch_id="disp-1")
    assert context is not None
    identity = dispatch_attempt_payload(context)

    settle_task_attempt_result(runtime, ZfEvent(
        type="issue.triage.child.completed",
        actor="issue-triage",
        task_id="TASK-1",
        payload=identity,
    ))
    settle_task_attempt_result(runtime, ZfEvent(
        type="issue.triage.child.failed",
        actor="issue-triage",
        task_id="TASK-1",
        payload={**identity, "reason": "semantic output rejected"},
    ))

    current = TaskAttemptStore(
        state_dir / "task_attempts.json"
    ).current(run_id="RUN-1", task_id="TASK-1")
    assert current is not None
    assert current["status"] == "sent"
    assert not any(
        event.type in {"task.attempt.succeeded", "task.attempt.failed"}
        for event in runtime.event_log.read_all()
    )


def test_attempt_terminal_is_monotonic_in_shadow_mode(tmp_path: Path) -> None:
    runtime, _, state_dir = _runtime(tmp_path, mode="shadow")
    context = _send(runtime, state_dir, dispatch_id="disp-1")
    assert context is not None
    identity = dispatch_attempt_payload(context)

    settle_task_attempt_result(runtime, ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload=identity,
    ))
    settle_task_attempt_result(runtime, ZfEvent(
        type="dev.failed",
        actor="dev",
        task_id="TASK-1",
        payload={**identity, "reason": "late contradictory result"},
    ))

    current = TaskAttemptStore(
        state_dir / "task_attempts.json"
    ).current(run_id="RUN-1", task_id="TASK-1")
    assert current is not None
    assert current["status"] == "succeeded"
    terminals = [
        event.type
        for event in runtime.event_log.read_all()
        if event.type in {"task.attempt.succeeded", "task.attempt.failed"}
    ]
    assert terminals == ["task.attempt.succeeded"]


def test_attempt_terminal_occurrence_clears_matching_active_dispatch(
    tmp_path: Path,
) -> None:
    runtime, _, state_dir = _runtime(tmp_path)
    context = _send(runtime, state_dir, dispatch_id="disp-1")
    assert context is not None
    terminal = ZfEvent(
        type="task.attempt.succeeded",
        actor="orchestrator",
        origin="kernel",
        task_id="TASK-1",
        payload={**dispatch_attempt_payload(context), "status": "succeeded"},
    )

    settle_task_attempt_result(runtime, terminal)

    task = runtime.task_store.get("TASK-1")
    assert task is not None
    assert task.active_dispatch_id == ""
    assert "TASK-1" not in runtime._active_dispatch_ids


def test_admitted_workflow_operation_settles_attempt_without_worker_identity(
    tmp_path: Path,
) -> None:
    runtime, _, state_dir = _runtime(tmp_path)
    context = _send(runtime, state_dir, dispatch_id="disp-1")
    assert context is not None
    source = ZfEvent(
        type="workflow.child.completed",
        actor="dev",
        payload={
            "workflow_run_id": "RUN-1",
            "operation_id": context.operation_id,
            "attempt_id": context.dispatch_id,
            "status": "completed",
        },
    )
    runtime.event_writer.append(source)
    admitted = ZfEvent(
        type="workflow.operation.settled",
        actor="zf-cli",
        origin="kernel",
        task_id="TASK-1",
        payload={
            "workflow_run_id": "RUN-1",
            "operation_id": context.operation_id,
            "task_id": "TASK-1",
            "admitted_call_result_ref": {
                "ref": "artifacts/call-results/result.json",
                "source_event_id": source.id,
            },
        },
    )

    settle_task_attempt_result(runtime, admitted)

    current = TaskAttemptStore(
        state_dir / "task_attempts.json"
    ).current(run_id="RUN-1", task_id="TASK-1")
    assert current is not None
    assert current["status"] == "succeeded"
    succeeded = next(
        event
        for event in runtime.event_log.read_all()
        if event.type == "task.attempt.succeeded"
    )
    assert succeeded.payload["source_event_id"] == source.id
    assert succeeded.payload["admission_event_id"] == admitted.id
    assert succeeded.payload["reconciled_shadow_expiry"] is False


def test_admitted_plan_synth_result_settles_attempt(
    tmp_path: Path,
) -> None:
    runtime, _, state_dir = _runtime(tmp_path)
    context = _send(runtime, state_dir, dispatch_id="plan-synth-dispatch")
    assert context is not None
    source = ZfEvent(
        type="fanout.synth.completed",
        actor="plan-synth",
        task_id="TASK-1",
        payload={
            "workflow_run_id": "RUN-1",
            "operation_id": context.operation_id,
            "attempt_id": context.dispatch_id,
            "status": "completed",
            "report": {
                "status": "passed",
                "recommendation": "approve",
            },
        },
    )
    runtime.event_writer.append(source)
    admitted = ZfEvent(
        type="workflow.operation.settled",
        actor="zf-cli",
        origin="kernel",
        task_id="TASK-1",
        payload={
            "workflow_run_id": "RUN-1",
            "operation_id": context.operation_id,
            "task_id": "TASK-1",
            "admitted_call_result_ref": {
                "ref": "artifacts/call-results/plan-synth.json",
                "source_event_id": source.id,
            },
        },
    )

    settle_task_attempt_result(runtime, admitted)

    current = TaskAttemptStore(
        state_dir / "task_attempts.json"
    ).current(run_id="RUN-1", task_id="TASK-1")
    assert current is not None
    assert current["status"] == "succeeded"
    succeeded = [
        event
        for event in runtime.event_log.read_all()
        if event.type == "task.attempt.succeeded"
    ]
    assert len(succeeded) == 1
    assert succeeded[0].payload["source_event_id"] == source.id


def test_operation_service_settlement_reconciles_after_canonical_result(
    tmp_path: Path,
) -> None:
    runtime, _, state_dir = _runtime(tmp_path)
    context = _send(runtime, state_dir, dispatch_id="disp-1")
    assert context is not None
    service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=runtime.event_log,
        event_writer=EventWriter(runtime.event_log),
    )
    ensured = service.ensure_operation(
        workflow_run_id="RUN-1",
        operation_id=context.operation_id,
        operation_type="fanout_writer_child",
        request={"prompt": "implement"},
        task_id="TASK-1",
    )
    source = ZfEvent(
        type="workflow.child.completed",
        actor="dev",
        task_id="TASK-1",
        payload={
            "workflow_run_id": "RUN-1",
            "operation_id": context.operation_id,
            "dispatch_id": context.dispatch_id,
            "status": "completed",
        },
    )
    settled = service.settle(
        operation_id=context.operation_id,
        request_hash=ensured.request_hash,
        workflow_run_id="RUN-1",
        task_id="TASK-1",
        admitted_call_result_ref={
            "ref": "artifacts/call-results/result.json",
            "sha256": "a" * 64,
            "source_event_id": source.id,
        },
    )
    assert settled is not None
    assert settled.origin == "kernel"
    runtime.event_log.append(source)

    assert reconcile_task_attempts(runtime) == 1
    current = TaskAttemptStore(
        state_dir / "task_attempts.json"
    ).current(run_id="RUN-1", task_id="TASK-1")
    assert current is not None
    assert current["status"] == "succeeded"
    assert current["terminal_event_id"] == source.id


def test_reconcile_repairs_shadow_expiry_after_admitted_result(
    tmp_path: Path,
) -> None:
    runtime, _, state_dir = _runtime(tmp_path)
    context = _send(runtime, state_dir, dispatch_id="disp-1")
    assert context is not None
    store = TaskAttemptStore(state_dir / "task_attempts.json")
    current = store.current(run_id="RUN-1", task_id="TASK-1")
    assert current is not None
    store.update(
        current["attempt_id"],
        status="failed",
        updated_at="2026-07-27T00:00:00+00:00",
        failure_reason="lease_expired_shadow_only",
        failure_class="lease_expired",
        retryable=True,
        recovery_owner="scheduler",
    )
    source = ZfEvent(
        type="workflow.child.completed",
        actor="dev",
        payload={
            "workflow_run_id": "RUN-1",
            "operation_id": context.operation_id,
            "attempt_id": context.dispatch_id,
            "status": "completed",
        },
    )
    runtime.event_writer.append(source)
    admitted = ZfEvent(
        type="workflow.operation.settled",
        actor="zf-cli",
        origin="kernel",
        task_id="TASK-1",
        payload={
            "workflow_run_id": "RUN-1",
            "operation_id": context.operation_id,
            "task_id": "TASK-1",
            "admitted_call_result_ref": {
                "ref": "artifacts/call-results/result.json",
                "source_event_id": source.id,
            },
        },
    )
    runtime.event_writer.append(admitted)

    assert reconcile_task_attempts(runtime) == 1
    assert reconcile_task_attempts(runtime) == 0
    repaired = store.current(run_id="RUN-1", task_id="TASK-1")
    assert repaired is not None
    assert repaired["status"] == "succeeded"
    succeeded = next(
        event
        for event in runtime.event_log.read_all()
        if event.type == "task.attempt.succeeded"
    )
    assert succeeded.payload["reconciled_shadow_expiry"] is True


def test_semantic_failure_remains_owned_by_workflow_recovery(
    tmp_path: Path,
) -> None:
    runtime, _, state_dir = _runtime(tmp_path)
    context = _send(runtime, state_dir, dispatch_id="disp-1")
    assert context is not None
    result = ZfEvent(
        type="dev.failed",
        actor="dev",
        task_id="TASK-1",
        payload={
            **dispatch_attempt_payload(context),
            "reason": "acceptance failed",
        },
    )

    settle_task_attempt_result(runtime, result)

    current = TaskAttemptStore(
        state_dir / "task_attempts.json"
    ).current(run_id="RUN-1", task_id="TASK-1")
    assert current is not None
    assert current["status"] == "failed"
    assert current["failure_class"] == "semantic_result_failed"
    assert current["retryable"] is False
    assert current["recovery_owner"] == "workflow"
