"""Deterministic contracts for K530/K584-R same-signature re-entry."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.testkit.incident_reentry import (
    IncidentReentryEvidence,
    K530_WATCHDOG_PROFILE,
    K584_HANDSHAKE_PROFILE,
    admit_incident_reentry,
)


def _evidence(profile):
    fields = {name: f"{name}-value" for name in profile.required_fields}
    fields.update({"pid": 4312, "started_at_ns": 1001})
    return IncidentReentryEvidence(
        issue_id=profile.issue_id,
        signature=profile.signature,
        source_ref="attachments/new-scene/testkit-run.json",
        source_sha256="a" * 64,
        observed_at_ns=2_000,
        fields=fields,
    )


@pytest.mark.parametrize("profile", [K530_WATCHDOG_PROFILE, K584_HANDSHAKE_PROFILE])
def test_same_signature_failure_with_complete_diagnostics_is_admitted(profile):
    result = admit_incident_reentry(profile, _evidence(profile))
    assert result["status"] == "admitted"
    assert result["issue_id"] == profile.issue_id
    assert result["scope"].startswith("admission only")


@pytest.mark.parametrize("mutation", [
    "wrong_issue", "wrong_signature", "historical", "normal_control", "missing_source",
    "bad_digest", "bad_timestamp", "missing_diagnostic",
])
def test_reentry_rejects_normal_history_and_incomplete_signatures(mutation):
    profile = K530_WATCHDOG_PROFILE
    evidence = _evidence(profile)
    if mutation == "wrong_issue":
        evidence = replace(evidence, issue_id="other")
    elif mutation == "wrong_signature":
        evidence = replace(evidence, signature="worker.retry.completed")
    elif mutation == "historical":
        evidence = replace(evidence, new_observation=False)
    elif mutation == "normal_control":
        evidence = replace(evidence, failure_observed=False, normal_control_only=True)
    elif mutation == "missing_source":
        evidence = replace(evidence, source_ref=" ")
    elif mutation == "bad_digest":
        evidence = replace(evidence, source_sha256="not-a-digest")
    elif mutation == "bad_timestamp":
        evidence = replace(evidence, observed_at_ns=0)
    else:
        fields = dict(evidence.fields)
        fields.pop("snapshot_ref")
        evidence = replace(evidence, fields=fields)
    with pytest.raises(ValueError):
        admit_incident_reentry(profile, evidence)
