"""Remediation cascade — no-dead-end tier transitions (doc 79 Tier1).

The R12 collapse root: ``worker_stuck`` is classified ``infra``/retryable by
``rework_triage`` (doc 43 §2.2), but when the bounded respawn retries exhaust,
the lifecycle parks the worker in ``blocked_human`` and waits for an operator.
Unattended, no operator comes → 6h limbo (135x worker.stuck / 286x escalate).

This module encodes the **no-dead-end invariant**: when a tier's bounded
remediation is exhausted, transition to the next tier; the floor is a
deterministic ``safe_halt`` — never a silent dead-end. An unrecognised failure
class is fail-safe halted, not dropped.

Pure / deterministic — no I/O. Reuses the ``rework_triage`` taxonomy buckets
rather than inventing a second classifier.
"""

from __future__ import annotations

from dataclasses import dataclass

from zf.runtime.rework_triage import derive_taxonomy_bucket

# Keep-this-run-alive cascade outcomes (doc 79 §4.1).
CASCADE_RETRY = "retry"          # Tier1: bounded retries remain
CASCADE_ESCALATE = "escalate"    # Tier3: escalate to human (liveness confirmed)
CASCADE_SAFE_HALT = "safe_halt"  # floor: no liveness → graceful halt, not limbo

# The deterministic floor terminal event (doc 79 §4.4). Unconditional — never
# gated behind ZF_AUTORESEARCH_AUTO_REPAIR, or unattended runs could not halt.
SAFE_HALTED_EVENT = "runtime.safe_halted"


def build_safe_halt_payload(
    *,
    root_failure_class: str,
    evidence_event_ids: list[str],
    reason: str,
) -> dict:
    """Build the payload for the single terminal ``runtime.safe_halted`` signal.

    safe-halt is "I tried and froze the scene", not a 287th escalate buried in
    noise: it stops dispatch, freezes a resumable state, and emits ONE signal
    carrying the root failure class + evidence so the operator can resume.
    """
    return {
        "root_failure_class": root_failure_class,
        "evidence_event_ids": list(evidence_event_ids),
        "reason": reason,
        "resumable": True,
    }


def classify_bucket(failure_class: str) -> str:
    """Map a failure class to a ``rework_triage`` bucket.

    Returns ``infra`` | ``content`` | ``terminal`` | ``unknown``. ``unknown``
    is the no-dead-end fail-safe trigger, not an error. Delegates to
    ``rework_triage.derive_taxonomy_bucket`` so the taxonomy has a single owner
    (doc 79 §6: do not build a second classifier).
    """
    return derive_taxonomy_bucket(failure_class)


@dataclass(frozen=True)
class CascadeDecision:
    tier: str
    reason: str
    failure_class: str
    bucket: str


def decide_cascade(
    *,
    failure_class: str,
    attempts: int,
    cap: int,
    liveness: bool,
) -> CascadeDecision:
    """Decide the next remediation step for a failure on the keep-alive axis.

    - ``infra`` under cap → retry; exhausted → escalate (if a human is
      reachable) else safe-halt. Exhausted infra is NOT limbo — that is the
      R12 fix.
    - ``terminal`` / ``content`` → cannot be auto-recovered on this run →
      escalate if a human is reachable, else safe-halt. (Tier routing of
      content → Tier2 self-repair is layered on top by ``decide_repair``;
      this function is the keep-this-run-alive floor.)
    - ``unknown`` → safe-halt (fail-safe; never silently dropped).

    ``liveness`` is whether escalation can actually reach an operator. When it
    is False, escalation would dead-end, so the floor is safe-halt instead.
    """
    bucket = classify_bucket(failure_class)

    def _decision(tier: str, reason: str) -> CascadeDecision:
        return CascadeDecision(
            tier=tier, reason=reason, failure_class=failure_class, bucket=bucket
        )

    if bucket == "infra":
        if attempts < cap:
            return _decision(
                CASCADE_RETRY, f"infra retry {attempts}/{cap}"
            )
        if liveness:
            return _decision(
                CASCADE_ESCALATE,
                "infra retry exhausted → escalate (structural, not a blip)",
            )
        return _decision(
            CASCADE_SAFE_HALT,
            "infra retry exhausted, no operator reachable → safe-halt (not limbo)",
        )

    if bucket in ("terminal", "content"):
        if liveness:
            return _decision(
                CASCADE_ESCALATE, f"{bucket} failure → escalate to operator"
            )
        return _decision(
            CASCADE_SAFE_HALT,
            f"{bucket} failure, no operator reachable → safe-halt",
        )

    # unknown → no-dead-end fail-safe
    return _decision(
        CASCADE_SAFE_HALT,
        "unrecognised failure class → fail-safe safe-halt (no-dead-end default)",
    )
