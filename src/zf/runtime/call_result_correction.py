"""Field-level repair guidance for invalid provider control results."""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from zf.runtime.call_result_admission import CallResultAdmissionOutcome

from zf.core.events.model import ZfEvent


def repair_instruction(issue: Mapping[str, Any]) -> str:
    field = str(issue.get("field") or "control_result")
    code = str(issue.get("code") or "schema_invalid")
    message = str(issue.get("message") or "").strip()
    if code == "missing_required":
        return f"Provide a non-empty value at {field}."
    if code == "workflow_output_missing":
        source_ref = message or field
        output_name = source_ref.rsplit(".", 1)[-1]
        return (
            f"Provide the non-placeholder artifact body at outputs.{output_name} "
            f"for declared port {source_ref}."
        )
    if code == "semantic_submit_required":
        return "Use the exact result submit command for the existing operation."
    detail = f" ({message})" if message else ""
    return f"Replace {field} with a value matching the required schema{detail}."


def build_correction_briefing(
    *,
    state_dir: Path,
    source_payload: Mapping[str, Any],
    task_id: str,
    operation_id: str,
    request_hash: str,
    correction_ref: Mapping[str, Any],
    issues: list[dict[str, str]],
) -> str:
    submit_command, scratch_path = _result_submit_command(
        state_dir=state_dir,
        source_payload=source_payload,
        operation_id=operation_id,
    )
    issue_rows = [
        {
            "field": str(issue.get("field") or "control_result"),
            "code": str(issue.get("code") or "schema_invalid"),
            "message": str(issue.get("message") or ""),
            "required_change": repair_instruction(issue),
        }
        for issue in issues
    ]
    submit_lines = [
        "Emit one corrected terminal result using the same "
        "task/attempt/dispatch identity."
    ]
    if submit_command:
        submit_lines = [
            f"Edit the existing result scratch in place: `{scratch_path}`",
            "Resubmit that corrected file exactly once:",
            "```bash",
            submit_command,
            "```",
        ]
    return "\n".join([
        f"Active task: {task_id or '(none)'}",
        "",
        "# Call Result Protocol Correction",
        "",
        "The previous provider turn completed work but returned an invalid control result.",
        "Do not redo implementation or change verdict/evidence semantics.",
        f"Correction packet: `{correction_ref.get('ref', '')}`",
        f"Operation: `{operation_id}`",
        f"Request hash: `{request_hash}`",
        "",
        "Exact schema issues from the latest result:",
        "```json",
        json.dumps(issue_rows, ensure_ascii=False, indent=2),
        "```",
        "",
        *submit_lines,
        "Do not merely change finding severity unless an issue above explicitly requires it.",
        "",
    ])


def wait_for_source_turn_stop(
    runtime: Any,
    *,
    source_event: ZfEvent,
    actor: str,
    backend: str,
    timeout_seconds: float = 30.0,
    poll_interval: float = 0.1,
) -> bool:
    """Wait for a stop hook or durable provider close after this result."""

    stop_type = {
        "codex": "codex.hook.stop",
        "claude-code": "claude.hook.stop",
    }.get(backend)
    event_log = getattr(runtime, "event_log", None)
    if not stop_type or event_log is None:
        return True

    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        source_seen = False
        try:
            events = event_log.read_all()
        except Exception:
            return False
        for event in events:
            if event.id == source_event.id:
                source_seen = True
                continue
            if (
                source_seen
                and (event.type == stop_type or (
                    event.type == "provider.turn.closed"
                    and event.payload.get("backend") == backend
                    and bool(event.payload.get("turn_id"))
                ))
                and str(event.actor or "") == actor
            ):
                return True
        if not source_seen:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.01, poll_interval))


def dispatch_call_result_correction(
    runtime: Any,
    *,
    source_event: ZfEvent,
    outcome: CallResultAdmissionOutcome,
) -> bool:
    """Continue the same resident provider session with a minimal correction."""

    if not outcome.repair_requested or not outcome.correction_ref:
        return False
    payload = source_event.payload if isinstance(source_event.payload, dict) else {}
    actor = str(payload.get("role_instance") or payload.get("role") or source_event.actor or "")
    role = next((
        item for item in getattr(runtime.config, "roles", [])
        if actor in {item.instance_id, item.name}
    ), None)
    if role is None:
        return False
    if not wait_for_source_turn_stop(
        runtime,
        source_event=source_event,
        actor=actor,
        backend=str(getattr(role, "backend", "") or ""),
    ):
        return False
    task_id = str(source_event.task_id or payload.get("task_id") or "")
    briefing_dir = Path(runtime.state_dir) / "briefings"
    briefing_dir.mkdir(parents=True, exist_ok=True)
    path = briefing_dir / f"{role.instance_id}-{task_id or 'call'}-result-correction-{outcome.repair_round}.md"
    path.write_text(
        build_correction_briefing(
            state_dir=Path(runtime.state_dir),
            source_payload=payload,
            task_id=task_id,
            operation_id=outcome.operation_id,
            request_hash=outcome.request_hash,
            correction_ref=outcome.correction_ref,
            issues=outcome.issues,
        ),
        encoding="utf-8",
    )
    from zf.runtime.injection import build_task_prompt

    prompt = build_task_prompt(role.instance_id, path)
    context = runtime._dispatch_context(
        role=role,
        briefing_path=path,
        task_id=task_id,
        trace_id=source_event.correlation_id,
    )
    runtime._send_transport_task(role.instance_id, path, prompt, context)
    runtime.event_writer.append(ZfEvent(
        type="workflow.call.result.repair.dispatched",
        actor="zf-cli", task_id=task_id or None,
        causation_id=source_event.id, correlation_id=source_event.correlation_id,
        payload={
            "schema_version": "call-result-repair-dispatch.v1",
            "workflow_run_id": str(payload.get("workflow_run_id") or source_event.correlation_id or ""),
            "operation_id": outcome.operation_id, "request_hash": outcome.request_hash,
            "dispatch_id": str(payload.get("dispatch_id") or payload.get("run_id") or ""),
            "role_instance": role.instance_id, "source_event_id": source_event.id,
            "repair_round": outcome.repair_round,
            "correction_ref": dict(outcome.correction_ref),
        },
    ))
    return True


def _result_submit_command(
    *,
    state_dir: Path,
    source_payload: Mapping[str, Any],
    operation_id: str,
) -> tuple[str, str]:
    scratch_ref = str(source_payload.get("result_scratch_ref") or "")
    if not scratch_ref or not operation_id:
        return "", ""
    try:
        from zf.runtime.cli_command import zf_cli_cmd
        from zf.runtime.stage_execution_card import prepare_result_file_command

        command, scratch = prepare_result_file_command(
            state_dir=state_dir,
            result_scratch_ref=scratch_ref,
            operation_id=operation_id,
            cli_command=zf_cli_cmd(),
            semantic_template={},
        )
    except (OSError, ValueError):
        return "", ""
    return command, str(scratch)


__all__ = [
    "build_correction_briefing",
    "dispatch_call_result_correction",
    "repair_instruction",
    "wait_for_source_turn_stop",
]
