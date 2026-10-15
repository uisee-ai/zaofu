from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_result_receipts import (
    reconcile_channel_result_receipts,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _runtime(tmp_path: Path) -> tuple[Path, EventLog, EventWriter]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    return state_dir, log, EventWriter(log)


def _channel(writer: EventWriter, *, channel_id: str = "ch-product") -> None:
    writer.emit(
        "channel.created",
        actor="web",
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "name": "Product",
            "source": "web",
        },
    )
    writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": "main",
            "message_id": "msg-origin",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "Build the approved product.",
        },
    )


def _authority() -> dict[str, object]:
    return {
        "channel_id": "ch-product",
        "thread_id": "main",
        "channel_member_id": "product-pm",
        "leader_revision": 1,
        "prd_revision": 2,
        "source_ref": "channels/ch-product/prd/r2.json",
        "source_digest": "a" * 64,
    }


def test_reconciles_prd_task_run_and_delivery_to_exact_origin(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _runtime(tmp_path)
    _channel(writer)
    writer.emit(
        "channel.consensus.reached",
        actor="owner:operator",
        correlation_id="ch-product",
        payload={
            "channel_id": "ch-product",
            "thread_id": "main",
            "artifact_ref": "channels/ch-product/prd/r2.json",
            "artifact_digest": "a" * 64,
            "prd_ref": "channels/ch-product/prd/r2.json",
            "prd_digest": "a" * 64,
            "prd_revision": 2,
            "source": "web",
        },
    )
    writer.emit(
        "task.created",
        actor="web",
        task_id="TASK-CHANNEL",
        correlation_id="trace-channel",
        payload={
            "source": "channel",
            "task": {"id": "TASK-CHANNEL"},
            "request": {
                "channel_authority": _authority(),
                "source_artifact": {
                    "kind": "channel_prd",
                    "ref": "channels/ch-product/prd/r2.json",
                    "digest": "a" * 64,
                    "revision": 2,
                },
            },
            "proposal_event_id": "evt-proposal",
        },
    )
    writer.emit(
        "run.goal.completed",
        actor="orchestrator",
        task_id="TASK-CHANNEL",
        correlation_id="run-channel",
        payload={
            "run_id": "run-channel",
            "summary": "Goal completed.",
        },
    )
    writer.emit(
        "ship.completed",
        actor="orchestrator",
        task_id="TASK-CHANNEL",
        correlation_id="run-channel",
        payload={
            "run_id": "run-channel",
            "delivery_id": "delivery-channel",
            "delivery_ref": "artifacts/delivery-channel.json",
            "delivery_digest": "b" * 64,
        },
    )

    result = reconcile_channel_result_receipts(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
    )

    assert result.recorded == 4
    detail = project_channel(state_dir, "ch-product")
    assert detail is not None
    receipts = detail["result_receipts"]
    assert {
        item["receipt_kind"] for item in receipts
    } == {"prd_confirmed", "task_created", "workflow_terminal", "delivery_terminal"}
    assert {item["thread_id"] for item in receipts} == {"main"}
    prd_receipt = next(
        item for item in receipts if item["receipt_kind"] == "prd_confirmed"
    )
    assert prd_receipt["artifact_digest"] == "a" * 64
    assert prd_receipt["revision"] == 2
    assert prd_receipt["links"]["prd_ref"] == (
        "channels/ch-product/prd/r2.json"
    )
    for item in receipts:
        hydrated = hydrate_sidecar_ref(
            state_dir,
            {
                "ref": item["receipt_ref"],
                "sha256": item["receipt_digest"],
            },
        )
        assert hydrated.payload["channel_id"] == "ch-product"
        assert hydrated.payload["thread_id"] == "main"


def test_receipt_reconcile_is_restart_safe_and_idempotent(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _runtime(tmp_path)
    _channel(writer)
    writer.emit(
        "task.created",
        actor="web",
        task_id="TASK-CHANNEL",
        payload={
            "source": "channel",
            "task": {"id": "TASK-CHANNEL"},
            "request": {"channel_authority": _authority()},
            "proposal_event_id": "evt-proposal",
        },
    )

    first = reconcile_channel_result_receipts(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
    )
    second = reconcile_channel_result_receipts(
        state_dir=state_dir,
        event_log=EventLog(state_dir / "events.jsonl"),
        writer=EventWriter(EventLog(state_dir / "events.jsonl")),
    )

    assert first.recorded == 1
    assert second.recorded == 0
    events = log.read_all()
    assert sum(
        event.type == "channel.result.receipt.recorded"
        for event in events
    ) == 1
    assert (
        state_dir
        / "projections"
        / "channel-result-receipts"
        / "cursor.json"
    ).exists()


@pytest.mark.parametrize("legacy_receipt", [False, True])
def test_same_prd_tasks_have_distinct_restart_safe_receipts(
    tmp_path: Path, legacy_receipt: bool,
) -> None:
    state_dir, log, writer = _runtime(tmp_path)
    _channel(writer)
    for task_id in ("TASK-ORIGINAL", "TASK-REPLACEMENT"):
        source = writer.emit(
            "task.created",
            actor="web",
            task_id=task_id,
            payload={
                "source": "channel",
                "task": {"id": task_id},
                "request": {"channel_authority": _authority()},
                "proposal_event_id": f"proposal-{task_id}",
            },
        )
        if legacy_receipt and task_id == "TASK-ORIGINAL":
            writer.emit(
                "channel.result.receipt.recorded",
                actor="zf-channel-result-reconciler",
                task_id=task_id,
                causation_id=source.id,
                payload={
                    "channel_id": "ch-product", "thread_id": "main",
                    "receipt_kind": "task_created", "task_id": task_id,
                    "source_event_id": source.id,
                    "idempotency_key": f"ch-product|main|task_created|{'a' * 64}|2",
                },
            )

    first = reconcile_channel_result_receipts(
        state_dir=state_dir, event_log=log, writer=writer,
    )
    assert first.recorded == (1 if legacy_receipt else 2)
    reopened = EventLog(state_dir / "events.jsonl")
    assert reconcile_channel_result_receipts(
        state_dir=state_dir, event_log=reopened, writer=EventWriter(reopened),
    ).recorded == 0
    receipts = [e for e in reopened.read_all()
                if e.type == "channel.result.receipt.recorded"]
    assert len(receipts) == 2
    assert {e.payload["task_id"] for e in receipts} == {
        "TASK-ORIGINAL", "TASK-REPLACEMENT",
    }


def test_feishu_origin_is_bound_to_the_exact_root_message(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _runtime(tmp_path)
    writer.emit(
        "channel.created",
        actor="feishu",
        correlation_id="ch-product",
        payload={
            "channel_id": "ch-product",
            "name": "Product",
            "source": "feishu",
        },
    )
    writer.emit(
        "channel.message.posted",
        actor="feishu:owner",
        correlation_id="ch-product",
        payload={
            "channel_id": "ch-product",
            "thread_id": "om-root",
            "message_id": "om-child",
            "member_id": "owner",
            "role": "user",
            "source": "feishu",
            "text": "Build the approved product.",
            "refs": {
                "feishu": {
                    "chat_id": "oc-product",
                    "message_id": "om-child",
                    "root_message_id": "om-root",
                    "thread_id": "om-root",
                },
            },
        },
    )
    writer.emit(
        "channel.consensus.reached",
        actor="owner:owner",
        correlation_id="ch-product",
        payload={
            "channel_id": "ch-product",
            "thread_id": "om-root",
            "artifact_ref": "channels/ch-product/prd/r1.json",
            "artifact_digest": "a" * 64,
            "prd_revision": 1,
            "source": "feishu",
        },
    )

    result = reconcile_channel_result_receipts(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
    )

    assert result.recorded == 1
    receipt = next(
        event
        for event in log.read_all()
        if event.type == "channel.result.receipt.recorded"
    )
    assert receipt.payload["origin_binding"] == {
        "schema_version": "channel-origin-binding.v1",
        "surface": "feishu",
        "channel_id": "ch-product",
        "thread_id": "om-root",
        "chat_id": "oc-product",
        "origin_message_id": "om-root",
        "root_message_id": "om-root",
        "source_message_id": "om-child",
    }


def test_distinct_terminal_events_do_not_reuse_the_source_prd_identity(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _runtime(tmp_path)
    _channel(writer)
    writer.emit(
        "task.created",
        actor="web",
        task_id="TASK-CHANNEL",
        payload={
            "source": "channel",
            "task": {"id": "TASK-CHANNEL"},
            "request": {"channel_authority": _authority()},
            "proposal_event_id": "evt-proposal",
        },
    )
    writer.emit(
        "run.goal.blocked",
        actor="orchestrator",
        task_id="TASK-CHANNEL",
        correlation_id="run-channel",
        payload={"run_id": "run-channel", "summary": "Awaiting repair."},
    )
    writer.emit(
        "run.goal.completed",
        actor="orchestrator",
        task_id="TASK-CHANNEL",
        correlation_id="run-channel",
        payload={"run_id": "run-channel", "summary": "Repair completed."},
    )

    result = reconcile_channel_result_receipts(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
    )

    assert result.recorded == 3
    terminal = [
        event
        for event in log.read_all()
        if event.type == "channel.result.receipt.recorded"
        and event.payload.get("receipt_kind") == "workflow_terminal"
    ]
    assert len(terminal) == 2
    assert {
        event.payload["status"] for event in terminal
    } == {"blocked", "completed"}
    assert len({
        event.payload["artifact_digest"] for event in terminal
    }) == 2


def test_missing_exact_origin_fails_bounded_then_escalates(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _runtime(tmp_path)
    writer.emit(
        "task.created",
        actor="web",
        task_id="TASK-MISSING",
        payload={
            "source": "channel",
            "task": {"id": "TASK-MISSING"},
            "request": {
                "channel_authority": {
                    **_authority(),
                    "channel_id": "ch-missing",
                },
            },
            "proposal_event_id": "evt-proposal",
        },
    )

    for _ in range(4):
        reconcile_channel_result_receipts(
            state_dir=state_dir,
            event_log=log,
            writer=writer,
        )

    events = log.read_all()
    assert sum(
        event.type == "channel.result.receipt.failed"
        for event in events
    ) == 3
    attention = [
        event
        for event in events
        if event.type == "runtime.attention.needed"
        and event.payload.get("title")
        == "channel.result.receipt.delivery_failed"
    ]
    assert len(attention) == 1
    assert attention[0].payload["source_ref"].startswith("channel-receipt:")
