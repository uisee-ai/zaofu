"""Mechanical authorization helpers for Channel PRD readiness."""

from __future__ import annotations

from typing import Any


def owner_readiness_risk_accepted(
    consensus: dict[str, Any],
    *,
    readiness_ref: object,
    readiness_digest: object,
) -> bool:
    """Return whether the Owner accepted this exact readiness artifact."""

    expected_ref = str(readiness_ref or "").strip()
    expected_digest = _bare_digest(readiness_digest)
    return bool(
        consensus.get("human_confirmed")
        and consensus.get("risk_accepted") is True
        and expected_ref
        and expected_digest
        and str(consensus.get("confirmed_readiness_ref") or "").strip()
        == expected_ref
        and _bare_digest(consensus.get("confirmed_readiness_digest"))
        == expected_digest
    )


def channel_prd_planning_authorized(
    *,
    readiness_verdict: object,
    risk_accepted: bool,
) -> bool:
    """Return whether a confirmed PRD may create a Task and workflow plan.

    ``implementation_start`` is deliberately not part of this boundary. A
    planning-only PRD can be durable enough to create its immutable Task and
    route plan while still forbidding actual workflow execution later.
    """

    return str(readiness_verdict or "").strip() == "ready" or risk_accepted


def channel_prd_execution_authorized(
    *,
    readiness_verdict: object,
    implementation_start: object,
    risk_accepted: bool,
) -> bool:
    """Return whether a Channel PRD may start a workflow that executes work."""

    return (
        str(readiness_verdict or "").strip() == "ready"
        and implementation_start is True
    ) or risk_accepted


def channel_task_execution_authorization_error(task: Any) -> str:
    """Return a fail-closed execution error for a Channel-origin Task.

    Task creation and Workflow planning use the lower planning boundary. This
    guard is intentionally consulted only by the controlled workflow-start
    action, immediately before it can create downstream workflow effects.
    """

    contract = getattr(task, "contract", None)
    if contract is None or str(getattr(contract, "source_mode", "")) != "channel_prd":
        return ""
    evidence = getattr(contract, "evidence_contract", {})
    if not isinstance(evidence, dict):
        return "Channel PRD workflow execution requires readiness evidence"
    readiness_verdict = evidence.get("readiness_verdict")
    implementation_start = evidence.get("implementation_start")
    risk_accepted = evidence.get("readiness_risk_accepted") is True
    if channel_prd_execution_authorized(
        readiness_verdict=readiness_verdict,
        implementation_start=implementation_start,
        risk_accepted=risk_accepted,
    ):
        return ""
    return (
        "Channel PRD permits Task and Workflow planning but not workflow "
        "execution: implementation_start=true or exact owner risk acceptance "
        "is required"
    )


def _bare_digest(value: object) -> str:
    return str(value or "").strip().removeprefix("sha256:")


__all__ = [
    "channel_prd_execution_authorized",
    "channel_prd_planning_authorized",
    "channel_task_execution_authorization_error",
    "owner_readiness_risk_accepted",
]
