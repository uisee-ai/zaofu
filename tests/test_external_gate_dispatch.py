"""External provider gates are waits; unknown delivery remains a failure."""
import pytest

from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxError
from zf.runtime.tmux_readiness import build_tmux_readiness_error
from tests.test_reader_fanout_runtime import _state, _start_fanout, _manifest
from tests.test_scheduler_task_attempt import _runtime, _send


def _blocked(marker="usage_limit_reached", *, unsent=True):
    return build_tmux_readiness_error("reader", {
        "failure_class": "provider_interactive_blocked",
        "interactive_prompt_marker": marker, "pane_alive": True,
        "input_not_sent": unsent,
    })


@pytest.mark.parametrize("marker", ["usage_limit_reached", "login_required", "trust_prompt"])
def test_initial_reader_external_gate_waits_then_dispatches_once(tmp_path, monkeypatch, marker):
    state, log, transport, orch = _state(tmp_path)
    orch.config.workflow.flow_metadata["result_protocol"] = {"mode": "blocking"}
    send = transport.send_task

    def block(*args, **kwargs):
        raise _blocked(marker)

    monkeypatch.setattr(transport, "send_task", block)
    _start_fanout(orch)
    for _ in range(5):
        orch._recover_pending_reader_fanout_dispatches(log.read_all())
    assert not any(e.type == "fanout.child.failed" for e in log.read_all())
    requested = [e.payload["operation_id"] for e in log.read_all()
                 if e.type == "workflow.operation.requested"]
    assert len(requested) == 2
    assert not transport.sent
    # Restart the controller while still gated; recover from persisted state.
    orch = Orchestrator(state, orch.config, transport)
    orch._recover_pending_reader_fanout_dispatches(log.read_all())
    assert not any(e.type == "fanout.child.failed" for e in log.read_all())
    monkeypatch.setattr(transport, "send_task", send)
    for _ in range(2):
        orch._recover_pending_reader_fanout_dispatches(log.read_all())
    dispatched = [e for e in log.read_all() if e.type == "fanout.child.dispatched"]
    assert len(dispatched) == len(transport.sent) == 2
    assert {e.payload.get("operation_id") or e.payload["payload"]["operation_id"]
            for e in dispatched} == set(requested)


def test_reader_retry_external_gate_keeps_retry_nonterminal(tmp_path, monkeypatch):
    state, log, transport, orch = _state(tmp_path)
    _start_fanout(orch)
    previous = next(e for e in log.read_all() if e.type == "fanout.child.dispatched")
    manifest = _manifest(state, previous.payload["fanout_id"])
    child = next(c for c in manifest["children"] if c["child_id"] == previous.payload["child_id"])
    monkeypatch.setattr(orch, "_ensure_fanout_role_dispatchable", lambda **kw: True)
    send = orch._send_scoped_transport_task

    def block(*args, **kwargs):
        raise _blocked()

    monkeypatch.setattr(orch, "_send_scoped_transport_task", block)
    for _ in range(5):
        assert not orch._retry_fanout_child(manifest=manifest, child=child,
            previous_dispatch=previous, attempt=1, provider_session_replaced=True)
    assert not any(e.type == "fanout.child.failed" for e in log.read_all())
    assert any(e.type == "fanout.child.dispatch_deferred" for e in log.read_all())
    monkeypatch.setattr(orch, "_send_scoped_transport_task", send)
    assert orch._retry_fanout_child(manifest=manifest, child=child,
        previous_dispatch=previous, attempt=1, provider_session_replaced=True)
    assert len([e for e in log.read_all() if e.type == "fanout.child.dispatched"
                and e.payload.get("retry_of_run_id") == previous.payload["run_id"]]) == 1


