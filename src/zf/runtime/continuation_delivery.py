"""Reconcile uncertain continuation delivery from exact provider observations."""
from __future__ import annotations

from dataclasses import replace
import hashlib
from typing import Any

from zf.core.events import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.task_attempt_runtime import task_attempt_store


def prepare_continuation_delivery(runtime: Any, context, prompt: str, claim_id: str):
    if not claim_id:
        return context
    context = replace(context, continuation_id=claim_id)
    runtime.event_writer.append(ZfEvent(type="worker.continuation.prepared", origin="kernel",
        actor=context.instance_id, task_id=context.task_id, causation_id=claim_id,
        payload={**context.to_payload(), "claim_id": claim_id,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}))
    return context


def reconcile_continuation_deliveries(runtime: Any) -> int:
    """Observations close an unknown delivery, never a business result or retry.

    No matching receipt means unknown, including a crash before preparation.
    This sweep can record evidence while dispatch is paused; it sends no prompt.
    """
    count = 0
    with locked_path(runtime.state_dir / "locks" / "continuation-receipts"):
        from zf.runtime.workflow_operation import reduce_workflow_operations
        events = runtime.event_log.read_all()
        if any(e.type == "event.malformed" and e.payload.get("ledger_integrity") == "unknown" for e in events):
            return 0
        operations = reduce_workflow_operations(events)
        resolved = {e.payload.get("claim_id") for e in events
                    if e.type == "worker.continuation.reconciled" and e.origin == "kernel"}
        claims = {e.id: e for e in events if e.type == "worker.continuation.claimed" and e.origin == "kernel"}
        for index, prepared in enumerate(events):
            if prepared.type != "worker.continuation.prepared" or prepared.origin != "kernel":
                continue
            payload = prepared.payload
            claim_id = payload.get("claim_id")
            claim = claims.get(claim_id)
            if claim is None or claim_id in resolved or claim.actor != prepared.actor or claim.task_id != prepared.task_id:
                continue
            row = task_attempt_store(runtime).get(payload.get("attempt_id", "")) or {}
            task = runtime.task_store.get(prepared.task_id or "")
            operation = operations.get(payload.get("operation_id"), {})
            if (operation.get("status") != "running"
                    or operation.get("active_attempt_id") != payload.get("attempt_id")
                    or row.get("status") != "sent" or task is None or task.status != "in_progress"
                    or task.assigned_to != prepared.actor or task.active_dispatch_id != payload.get("dispatch_id")
                    or any(not payload.get(k) or payload[k] != row.get(k)
                           for k in ("task_id", "instance_id", "operation_id", "attempt_id", "dispatch_id", "lease_id"))
                    or any(claim.payload.get(k) != payload.get(k) for k in ("attempt_id", "dispatch_id"))):
                continue
            for receipt in events[index + 1:]:
                if receipt.actor != prepared.actor or receipt.origin != "kernel":
                    continue
                native = (receipt.type == "codex.hook.user_prompt_submit"
                    and bool(receipt.payload.get("session_id")) and bool(receipt.payload.get("turn_id"))
                    and bool(payload.get("prompt_sha256"))
                    and receipt.payload.get("submitted_prompt_sha256") == payload["prompt_sha256"])
                stream = (receipt.type == "provider.turn.opened"
                    and receipt.payload.get("continuation_id") == claim_id
                    and all(receipt.payload.get(k) == payload.get(k)
                            for k in ("attempt_id", "dispatch_id", "operation_id", "lease_id")))
                if not native and not stream:
                    continue
                runtime.event_writer.append(ZfEvent(type="worker.continuation.reconciled", origin="kernel",
                    actor=prepared.actor, task_id=prepared.task_id, causation_id=receipt.id,
                    payload={"claim_id": claim_id, "receipt_event_id": receipt.id,
                        "attempt_id": payload["attempt_id"], "dispatch_id": payload["dispatch_id"],
                        "operation_id": payload["operation_id"], "workflow_run_id": row.get("run_id", ""),
                        "recovery_action": "completion_nudge_requested", "delivery_status": "observed",
                        "business_completed": False, "next_action": "observe_bound_progress"}))
                resolved.add(claim_id)
                count += 1
                break
    return count


def inject_completion_prompt(runtime: Any, *, role, task, dispatch_id: str, expected_event: str, claim_id: str):
    from zf.runtime.injection import build_task_prompt
    from zf.runtime.cli_command import zf_cli_cmd

    briefing_dir = runtime.state_dir / "briefings"
    briefing_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"-{claim_id}" if claim_id else ""
    path = briefing_dir / f"{role.instance_id}-{task.id}-completion-nudge{suffix}.md"
    lines = [
        f"Active task: {task.id}",
        "",
        "# Resume or complete the active task",
        "",
        (
            "The worker became silent before the kernel observed a terminal "
            "event. The kernel does not know whether implementation is complete. "
            "Preserve the current worktree and use the original active briefing "
            "and role instructions as the authoritative task contract."
        ),
        "",
        "## Required action (run these now, in order)",
        (
            f"1. `{zf_cli_cmd()} guard ownership --task {task.id} "
            f"--actor {role.instance_id}` — if it exits non-zero, STOP "
            "(your ownership is stale)."
        ),
        (
            "2. Inspect the current worktree and the original active briefing. "
            "First reconcile outstanding tools and external effects; continue waiting "
            "on an existing test/process instead of launching it again. An unknown "
            "effect or pending approval is a wait condition, not permission to retry. "
            "If acceptance is not yet met and no real external blocker exists, "
            "continue the original task from the preserved state. Do not emit a "
            "failure solely because this nudge arrived, the worktree is clean, "
            "or expected tests do not exist yet."
        ),
        (
            "3. When acceptance is met, commit any task-owned changes before "
            "completion. Stage only this task's changed files with explicit "
            "pathspecs (`git add -- <path>...`); never use `git add -A`, `git add .`, "
            "or `git commit -a`."
        ),
        (
            f"4. Emit your terminal event `{expected_event}` with the active "
            f"dispatch id `{dispatch_id or '-'}` (see your role instructions "
            "for the exact command). Emit the failure event only when a real "
            "acceptance blocker remains after attempting to resume the task."
        ),
    ]
    from zf.runtime.attempt_read_progress import input_continuation_lines
    lines.extend(input_continuation_lines(runtime, role_instance=role.instance_id, task_id=task.id, dispatch_id=dispatch_id))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    prompt = build_task_prompt(role.instance_id, path)
    context = runtime._dispatch_context(
        role=role,
        briefing_path=path,
        task_id=task.id,
    )
    from zf.runtime.task_attempt_continuation import continuation_context
    context = continuation_context(runtime, role.instance_id, context)
    context = prepare_continuation_delivery(runtime, context, prompt, claim_id)
    runtime._send_transport_task(role.instance_id, path, prompt, context)
    return path
