"""EVAL-PAYLOAD-CONTRACT-001 — completion-event payload contract (doc 43 §2.4).

Adopts a 6-field task-output contract: every completion event
emitted by a worker MUST carry a structured payload describing what
was done + evidence + residual risks + next-agent input.

Missing required fields → kernel emits ``task.contract.invalid`` audit
event (non-blocking) so the next reviewer / downstream gate can see
the gap, and ``zf workflow audit`` / ``zf handoff --score`` surface
the incomplete handoff to operators.

Discipline:
- This module is pure: validate_completion_payload returns a list of
  missing fields, never raises.
- ``residual_risks`` and ``next_agent_input`` are **WARN** fields:
  absence is recorded as a warning but NOT included in the missing
  errors list (so backward-compat with old payloads is preserved).
- ``tests_run`` is required only for verifier-role events
  (verify.passed / test.passed / judge.passed); other completion events get a pass.
"""

from __future__ import annotations

from typing import Any, Iterable

from zf.core.events.model import ZfEvent


# Events that carry "I finished my role's work" semantics. Each must
# describe its work in the 6-field contract.
SUCCESS_EVENT_TYPES: frozenset[str] = frozenset({
    "dev.build.done",
    "review.approved",
    "verify.passed",
    "test.passed",
    "judge.passed",
    "arch.proposal.done",
    "design.critique.done",
})

# Subset of SUCCESS_EVENT_TYPES that must carry a non-empty
# ``tests_run`` list. Verifier roles produce these.
VERIFY_EVENT_TYPES: frozenset[str] = frozenset({
    "verify.passed",
    "test.passed",
    "judge.passed",
})

# WARN-only fields (missing → warning, not error). The full payload
# contract requires them, but lack of them does not block downstream
# processing.
_WARN_ONLY_FIELDS: tuple[str, ...] = (
    "residual_risks",
    "next_agent_input",
)


def validate_completion_payload(event: ZfEvent) -> list[str]:
    """Return list of missing / invalid required fields.

    Empty list means the event payload satisfies the contract. Non-empty
    list signals the kernel to emit ``task.contract.invalid``.

    For non-SUCCESS_EVENT_TYPES events, returns [] (no contract).
    """
    if event.type not in SUCCESS_EVENT_TYPES:
        return []
    payload = event.payload if isinstance(event.payload, dict) else {}
    if _is_design_approval_payload(event.type, payload):
        return []
    errors: list[str] = []
    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("summary")
    if "changed_files" not in payload:
        errors.append("changed_files")
    elif not isinstance(payload["changed_files"], (list, tuple)):
        errors.append("changed_files")
    if event.type in VERIFY_EVENT_TYPES:
        tests_run = payload.get("tests_run")
        if not isinstance(tests_run, (list, tuple)) or not tests_run:
            errors.append("tests_run")
    evidence_refs = payload.get("evidence_refs")
    if not isinstance(evidence_refs, (list, tuple)) or not evidence_refs:
        errors.append("evidence_refs")
    return errors


def warn_completion_payload(event: ZfEvent) -> list[str]:
    """Return list of WARN-only fields that are missing.

    These don't gate downstream processing but indicate the worker
    didn't surface residual risks / handoff hints — surfaced by
    ``zf handoff --score`` and ``zf kanban health``.
    """
    if event.type not in SUCCESS_EVENT_TYPES:
        return []
    payload = event.payload if isinstance(event.payload, dict) else {}
    if _is_design_approval_payload(event.type, payload):
        return []
    warnings: list[str] = []
    for field in _WARN_ONLY_FIELDS:
        v = payload.get(field)
        if v is None:
            warnings.append(field)
        elif isinstance(v, (list, tuple, str)) and not v:
            warnings.append(field)
    return warnings


def build_invalid_event_payload(
    source_event: ZfEvent,
    missing: list[str],
    *,
    warnings: Iterable[str] = (),
) -> dict[str, Any]:
    """Build the ``task.contract.invalid`` event payload that the
    kernel emits when validate_completion_payload finds missing
    required fields."""
    return {
        "reason": "completion_payload_contract_violation",
        "source_event_id": source_event.id,
        "source_event_type": source_event.type,
        "source_actor": source_event.actor or "",
        "missing_fields": list(missing),
        "warn_fields": list(warnings),
    }


def _is_design_approval_payload(event_type: str, payload: dict[str, Any]) -> bool:
    """Product delivery may use design.critique.done as plan approval.

    In that mode the event is an approval signal, not a role handoff payload.
    A completion payload still carries summary / changed_files / evidence_refs
    and is validated by the normal path.
    """
    if event_type != "design.critique.done":
        return False
    if "summary" in payload or "changed_files" in payload or "evidence_refs" in payload:
        return False
    verdict = str(payload.get("verdict") or payload.get("recommendation") or "").strip()
    checks = payload.get("checks")
    return bool(verdict) and isinstance(checks, list)
