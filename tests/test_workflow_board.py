from types import SimpleNamespace
import pytest
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.workflow_board import deliver_mentions, inbox, post
from zf.runtime.worker_reply_binding import claim_bound_notification


def seed(runtime, actor):
    op = "op-" + actor
    for kind, payload in [
        ("requested", {"operation_type": "task_pipeline", "operation_key": actor}),
        ("started", {}),
    ]:
        runtime.event_log.append(
            ZfEvent(
                type="workflow.operation." + kind,
                origin="kernel",
                task_id="task",
                payload={
                    "workflow_run_id": "r",
                    "operation_id": op,
                    "request_hash": "hash",
                    "role_instance": actor,
                    "active_attempt_id": actor,
                    "task_id": "task",
                    **payload,
                },
            )
        )
    return {"actor": actor, "attempt_id": actor, "operation_id": op}


def test_frozen_board_target_and_per_recipient_ack(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    board = {
        "enabled": True,
        "generation": "first",
        "members": ["A", "B", "C"],
        "groups": {"review": ["B"]},
    }
    runtime = SimpleNamespace(
        state_dir=tmp_path,
        event_log=log,
        event_writer=EventWriter(log),
        config=SimpleNamespace(
            workflow=SimpleNamespace(flow_metadata={"run_boards": {"r": board}})
        ),
    )
    a, b, c = (seed(runtime, actor) for actor in "ABC")
    msg = post(runtime, a, "1", ["@group/review"], "Inspect the published evidence.")
    board["groups"]["review"] = ["C"]
    assert (
        post(runtime, a, "1", ["@group/review"], "Inspect the published evidence.")
        == msg
    )
    deliver_mentions(runtime)
    deliver_mentions(runtime)
    wakes = [e for e in log.read_all() if e.type == "worker.reply.requested"]
    assert [w.payload["instance_id"] for w in wakes] == ["B"]
    assert not inbox(runtime, c, seen=msg["message_key"])[0]["consumed"]
    with pytest.raises(ValueError):
        inbox(runtime, c, acknowledge=msg["message_key"])
    assert inbox(runtime, b, acknowledge=msg["message_key"])[0]["consumed"]
    inbox(runtime, b, acknowledge=msg["message_key"])
    assert sum(e.type == "workflow.board.consumed" for e in log.read_all()) == 1
    claim_bound_notification(runtime, wakes[0], "B", wakes[0].payload)
    with pytest.raises(ValueError, match="prior send"):
        claim_bound_notification(runtime, wakes[0], "B", wakes[0].payload)
    board["generation"] = "reassigned"
    with pytest.raises(ValueError, match="audience changed"):
        claim_bound_notification(runtime, wakes[0], "B", wakes[0].payload)
    assert inbox(runtime, b) == []


def test_notification_reuses_current_operation_through_runtime_entrypoint(
    tmp_path, monkeypatch
):
    from zf.runtime.transport import DispatchContext
    from zf.runtime.task_attempt_continuation import validate_task_continuation
    from tests.test_worker_reply_binding import _runtime

    runtime, _, _, sends = _runtime(tmp_path, monkeypatch)
    actor = "dev-lane-2"
    runtime.config.workflow.flow_metadata["run_boards"] = {
        "run-1": {"enabled": True, "generation": "g", "members": [actor]}
    }
    own = {"actor": actor, "attempt_id": "attempt-1", "operation_id": "op-1"}
    post(runtime, own, "notice", [actor], "Inspect current evidence")
    deliver_mentions(runtime)
    request = next(
        e for e in runtime.event_log.read_all() if e.type == "worker.reply.requested"
    )
    monkeypatch.setattr(runtime.transport, "is_alive", lambda _: True)
    outcome = runtime._apply_worker_reply_request(
        request, instance_id=actor, payload=request.payload
    )
    assert outcome and len(sends) == 1
    context = sends[0][-1]
    assert (
        isinstance(context, DispatchContext)
        and context.continuation
        and context.operation_id == "op-1"
    )
    validate_task_continuation(runtime, actor, context)
    assert not (runtime.state_dir / "task_attempts.json").exists()
