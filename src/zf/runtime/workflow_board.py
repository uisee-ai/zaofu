"""Public Run messages for explicitly bound live operations, with per-recipient receipts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.call_result_envelope import (
    canonical_json_sha256,
    write_immutable_json_sidecar,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.runtime.worker_reply_binding import resolve_reply_operation

ACTOR = "workflow-board"


def _binding(op: dict) -> dict:
    return {
        "workflow_run_id": op["workflow_run_id"],
        "operation_id": op["operation_id"],
        "attempt_id": op["active_attempt_id"],
        "instance_id": op["role_instance"],
        "request_hash": op["request_hash"],
        "lease_id": op.get("lease_id") or "",
        "task_id": op.get("task_id") or "",
    }


def _config(config: Any, run: str) -> dict:
    boards = (
        getattr(getattr(config, "workflow", None), "flow_metadata", {}) or {}
    ).get("run_boards", {})
    board = boards.get(run, {}) if isinstance(boards, dict) else {}
    if (
        board.get("enabled") is not True
        or not isinstance(board.get("members"), list)
        or not board.get("generation")
    ):
        raise ValueError("this run has no enabled board with a bound audience")
    return board


def current_board_identity(runtime, identity: dict) -> tuple[dict, dict]:
    op = resolve_reply_operation(runtime, identity["actor"], identity)
    if not op or op.get("active_attempt_id") != identity["attempt_id"]:
        raise ValueError("board requires a current bound operation")
    board = _config(runtime.config, op["workflow_run_id"])
    if identity["actor"] not in board["members"]:
        raise ValueError("worker outside the authorized board audience")
    return op, board


def post(
    runtime, identity: dict, message_id: str, recipients: list[str], text: str
) -> dict:
    op, board = current_board_identity(runtime, identity)
    if (
        not message_id.strip()
        or len(message_id) > 200
        or not text.strip()
        or len(text.encode()) > 16384
    ):
        raise ValueError("invalid board message")
    run = op["workflow_run_id"]
    key = canonical_json_sha256(
        [run, op["operation_id"], identity["actor"], message_id]
    )
    with locked_path(runtime.state_dir / "locks" / "run-board" / key):
        op, board = current_board_identity(runtime, identity)
        events = runtime.event_log.read_all()
        digest = canonical_json_sha256([text, sorted(set(recipients))])
        prior = next(
            (
                e
                for e in events
                if e.origin == "kernel"
                and e.actor == ACTOR
                and e.type == "workflow.board.posted"
                and e.payload.get("message_key") == key
            ),
            None,
        )
        if prior:
            if prior.payload["request_digest"] != digest or prior.payload[
                "sender"
            ] != _binding(op):
                raise ValueError(
                    "message ID already belongs to a different post or attempt"
                )
            return prior.payload
        members = set(board["members"])
        targets = set()
        for target in recipients:
            if target == "@all":
                targets.update(members)
            elif target.startswith("@group/"):
                group = board.get("groups", {}).get(target.removeprefix("@group/"))
                if not isinstance(group, list):
                    raise ValueError("unknown recipient group")
                targets.update(group)
            else:
                targets.add(target)
        if not targets or not targets <= members:
            raise ValueError("recipient outside this run board")
        operations = reduce_workflow_operations(events)
        audience = {}
        for who in sorted(targets):
            rows = [
                r
                for r in operations.values()
                if r.get("status") == "running"
                and r.get("role_instance") == who
                and r.get("workflow_run_id") == run
                and not r.get("admitted_call_result_ref")
            ]
            audience[who] = _binding(rows[0]) if len(rows) == 1 else None
        ref = write_immutable_json_sidecar(
            runtime.state_dir,
            {"text": text},
            root="run-board",
            kind="board_message",
            schema_version="board-message.v1",
            created_by=ACTOR,
        )
        payload = {
            "message_key": key,
            "message_id": message_id,
            "workflow_run_id": run,
            "generation": board["generation"],
            "request_digest": digest,
            "sender": _binding(op),
            "audience": audience,
            "viewers": sorted(members),
            "body_ref": ref,
        }
        return runtime.event_writer.append(
            ZfEvent(
                type="workflow.board.posted",
                origin="kernel",
                actor=ACTOR,
                task_id=op.get("task_id"),
                correlation_id=run,
                causation_id=op.get("last_event_id"),
                payload=payload,
            )
        ).payload


def inbox(runtime, identity: dict, *, acknowledge: str = "", seen: str = "") -> list:
    op, board = current_board_identity(runtime, identity)
    own = _binding(op)
    rows = []
    for e in runtime.event_log.read_all():
        p = e.payload
        if (
            e.origin != "kernel"
            or e.actor != ACTOR
            or e.type != "workflow.board.posted"
            or p["workflow_run_id"] != own["workflow_run_id"]
            or p["generation"] != board["generation"]
            or identity["actor"] not in p["viewers"]
        ):
            continue
        key = canonical_json_sha256([p["message_key"], own, board["generation"]])
        assigned = p["audience"].get(identity["actor"]) == own
        if (acknowledge and not assigned) or (
            (acknowledge or seen) and p["message_key"] != (acknowledge or seen)
        ):
            continue
        with locked_path(runtime.state_dir / "locks" / "run-board" / key):
            current_board_identity(runtime, identity)
            history = runtime.event_log.read_all()
            viewed = any(
                x.type == "workflow.board.seen"
                and x.actor == ACTOR
                and x.origin == "kernel"
                and x.payload.get("delivery_key") == key
                for x in history
            )
            if seen and not viewed:
                runtime.event_writer.append(
                    ZfEvent(
                        type="workflow.board.seen",
                        actor=ACTOR,
                        origin="kernel",
                        causation_id=e.id,
                        correlation_id=p["workflow_run_id"],
                        payload={"delivery_key": key, "receiver": own},
                    )
                )
                viewed = True
            acknowledged = any(
                x.origin == "kernel"
                and x.actor == ACTOR
                and x.type == "workflow.board.consumed"
                and x.payload.get("delivery_key") == key
                for x in history
            )
            if acknowledge and not acknowledged:
                runtime.event_writer.append(
                    ZfEvent(
                        type="workflow.board.consumed",
                        actor=ACTOR,
                        origin="kernel",
                        causation_id=e.id,
                        correlation_id=p["workflow_run_id"],
                        payload={
                            "delivery_key": key,
                            "message_key": p["message_key"],
                            "receiver": own,
                        },
                    )
                )
                acknowledged = True
        rows.append(
            {
                "key": p["message_key"],
                "sender": p["sender"]["instance_id"],
                "addressed": assigned,
                "seen": viewed,
                "consumed": acknowledged,
                "body": hydrate_sidecar_ref(runtime.state_dir, p["body_ref"]).payload[
                    "text"
                ],
            }
        )
    if (acknowledge or seen) and not rows:
        raise ValueError("unknown message for this receiver")
    return rows


def deliver_mentions(runtime):
    metadata = (
        getattr(getattr(runtime.config, "workflow", None), "flow_metadata", {}) or {}
    )
    if not metadata.get("run_boards"):
        return
    for e in runtime.event_log.read_all():
        if (
            e.origin != "kernel"
            or e.actor != ACTOR
            or e.type != "workflow.board.posted"
        ):
            continue
        p = e.payload
        for actor, binding in p["audience"].items():
            key = canonical_json_sha256(
                [p["message_key"], binding, actor, p["generation"]]
            )
            with locked_path(Path(runtime.state_dir) / "locks" / "run-board" / key):
                if any(
                    x.origin == "kernel"
                    and x.actor == ACTOR
                    and x.payload.get("notification_key") == key
                    and x.type in {"worker.reply.requested", "workflow.board.expired"}
                    for x in runtime.event_log.read_all()
                ):
                    continue
                try:
                    board = _config(runtime.config, p["workflow_run_id"])
                    if (
                        not binding
                        or actor not in board["members"]
                        or board["generation"] != p["generation"]
                    ):
                        raise ValueError("audience or generation expired")
                    op = resolve_reply_operation(runtime, actor, binding)
                    if _binding(op) != binding:
                        raise ValueError("recipient changed")
                except ValueError as exc:
                    kind, fields = "workflow.board.expired", {"reason": str(exc)}
                else:
                    kind, fields = (
                        "worker.reply.requested",
                        {
                            **binding,
                            "message": f"Read the Run board with zf artifact board --attempt {binding['attempt_id']}. "
                            "The notice carries no change to the current task contract.",
                        },
                    )
                runtime.event_writer.append(
                    ZfEvent(
                        type=kind,
                        origin="kernel",
                        actor=ACTOR,
                        causation_id=e.id,
                        correlation_id=p["workflow_run_id"],
                        task_id=(binding or {}).get("task_id"),
                        payload={
                            **fields,
                            "instance_id": actor,
                            "notification_key": key,
                            "message_key": p["message_key"],
                            "generation": p["generation"],
                        },
                    )
                )
