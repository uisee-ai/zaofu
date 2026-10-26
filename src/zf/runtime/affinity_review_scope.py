"""B3 (R20): per-lane review/verify scope discipline for affinity fanout children.

An ``affinity_stage_slots`` reader child (a review or verify lane) checks out the
WHOLE candidate (``target_ref`` = the candidate branch) but is responsible for
only ONE slice — its affinity lane's package. R20 showed the briefing didn't say
so: each lane read the full 213-file candidate + reference source, exhausted its
context (worker.context.warning → context.compact.failed → 2400s timeout) and
spuriously emitted ``review.rejected``.

Ordinary affinity review remains slice-scoped. A bound Product Acceptance report
also requires the candidate-wide journeys, so its briefing authorizes that
additional evidence closure without widening source-write permissions.
"""
from __future__ import annotations


def affinity_scope_identity_errors(
    child_payload: dict | None,
    *,
    role_instance: str = "",
) -> list[str]:
    """Return fail-closed diagnostics for affinity child identity."""
    payload = child_payload if isinstance(child_payload, dict) else {}
    if str(payload.get("assignment_strategy") or "") != "affinity_stage_slots":
        return []
    errors: list[str] = []
    if not str(payload.get("lane_id") or "").strip():
        errors.append("missing_lane_id")
    if not str(role_instance or payload.get("role_instance") or "").strip():
        errors.append("missing_role_instance")
    if not str(payload.get("stage_slot") or "").strip():
        errors.append("missing_stage_slot")
    return errors


def affinity_scope_briefing_lines(
    child_payload: dict | None, *, product_acceptance_required: bool = False,
) -> list[str]:
    """Scope-discipline briefing lines for an affinity child, or [] if the child
    is not an affinity_stage_slots lane (or carries no lane/task identity)."""
    payload = child_payload if isinstance(child_payload, dict) else {}
    if str(payload.get("assignment_strategy") or "") != "affinity_stage_slots":
        return []
    lane = str(payload.get("lane_id") or "")
    tag = str(payload.get("affinity_tag") or payload.get("task_id") or "")
    if not lane and not tag:
        return []
    label = (f"lane `{lane}`" if lane else "") + (
        (" / task `" + tag + "`") if tag else ""
    )
    if product_acceptance_required:
        return [
            "## Review Scope — Task Slice and Candidate Product Acceptance",
            "",
            f"You are the slot for affinity {label.strip(' /')}. "
            "Task requirement rows remain scoped to the pinned task contract.",
            "The bound Product Acceptance report is a separate obligation: verify all "
            "mandatory product journeys against this exact candidate. Cross-slice "
            "reads and probes needed by those journeys are authorized; do not leave "
            "them unverified for other lanes or synth. Limit inspection to the "
            "journeys and their dependency closure, not an unrelated whole-repository audit.",
            "This does not grant source-write permission or change task ownership.",
            "",
        ]
    return [
        "## Review Scope — YOUR AFFINITY SLICE ONLY (context discipline)",
        "",
        f"You are the slot for affinity {label.strip(' /')}. Inspect ONLY the "
        "files that belong to YOUR slice — its package / allowed_paths in the "
        "candidate (e.g. `packages/<your-lane>/**`, `tests/<your-lane>/**`). Use "
        "the reference source ONLY for the specific files you are inspecting.",
        "",
        "Do NOT read or review the full candidate or other lanes' packages. The "
        "candidate is large; reading it all exhausts your context (→ compaction "
        "failure / timeout / a spurious reject). The other lanes + the synth role "
        "cover the rest — keep your inspection bounded to your slice.",
        "",
    ]
