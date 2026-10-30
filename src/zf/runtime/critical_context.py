"""Bind original user inputs and approved configuration to each run's reads."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import yaml

from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def critical_context_sources(
    state_dir: Path, events: Iterable[Any], workflow_run_id: str,
) -> list[dict[str, Any]]:
    events = list(events)
    binding = next((event for event in reversed(events)
                    if event.type == "config.run_contract.request_bound"
                    and event.payload.get("run_id") == workflow_run_id), None)
    if binding is None:
        return []  # Legacy/direct dispatch has no approved workflow snapshot.
    payload = binding.payload
    sources = []
    manifest_ref = str(payload.get("workflow_input_manifest_ref") or "")
    if manifest_ref:
        sources.append({"source_id": "critical-workflow-input", "ref": manifest_ref})
    created = next((event for event in events if event.type == "task.created"
                    and event.task_id == binding.task_id), None)
    request = created.payload.get("request", {}) if created else {}
    by_id = {event.id: event for event in events}
    messages = []
    for event_id in request.get("originating_message_event_ids", []):
        event = by_id.get(event_id)
        if event is None or event.type != "user.message":
            raise ValueError(f"critical user input event unavailable: {event_id}")
        for key in ("project_id", "conversation_id"):
            if request.get(key) and request[key] != event.payload.get(key):
                raise ValueError("critical user input belongs to another origin")
        message = event.payload.get("message", "")
        raw_output = (event.payload.get("refs") or {}).get("raw_output")
        if isinstance(raw_output, dict):
            message = hydrate_sidecar_ref(state_dir, raw_output).payload
        messages.append({"event_id": event.id, "message": message,
                         "request": event.payload.get("request", {})})
    approved = payload.get("effective_config_ref")
    config_identity = {}
    if isinstance(approved, dict) and approved.get("ref"):
        snapshot = hydrate_sidecar_ref(state_dir, approved).payload
        # Keep the exact Flow spec (including Raw lane/model overrides) hot.
        # The complete compiled controller stays retrievable by its bound ref.
        docs = list(yaml.safe_load_all(snapshot.get("yaml") or ""))
        specs = [doc for doc in docs if isinstance(doc, dict)
                 and str(doc.get("kind", "")).endswith("Flow")]
        config_identity = {"snapshot": approved,
                           "request_revision": snapshot.get("request_revision"),
                           "flow_documents": specs}
        sources.append({**approved, "source_id": "approved-config-full"})
    body = {
        "schema_version": "critical-context.v1", "workflow_run_id": workflow_run_id,
        "binding_event_id": binding.id, "original_user_inputs": messages,
        "user_input_provenance": "bound_events" if messages else "unavailable",
        "clarification_answers": request.get("plan_continuation", {}),
        "approved_configuration": config_identity,
        "authority": "Original inputs constrain semantic decisions; the approved Flow overrides earlier configuration preferences. Current Task Contract and current rework remain mandatory.",
    }
    descriptor = write_immutable_json_sidecar(
        state_dir, body, root="critical-context", kind="critical_context",
        schema_version="critical-context.v1", created_by="call-result-runtime",
        source_event_id=binding.id,
    )
    sources.append({**descriptor, "source_id": "critical-user-and-config"})
    return sources


def recovery_required_inputs(state_dir: Path, events: Iterable[Any], task: Any, role: str) -> str:
    """Restore current controlled inputs, not a prior attempt's read receipts."""
    from zf.runtime.artifact_read_ledger import render_attempt_source_briefing
    from zf.runtime.artifact_read_liveness import live_attempt_ids

    events = list(events)
    live = live_attempt_ids(events)
    dispatch = str(task.active_dispatch_id or "")
    if not dispatch:
        return ""
    for event in reversed(events):
        payload = event.payload
        if (payload.get("dispatch_id") == dispatch
                and (event.task_id or payload.get("task_id")) == task.id
                and payload.get("role_instance", role) == role
                and payload.get("attempt_id") in live
                and payload.get("attempt_source_manifest_ref")):
            return render_attempt_source_briefing(payload, state_dir=state_dir)
    return ""
