"""Public Run messages for explicitly bound live operations, with per-recipient receipts."""

from __future__ import annotations

from pathlib import Path
from shlex import quote

from zf.core.events import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.call_result_envelope import (
    canonical_json_sha256,
    write_immutable_json_sidecar,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.runtime.worker_reply_binding import resolve_reply_operation
from zf.runtime.workflow_board_authority import board_for_run, board_policy_enabled, legacy_boards, permits

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


def current_board_identity(runtime, identity: dict, *, events=None, operations=None) -> tuple[dict, dict]:
    if events is None:
        events = runtime.event_log.read_all()
    if operations is None:
        operations = reduce_workflow_operations([e for e in events if e.origin == "kernel"])
    op = resolve_reply_operation(runtime, identity["actor"], identity, operations=operations)
    if not op or op.get("active_attempt_id") != identity["attempt_id"]:
        raise ValueError("board requires a current bound operation")
    board = board_for_run(runtime.state_dir, runtime.config, events, operations, op["workflow_run_id"])
    if not permits(board, op):
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
        offset = runtime.event_log.current_offset()
        events = runtime.event_log.read_all()
        operations = reduce_workflow_operations([e for e in events if e.origin == "kernel"])
        op, board = current_board_identity(runtime, identity, events=events, operations=operations)
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
            if prior.payload["generation"] != board["generation"] or prior.payload["request_digest"] != digest or prior.payload[
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
        audience = {}
        for who in sorted(targets):
            rows = [
                r
                for r in operations.values()
                if r.get("status") == "running"
                and r.get("role_instance") == who
                and r.get("workflow_run_id") == run
                and not r.get("admitted_call_result_ref")
                and permits(board, r)
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
        if "authorized_operations" in board:
            payload["viewer_operations"] = sorted(board["authorized_operations"])
        return runtime.event_writer.append(
            ZfEvent(
                type="workflow.board.posted",
                origin="kernel",
                actor=ACTOR,
                task_id=op.get("task_id"),
                correlation_id=run,
                causation_id=op.get("last_event_id"),
                payload=payload,
            ), expected_offset=offset, allow_intervening=frozenset({"agent.text", "worker.heartbeat"})
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
            or ("viewer_operations" in p and own["operation_id"] not in p["viewer_operations"])
        ):
            continue
        key = canonical_json_sha256([p["message_key"], own, board["generation"]])
        assigned = p["audience"].get(identity["actor"]) == own
        if (acknowledge and not assigned) or (
            (acknowledge or seen) and p["message_key"] != (acknowledge or seen)
        ):
            continue
        with locked_path(runtime.state_dir / "locks" / "run-board" / key):
            history = runtime.event_log.read_all()
            _, current = current_board_identity(runtime, identity, events=history)
            if current["generation"] != p["generation"]:
                continue
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
    if not legacy_boards(runtime.config) and not board_policy_enabled(runtime.config):
        return
    history = runtime.event_log.read_all()
    settled = {e.payload.get("notification_key") for e in history if e.origin == "kernel" and e.actor == ACTOR
               and e.type in {"worker.reply.requested", "workflow.board.expired"}}
    for e in history:
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
            if key in settled:
                continue
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
                    if not binding:
                        raise ValueError("unbound recipient")
                    op, board = current_board_identity(runtime, {**binding, "actor": actor})
                    if board["generation"] != p["generation"]:
                        raise ValueError("audience or generation expired")
                    if _binding(op) != binding:
                        raise ValueError("recipient changed")
                except ValueError as exc:
                    kind, fields = "workflow.board.expired", {"reason": str(exc)}
                else:
                    kind, fields = (
                        "worker.reply.requested",
                        {
                            **binding,
                            "message": f"Read the Run board with zf artifact board --attempt {quote(binding['attempt_id'])} "
                            f"--state-dir {quote(str(runtime.state_dir.resolve()))}. The notice carries no change to the current task contract.",
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
