"""Deterministic Channel updates for workflow/fanout handoffs."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.security.redaction import redact_obj


def emit_fanout_channel_state_update(
    *,
    writer: EventWriter,
    terminal_event: ZfEvent,
    manifest: dict[str, Any],
    synth_event: ZfEvent | None = None,
) -> ZfEvent | None:
    """Mirror a fanout terminal result into its originating Channel.

    The fanout/workflow event remains the execution truth. This function only
    emits a Channel state update when the fanout carries an explicit
    ``channel_id`` through its request/source refs.
    """
    terminal_payload = terminal_event.payload if isinstance(terminal_event.payload, dict) else {}
    synth_payload = synth_event.payload if synth_event is not None and isinstance(synth_event.payload, dict) else {}
    context = _channel_context(manifest, terminal_payload, synth_payload)
    channel_id = str(context.get("channel_id") or "").strip()
    if not channel_id:
        return None

    stage_id = str(
        terminal_payload.get("stage_id")
        or manifest.get("stage_id")
        or synth_payload.get("stage_id")
        or ""
    )
    terminal_status = str(terminal_payload.get("status") or synth_payload.get("status") or "completed")
    status = _channel_status(stage_id, terminal_status)
    task_id = str(
        context.get("task_id")
        or terminal_event.task_id
        or (synth_event.task_id if synth_event is not None else "")
        or ""
    )
    fanout_id = str(
        terminal_payload.get("fanout_id")
        or manifest.get("fanout_id")
        or synth_payload.get("fanout_id")
        or ""
    )
    summary = _summary(
        stage_id=stage_id,
        status=status,
        terminal_status=terminal_status,
        synth_payload=synth_payload,
        terminal_payload=terminal_payload,
    )
    refs = _refs(
        fanout_id=fanout_id,
        stage_id=stage_id,
        terminal_event=terminal_event,
        synth_event=synth_event,
        manifest=manifest,
        context=context,
    )
    return writer.emit(
        "channel.state_update.posted",
        actor="zf-cli",
        task_id=task_id or None,
        causation_id=terminal_event.id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": str(context.get("thread_id") or "main"),
            "status": status,
            "summary": summary,
            "task_id": task_id,
            "refs": redact_obj(refs),
            "source": "runtime",
        },
    )


def _channel_context(manifest: dict[str, Any], *items: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def absorb(item: dict[str, Any]) -> None:
        source_refs = item.get("source_refs") if isinstance(item.get("source_refs"), dict) else {}
        for key in (
            "channel_id",
            "thread_id",
            "task_id",
            "pattern_id",
            "workflow_run_id",
            "workflow_input_manifest_ref",
            "workflow_prompt_ref",
            "prompt_kind",
        ):
            value = str(item.get(key) or source_refs.get(key) or "").strip()
            if value and not out.get(key):
                out[key] = value
        if source_refs and not out.get("source_refs"):
            out["source_refs"] = dict(source_refs)
        artifact_refs = item.get("artifact_refs")
        if isinstance(artifact_refs, list) and artifact_refs and not out.get("artifact_refs"):
            out["artifact_refs"] = artifact_refs

    absorb(manifest)
    for item in items:
        absorb(item)
        request = item.get("request") if isinstance(item.get("request"), dict) else {}
        absorb(request)
        trigger_payload = (
            item.get("trigger_payload")
            if isinstance(item.get("trigger_payload"), dict)
            else {}
        )
        absorb(trigger_payload)

    for child in manifest.get("children", []) or []:
        if not isinstance(child, dict):
            continue
        absorb(child)
        payload = child.get("payload") if isinstance(child.get("payload"), dict) else {}
        absorb(payload)
        trigger_payload = (
            payload.get("trigger_payload")
            if isinstance(payload.get("trigger_payload"), dict)
            else {}
        )
        absorb(trigger_payload)
        if out.get("channel_id"):
            break
    return out


def _channel_status(stage_id: str, terminal_status: str) -> str:
    label = "research" if _is_research_stage(stage_id) else "fanout"
    if terminal_status in {"completed", "passed", "ok", "success"}:
        return f"{label}_completed"
    return f"{label}_failed"


def _is_research_stage(stage_id: str) -> bool:
    lowered = stage_id.lower()
    return "research" in lowered or "autoresearch" in lowered


def _summary(
    *,
    stage_id: str,
    status: str,
    terminal_status: str,
    synth_payload: dict[str, Any],
    terminal_payload: dict[str, Any],
) -> str:
    report = synth_payload.get("report") if isinstance(synth_payload.get("report"), dict) else {}
    text = str(
        synth_payload.get("summary")
        or report.get("summary")
        or terminal_payload.get("summary")
        or ""
    ).strip()
    if text:
        return _excerpt(text, 280)
    readable = "research fanout" if status.startswith("research_") else "fanout"
    outcome = "completed" if status.endswith("_completed") else f"ended as {terminal_status or 'failed'}"
    suffix = f" for {stage_id}" if stage_id else ""
    return f"{readable} {outcome}{suffix}"


def _refs(
    *,
    fanout_id: str,
    stage_id: str,
    terminal_event: ZfEvent,
    synth_event: ZfEvent | None,
    manifest: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    refs: dict[str, Any] = {
        "fanout_id": fanout_id,
        "stage_id": stage_id,
        "fanout_terminal_event_id": terminal_event.id,
    }
    if synth_event is not None:
        refs["fanout_synth_event_id"] = synth_event.id
    for key in (
        "workflow_run_id",
        "workflow_input_manifest_ref",
        "workflow_prompt_ref",
        "prompt_kind",
    ):
        value = str(context.get(key) or manifest.get(key) or "").strip()
        if value:
            refs[key] = value
    source_refs = context.get("source_refs") if isinstance(context.get("source_refs"), dict) else {}
    if source_refs:
        refs["source_refs"] = source_refs
    artifact_refs = context.get("artifact_refs")
    if isinstance(artifact_refs, list) and artifact_refs:
        refs["artifact_refs"] = artifact_refs
    return refs


def _excerpt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)].rstrip() + "..."
