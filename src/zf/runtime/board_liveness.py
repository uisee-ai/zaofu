"""Project a silent Run audience into the existing Supervisor -> Run Manager route."""

from datetime import datetime
from pathlib import Path

from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.fanout_idle_liveness import codex_turn_within_idle_grace
from zf.runtime.call_result_envelope import canonical_json_sha256
from zf.runtime.supervisor_attention import _attention_item
from zf.runtime.task_attempt_liveness import iso_epoch
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.runtime.workflow_board_authority import board_for_run, board_policy_enabled, legacy_boards, permits


def board_silence_attention(
    state: Path, config, events, now: datetime, paused=False, tasks=()
):
    metadata = getattr(getattr(config, "workflow", None), "flow_metadata", {}) or {}
    boards = dict(legacy_boards(config))
    dynamic = board_policy_enabled(config)
    if paused or (not dynamic and not boards):
        return []
    operations = reduce_workflow_operations([e for e in events if e.origin == "kernel"])
    if dynamic:
        runs = {op["workflow_run_id"] for op in operations.values()
                if op.get("role_instance") and op.get("status") == "running" and not op.get("admitted_call_result_ref")}
        runs.update(t.execution_binding.workflow_run_id for t in tasks
                    if t.status in {"todo", "sprint", "in_progress", "ready"} and t.execution_binding.workflow_run_id)
        for run in runs - boards.keys():
            try:
                boards[run] = board_for_run(state, config, events, operations, run)
            except (OSError, ValueError):
                # Only a Run with an available, currently admitted authority is observable here.
                continue
    leases = TaskAttemptStore(state / "task_attempts.json").current_rows()
    now_epoch = now.timestamp()
    items = []
    for run, board in boards.items():
        if (
            not isinstance(board, dict)
            or board.get("enabled") is not True
            or not board.get("generation")
        ):
            continue
        threshold = board.get("silence_seconds")
        if type(threshold) is not int or threshold < 1:
            continue
        roster = board.get("members")
        if (
            not isinstance(roster, list)
            or (not roster and "authorized_operations" not in board)
            or not all(isinstance(m, str) and m for m in roster)
        ):
            continue
        members = set(roster)
        pending = {
            k: op
            for k, op in operations.items()
            if op.get("workflow_run_id") == run
            and permits(board, op)
            and op.get("status") in {"requested", "reserved", "running"}
            and not op.get("admitted_call_result_ref")
        }
        unfinished = [
            t
            for t in tasks
            if t.execution_binding.workflow_run_id == run
            and t.status in {"in_progress", "sprint", "todo", "ready"}
            and (not t.assigned_to or t.assigned_to in members)
        ]
        if not pending and not unfinished:
            continue
        scoped = [
            e
            for e in events
            if e.payload.get("workflow_run_id") == run
            or e.payload.get("run_id") == run
            or e.correlation_id == run
        ]
        terminal = next(
            (
                e
                for e in reversed(scoped)
                if e.origin == "kernel"
                and e.type
                in {
                    "run.paused",
                    "run.resumed",
                    "run.completed",
                    "run.archived",
                    "run.abandoned",
                }
            ),
            None,
        )
        if terminal and terminal.type != "run.resumed":
            continue
        if any(
            l.get("run_id") == run
            and l.get("instance_id") in members
            and l.get("status") in {"prepared", "delivering", "sent"}
            and iso_epoch(l.get("lease_expires_at")) > now_epoch
            for l in leases
        ):
            continue
        if any(
            iso_epoch(op.get("reservation_expires_at")) > now_epoch
            for op in pending.values()
        ):
            continue
        activities = []
        charges = []
        for e in scoped:
            p = e.payload
            if e.type == "agent.usage":
                charges.append(e.id)
            if e.origin == "kernel" and (
                e.type.startswith("workflow.board.")
                and e.type != "workflow.board.expired"
                or (
                    p.get("operation_id") in pending
                    and e.type.startswith("workflow.operation.")
                )
                or (
                    e.type in {"provider.turn.opened", "provider.turn.closed"}
                    and (e.actor in members or p.get("instance_id") in members)
                )
            ):
                activities.append(e)
            elif (
                e.type == "worker.heartbeat"
                and (e.actor in members or p.get("instance_id") in members)
                and p.get("operation_id") in pending
            ):
                activities.append(e)
        last_time = iso_epoch(activities[-1].ts) if activities else 0
        last_time = max(
            [last_time]
            + [
                iso_epoch(t.started_at or t.dispatched_at or t.created_at)
                for t in unfinished
            ]
        )
        if not last_time or now_epoch - last_time < threshold:
            continue
        first_by_op = {
            op: next(
                (
                    iso_epoch(e.ts)
                    for e in scoped
                    if e.payload.get("operation_id") == op
                ),
                now_epoch,
            )
            for op in pending
        }
        if any(
            codex_turn_within_idle_grace(
                events,
                instance_id=op["role_instance"],
                dispatch_epoch=first_by_op[key],
                last_activity_epoch=last_time,
                idle_threshold=threshold,
                now=now_epoch,
            )
            for key, op in pending.items()
        ):
            continue
        # A live authorized deadline is an intentional wait. An overdue wait remains diagnosable.
        waits = {
            d.get("operation_id")
            for d in metadata.get("orchestration_schedules", [])
            if isinstance(d, dict)
            and d.get("enabled") is True
            and d.get("action") == "notify"
            and d.get("workflow_run_id") == run
            and d.get("operation_id") in pending
            and d.get("request_hash") == pending[d["operation_id"]].get("request_hash")
            and d.get("actor") == pending[d["operation_id"]].get("role_instance")
            and d.get("attempt_id")
            == pending[d["operation_id"]].get("active_attempt_id")
            and (not d.get("expires_at") or iso_epoch(d["expires_at"]) > now_epoch)
            and iso_epoch(d.get("effective_at"))
            <= now_epoch
            < iso_epoch(d.get("deadline"))
        }
        if (
            pending
            and waits == set(pending)
            and all(
                t.id in {op.get("task_id") for op in pending.values()}
                for t in unfinished
            )
        ):
            continue
        fingerprint = canonical_json_sha256(
            [
                run,
                board["generation"],
                sorted(pending),
                sorted(t.id for t in unfinished),
                activities[-1].id if activities else last_time,
            ]
        )
        item = _attention_item(
            source="run_board_silence",
            fingerprint="board-silence:" + fingerprint,
            severity="high",
            title="Run board members have no recent progress",
            summary=f"{len(pending)} unfinished operations and {len(unfinished)} assigned tasks have no recent progress for {threshold}s. Cause unknown.",
            source_event_ids=[activities[-1].id] if activities else [],
            source_ref="kanban.json" if unfinished else "",
            suggested_route="run_manager_recovery",
            suggested_action={
                "kind": "diagnose_attention",
                "workflow_run_id": run,
                "members": sorted(members),
                "operation_ids": sorted(pending),
                "task_ids": sorted(t.id for t in unfinished),
                "last_usage_event_id": charges[-1] if charges else "",
                "expected_output": [
                    "diagnosis_report",
                    "recommended_run_manager_action",
                ],
                "authorizes_retry": False,
            },
        )
        item["expected_output"] = [
            "diagnosis_report",
            "recommended_run_manager_action",
            "evidence_refs",
            "expected_downstream_event",
        ]
        items.append(item)
    return items
