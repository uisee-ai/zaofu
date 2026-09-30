"""Fail-closed contracts for final unified acceptance readiness."""

from __future__ import annotations

from copy import deepcopy

import pytest

from tests.testkit.unified_acceptance import evaluate_unified_acceptance


def _audit():
    return {
        "verdict": "pass",
        "gate_exit": 0,
        "same_revision_verified": True,
        "source_variant_count": 1,
        "requirements": [
            {"id": "core", "status": "pass", "source_verified": True},
            {"id": "web", "status": "pass", "source_verified": True},
        ],
    }


def test_unified_acceptance_returns_readiness_without_release_authorization():
    result = evaluate_unified_acceptance(_audit())
    assert result["status"] == "ready_for_operator_review"
    assert result["release_authorized"] is False
    assert result["requirement_count"] == 2


@pytest.mark.parametrize("mutation", [
    "verdict", "gate_exit", "source_revision", "source_variants", "missing", "harness_failure",
])
def test_unified_acceptance_rejects_incomplete_or_mixed_evidence(mutation):
    audit = deepcopy(_audit())
    if mutation == "verdict":
        audit["verdict"] = "incomplete"
    elif mutation == "gate_exit":
        audit["gate_exit"] = 1
    elif mutation == "source_revision":
        audit["same_revision_verified"] = False
    elif mutation == "source_variants":
        audit["source_variant_count"] = 2
    elif mutation == "missing":
        audit["requirements"][0]["status"] = "missing"
    else:
        audit["requirements"][1]["status"] = "harness_failure"
    with pytest.raises(ValueError):
        evaluate_unified_acceptance(audit)
