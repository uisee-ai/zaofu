"""Read-only readiness inventory for open host/incident test tiers.

This module never starts a provider, VM, Memray, or fault injector.  It turns
the existing fail-closed admission contracts into an explicit operator-facing
answer: ``not_ready`` or ``ready_for_operator_run``.  The latter still has
``execution_authorized=false`` and is never a product pass.
"""

from __future__ import annotations

from typing import Any, Mapping

from tests.testkit.allocation_segments import (
    AllocationReentryEvidence,
    admit_long_sample,
)


def inspect_long_sample_readiness(
    evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Classify K552-F2 evidence without executing the long sample."""

    base = {
        "item_id": "K552-F2-long-sample",
        "execution_authorized": False,
        "counts_as_product_pass": False,
        "scope": "readiness inventory only; caller owns explicit 32Task/1200s run",
    }
    if evidence is None:
        return {
            **base,
            "status": "not_ready",
            "reason": "missing_reentry_evidence",
        }
    required = (
        "source_kind", "reference", "sha256", "complete_window_replay",
        "budget_compatible",
    )
    missing = [name for name in required if name not in evidence]
    if missing:
        return {
            **base,
            "status": "not_ready",
            "reason": "missing_reentry_fields",
            "missing": missing,
        }
    try:
        admitted = admit_long_sample(AllocationReentryEvidence(
            source_kind=evidence["source_kind"],
            reference=evidence["reference"],
            sha256=evidence["sha256"],
            complete_window_replay=evidence["complete_window_replay"],
            budget_compatible=evidence["budget_compatible"],
        ))
    except (TypeError, ValueError) as exc:
        return {
            **base,
            "status": "not_ready",
            "reason": "invalid_reentry_evidence",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    return {
        **base,
        "status": "ready_for_operator_run",
        "source_kind": admitted["source_kind"],
        "reference": admitted["reference"],
        "sha256": admitted["sha256"],
        "next_step": "operator must run the fixed single-Tracker 32Task/1200s tier",
    }


def summarize_open_item_readiness(
    *,
    long_sample_evidence: Mapping[str, Any] | None = None,
    same_signature_scene_present: bool = False,
    destructive_host_capability_present: bool = False,
) -> dict[str, Any]:
    """Produce a non-authorizing snapshot for the current open-item set."""

    long_sample = inspect_long_sample_readiness(long_sample_evidence)
    return {
        "schema_version": "zf-testkit-open-item-readiness.v1",
        "items": {
            "K552-F2-long-sample": long_sample,
            "K530/K584-R-same-signature-scene": {
                "status": "ready_for_operator_run" if same_signature_scene_present else "not_ready",
                "execution_authorized": False,
                "counts_as_product_pass": False,
                "reason": (
                    "operator must still provide exact signature diagnostics"
                    if same_signature_scene_present
                    else "missing exact same-signature new scene"
                ),
            },
            "destructive-OS-host-tier": {
                "status": "ready_for_operator_run" if destructive_host_capability_present else "not_ready",
                "execution_authorized": False,
                "counts_as_product_pass": False,
                "reason": (
                    "operator must provide explicit host approval and capability receipt"
                    if destructive_host_capability_present
                    else "missing dedicated host capability/approval"
                ),
            },
            "final-unified-acceptance": {
                "status": "blocked_by_open_items",
                "execution_authorized": False,
                "counts_as_product_pass": False,
            },
        },
        "release_authorized": False,
        "scope": "read-only readiness; no provider/VM/Memray/fault execution",
    }

