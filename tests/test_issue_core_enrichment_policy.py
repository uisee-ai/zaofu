from issue_core.enrichment import Claim, DraftFieldState, Evidence
from issue_core.enrichment_policy import can_auto_fill


def _evidence(*, applicability: str = "current") -> Evidence:
    return Evidence(
        evidence_id="e-1", kind="reproduction", uri="artifact://run/1",
        digest="sha256:" + "a" * 64, collected_by="run-1",
        project_revision="git:abc", captured_at="2026-09-18T00:00:00Z",
        disclosure="private", redaction_status="checked", integrity="verified",
        applicability=applicability, environment_fingerprint="sha256:" + "b" * 64,
        stale_reason="changed" if applicability == "stale" else "",
    )


def _claim(*, kind: str = "observed", status: str = "verified") -> Claim:
    return Claim(
        claim_id="c-1", draft_id="d-1", field_path="actual_behavior",
        value="The request remains pending.", kind=kind,
        source={"type": "investigation", "run_id": "run-1"}, confidence="high",
        verification_status=status, valid_from_revision=2,
        dependency_refs=("e-1",), created_at="2026-09-18T00:00:00Z",
    )


def test_only_empty_unlocked_field_can_be_auto_filled_from_current_verified_observation() -> None:
    eligible = DraftFieldState(field_path="actual_behavior")
    assert can_auto_fill(eligible, claim=_claim(), evidence=_evidence())
    assert not can_auto_fill(
        eligible.user_edit("", revision=3), claim=_claim(), evidence=_evidence(),
    )
    assert not can_auto_fill(
        DraftFieldState(field_path="expected_behavior"),
        claim=_claim(), evidence=_evidence(),
    )
    assert not can_auto_fill(eligible, claim=_claim(), evidence=_evidence(applicability="stale"))
    assert not can_auto_fill(eligible, claim=_claim(kind="inferred"), evidence=_evidence())
