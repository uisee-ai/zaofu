"""Exact Owner start approval for a ready, previously planning-only Channel PRD."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.runtime.channel_owner_authority import channel_owner_authority_error
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_readiness import channel_task_execution_authorization_error
from zf.runtime.channel_workflow_authority import (
    channel_authority_context_from_task,
    channel_workflow_authority_error,
)
from zf.runtime.kanban_proposals import proposal_execution_gate


def channel_start_authorization_error(
    *, state_dir: Path, task: Any, actor: str, payload: dict[str, Any], events: list[Any],
) -> str:
    """Authorize only this exact Task/route proposal; never rewrite PRD readiness.

    The caller is a token-gated controlled action. Its request and proposal form
    the durable authorization receipt through the existing causation chain.
    """
    original = channel_task_execution_authorization_error(task)
    if not original:
        return ""
    parameters = payload.get("parameters") or {}
    if parameters.get("authorize_channel_execution") is not True:
        return original
    evidence = task.contract.evidence_contract
    if evidence.get("readiness_verdict") != "ready":
        return "explicit start approval cannot override unresolved PRD readiness"
    authority = channel_authority_context_from_task(task)
    if error := channel_workflow_authority_error(state_dir, authority):
        return error
    channel = project_channel(state_dir, str(authority.get("channel_id") or "")) or {}
    if error := channel_owner_authority_error(
        channel, actor=actor, capability="channel.workflow.start",
    ):
        return error
    proposal_id = str(payload.get("proposal_event_id") or "")
    if not proposal_id:
        return "explicit Channel execution requires an exact workflow-start proposal"
    gate = proposal_execution_gate(
        events, proposal_event_id=proposal_id, action="workflow-start", execution_payload=payload,
    )
    if not gate.get("ok") or gate.get("status") != "ready":
        return "explicit Channel execution proposal rejected: " + str(gate.get("status"))
    return ""
