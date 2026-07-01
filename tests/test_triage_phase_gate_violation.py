"""#U fix: rework_triage classifier add phase_gate_violation case
(cangjie 2026-05-22 r3 P0V06 observation).

Pre-fix: dev.blocked event with payload `phase_gate_check.violation`
+ `trigger_misclassification` fell through to evidence_payload_gap
→ request_evidence_reissue (无效 retry,plan-level bug)→ dev retry
→ second dev.blocked → triage 'ambiguous' → human.escalate → cangjie
chain stuck 3h+ until operator manual intervention.

Fix: detect 3 signals in classify_rework_trigger:
  Signal 1: payload.phase_gate_check.violation non-empty (kernel-emitted)
  Signal 2: payload.trigger_misclassification.actual_classification contains 'phase_gate' (dev round-2 explicit halt)
  Signal 3: text contains 'phase_gate_violation' marker (fallback)

Route to arch with `dispatch_rework` action. plan-level redesign needed
(baseline order conflict, scope_gate placement, etc.), not dev rework.

Refs: tasks/2026-05-22-0608-triage-classifier-phase-gate-violation-case.md
Cangjie evidence: docs/records/archives/r3-events-final-20260522.jsonl
"""

from __future__ import annotations

from typing import Any

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.rework_triage import classify_rework_trigger


def _ev(payload: dict[str, Any]) -> ZfEvent:
    return ZfEvent(
        type="dev.blocked",
        actor="dev-1",
        task_id="TASK-X",
        payload=payload,
    )


# ─── core: 3 signal types each classifies as phase_gate_violation ─────────


def test_phase_gate_check_violation_payload_classified(tmp_path):
    """Signal 1: payload.phase_gate_check.violation non-empty (cangjie r3 round 1)."""
    ev = _ev({
        "state": "BLOCKED",
        "summary": "P0 RED gate cannot be established",
        "phase_gate_check": {
            "phase": "P0",
            "violation": (
                "P1 vertical (P1V01..P1V17) already shipped on worker/dev-1 "
                "baseline BEFORE this P0 RED gate"
            ),
        },
    })
    result = classify_rework_trigger(ev)
    assert result.classification == "phase_gate_violation", (
        f"#U fix: phase_gate_check.violation should classify as "
        f"phase_gate_violation; got {result.classification!r}"
    )
    assert result.suspected_owner == "arch", (
        f"phase_gate_violation should route to arch (plan-level redesign), "
        f"got {result.suspected_owner!r}"
    )
    assert result.recommended_action == "dispatch_rework"


def test_trigger_misclassification_round2_halt_signal(tmp_path):
    """Signal 2: payload.trigger_misclassification.actual_classification (cangjie r3 round 2)."""
    ev = _ev({
        "state": "BLOCKED",
        "escalation_round": 2,
        "halt_request": "explicit halt — verification real fail not evidence gap",
        "trigger_misclassification": {
            "harness_classification": "evidence_payload_gap",
            "actual_classification": "phase_gate_violation",
        },
    })
    result = classify_rework_trigger(ev)
    assert result.classification == "phase_gate_violation"
    assert result.suspected_owner == "arch"


def test_phase_gate_violation_text_marker_fallback(tmp_path):
    """Signal 3: text contains 'phase_gate_violation' as fallback marker."""
    ev = _ev({
        "state": "BLOCKED",
        "summary": "phase_gate_violation: P3 vertical shipped before P2 gate established",
    })
    result = classify_rework_trigger(ev)
    assert result.classification == "phase_gate_violation"


# ─── preservation: no signal → fall through to existing logic ─────────────


def test_no_phase_gate_signal_falls_through(tmp_path):
    """Without any phase_gate signal → existing evidence_payload_gap / ambiguous flow.

    Use a payload that triggers evidence_payload_gap heuristic.
    """
    ev = _ev({
        "state": "BLOCKED",
        "summary": "missing artifact_refs in evidence schema",
        # No phase_gate_check, no trigger_misclassification, no marker text
    })
    result = classify_rework_trigger(ev)
    # Must NOT misclassify as phase_gate_violation
    assert result.classification != "phase_gate_violation"


def test_evidence_gap_still_works_when_no_phase_gate(tmp_path):
    """evidence_payload_gap path still works for true evidence gaps."""
    ev = ZfEvent(
        type="discriminator.failed",
        actor="dev",
        task_id="TASK-Y",
        payload={
            "failed_d": ["EvidenceD"],
            "summary": "missing evidence_refs in dev.build.done payload",
        },
    )
    result = classify_rework_trigger(ev)
    # Should not pick up phase_gate_violation (no signal)
    assert result.classification != "phase_gate_violation"


# ─── taxonomy + retry derivation ──────────────────────────────────────────


def test_phase_gate_violation_in_content_bucket():
    """phase_gate_violation should be content classification (not infra, not terminal)."""
    from zf.runtime.rework_triage import derive_taxonomy_bucket, derive_retryable
    bucket = derive_taxonomy_bucket("phase_gate_violation")
    assert bucket == "content", (
        f"phase_gate_violation should be content taxonomy bucket; got {bucket!r}"
    )


def test_phase_gate_violation_retryable():
    """phase_gate_violation is retryable (arch redesign + retry meaningful)."""
    from zf.runtime.rework_triage import derive_retryable
    assert derive_retryable("phase_gate_violation") is False, (
        "phase_gate_violation retry only after arch redesigns plan; "
        "infra retry should not auto-retry the failed task"
    )


def test_phase_gate_violation_should_increment_retry():
    """should_increment_retry True so retry counter advances toward circuit-breaker."""
    from zf.runtime.rework_triage import REWORK_RETRY_CLASSIFICATIONS
    assert "phase_gate_violation" in REWORK_RETRY_CLASSIFICATIONS, (
        "phase_gate_violation needs retry accounting (plan redesign + retry path)"
    )
    ev = _ev({
        "phase_gate_check": {"phase": "P0", "violation": "test"},
    })
    result = classify_rework_trigger(ev)
    assert result.should_increment_retry is True
