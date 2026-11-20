"""Resolve immutable objective and candidate refs for Goal closure."""
from __future__ import annotations

from zf.core.events.model import ZfEvent


def _resolve_run_objective_ref(
    events: list[ZfEvent],
    *,
    workflow_run_id: str,
    payload: dict,
    metadata: dict,
) -> str:
    """Resolve the immutable objective artifact for one workflow run."""

    scoped_payloads = [payload]
    # A controlled owner amendment is later Run authority; the original
    # Workflow approval remains evidence of the historical launch only.
    for event in reversed(events):
        body = event.payload if isinstance(event.payload, dict) else {}
        if (event.type == "plan.rejected" and event.actor == "operator"
                and str(body.get("via") or "").startswith("controlled-action:")
                and str(body.get("workflow_run_id") or body.get("trace_id") or "") == workflow_run_id
                and body.get("requirement_amendment_ref") and body.get("requirement_spec_ref")):
            return str(body["requirement_spec_ref"])
    for event in reversed(events):
        body = event.payload if isinstance(event.payload, dict) else {}
        identities = {
            str(body.get("workflow_run_id") or "").strip(),
            str(body.get("run_id") or "").strip(),
            str(body.get("request_id") or "").strip(),
            str(body.get("trace_id") or "").strip(),
            str(event.correlation_id or "").strip(),
        }
        if workflow_run_id in identities:
            scoped_payloads.append(body)

    # A submitted Request pins the requirement revision for the Run. It must
    # outrank profile placeholders and mutable intake projections.
    for body in scoped_payloads:
        ref = str(body.get("requirement_spec_ref") or "").strip()
        if ref:
            return ref
    for body in scoped_payloads:
        for key in ("objective_ref", "workflow_prompt_ref", "workflow_input_manifest_ref"):
            ref = str(body.get(key) or "").strip()
            if ref:
                return ref
    return str(
        metadata.get("objective_ref")
        or metadata.get("prd_ref")
        or metadata.get("issue_ref")
        or ""
    ).strip()


def _resolve_candidate_ref(
    events: list[ZfEvent],
    *,
    workflow_run_id: str,
    target_commit: str,
    payload: dict,
) -> str:
    explicit = str(
        payload.get("candidate_ref") or payload.get("target_ref") or ""
    ).strip()
    if explicit and explicit != target_commit:
        return explicit
    for event in reversed(events):
        if event.type not in {"candidate.ready", "candidate.integration.completed"}:
            continue
        body = event.payload if isinstance(event.payload, dict) else {}
        event_run = str(
            body.get("workflow_run_id")
            or body.get("run_id")
            or body.get("trace_id")
            or event.correlation_id
            or ""
        ).strip()
        if workflow_run_id and event_run and event_run != workflow_run_id:
            continue
        event_target = str(
            body.get("candidate_head_commit") or body.get("commit") or ""
        ).strip()
        if target_commit and event_target != target_commit:
            continue
        candidate_ref = str(
            body.get("candidate_ref") or body.get("branch") or ""
        ).strip()
        if candidate_ref:
            return candidate_ref
    return explicit or target_commit