def test_unsent_interactive_delivery_reuses_attempt_after_gate_clears(tmp_path, monkeypatch):
    runtime, transport, state = _runtime(tmp_path, mode="enforce")
    send = transport.send_task

    def block(*args, **kwargs):
        raise _blocked()

    monkeypatch.setattr(transport, "send_task", block)
    store = TaskAttemptStore(state / "task_attempts.json")
    for _ in range(5):
        with pytest.raises(TmuxError, match="provider readiness failed"):
            _send(runtime, state, dispatch_id="same-dispatch")
        row = store.current(run_id="RUN-1", task_id="TASK-1")
        assert row["status"] == "prepared"
        assert row["ordinal"] == 1
    assert not any(e.type == "task.attempt.failed" for e in runtime.event_log.read_all())
    monkeypatch.setattr(transport, "send_task", send)
    context = _send(runtime, state, dispatch_id="same-dispatch")
    assert context.attempt_id == row["attempt_id"]
    assert store.get(context.attempt_id)["status"] == "sent"


def test_unproven_delivery_failure_is_not_reset(tmp_path, monkeypatch):
    runtime, transport, state = _runtime(tmp_path, mode="enforce")

    def ambiguous(*args, **kwargs):
        raise _blocked(unsent=False)

    monkeypatch.setattr(transport, "send_task", ambiguous)
    with pytest.raises(TmuxError, match="provider readiness failed"):
        _send(runtime, state, dispatch_id="ambiguous")
    row = TaskAttemptStore(state / "task_attempts.json").current(run_id="RUN-1", task_id="TASK-1")
    assert row["status"] == "failed"


@pytest.mark.parametrize("concurrent_change", ["sent", "superseded"])
def test_external_gate_never_rewinds_changed_delivery(tmp_path, monkeypatch, concurrent_change):
    from zf.runtime.task_attempt_runtime import TaskAttemptDeliveryClaimedError
    runtime, transport, state = _runtime(tmp_path, mode="enforce")
    store = TaskAttemptStore(state / "task_attempts.json")
    before = []

    def raced(role, briefing, prompt, *, context):
        row = store.get(context.attempt_id)
        if concurrent_change == "sent":
            store.mark_sent(context.attempt_id, updated_at=row["updated_at"])
        else:
            store.ensure_for_dispatch(run_id=row["run_id"], task_id=row["task_id"],
                dispatch_id="next-dispatch", role=row["role"], instance_id=row["instance_id"],
                operation_id=row["operation_id"], briefing_ref=row["briefing_ref"],
                created_at=row["created_at"], lease_expires_at=row["lease_expires_at"], max_attempts=3)
        before.append(store.load())
        raise _blocked()

    monkeypatch.setattr(transport, "send_task", raced)
    with pytest.raises(TaskAttemptDeliveryClaimedError, match="cannot release"):
        _send(runtime, state, dispatch_id="raced")
    assert store.load() == before[0]


@pytest.mark.parametrize("stage", ["impl", "verify"])
@pytest.mark.parametrize("phase", ["activation", "delivery"])
def test_pipeline_gate_wait_does_not_trip_dispatch_circuit(tmp_path, monkeypatch, stage, phase):
    from unittest.mock import Mock
    from tests.e2e.test_verification_eight_slots_mock_e2e import _runtime as pipeline_runtime
    from zf.runtime.task_pipeline_runtime import reconcile_task_pipeline_runtime
    from zf.runtime.workflow_operation import reduce_workflow_operations
    runtime, delivered, _ = pipeline_runtime(tmp_path, monkeypatch, stage, task_count=2)
    method = "_ensure_role_active" if phase == "activation" else "_send_transport_task"
    send = getattr(runtime, method)
    failure = Mock(wraps=runtime._record_dispatch_failure)
    monkeypatch.setattr(runtime, "_record_dispatch_failure", failure)

    def block(*args, **kwargs):
        raise _blocked()

    monkeypatch.setattr(runtime, method, block)
    for _ in range(5):
        reconcile_task_pipeline_runtime(runtime)
    failure.assert_not_called()
    assert not delivered
    requested = reduce_workflow_operations(runtime.event_log.read_all())
    assert len(requested) == (0 if phase == "activation" else 2)
    monkeypatch.setattr(runtime, method, send)
    reconcile_task_pipeline_runtime(runtime)
    assert len(delivered) == 2
    current = reduce_workflow_operations(runtime.event_log.read_all())
    if requested:
        assert set(current) == set(requested)
    assert all(op["status"] == "running" for op in current.values())
