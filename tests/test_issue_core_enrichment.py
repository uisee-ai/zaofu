from __future__ import annotations

import json
from pathlib import Path

import pytest

from issue_core.enrichment import (
    Claim,
    Conflict,
    DraftFieldState,
    DraftRevision,
    EvaluationRun,
    Evidence,
    Gap,
    IssueDraftV3,
    Resolution,
    Suggestion,
    dismiss_suggestion,
    stable_content_digest,
)
from issue_core.publication import HandoffBundle, HandoffItem, PublicationSnapshot


def test_field_state_user_edit_locks_the_whole_field_even_when_cleared() -> None:
    field = DraftFieldState(
        field_path="actual_behavior",
        value="The board remains unchanged.",
        source="structure",
    )
    edited = field.user_edit("")
    assert edited.value == ""
    assert edited.user_locked is True
    assert edited.auto_fill_eligible is False


def test_issue_draft_v3_digest_and_user_edit_are_revision_bound() -> None:
    draft = IssueDraftV3.create(
        draft_id="draft-1",
        fields={
            "title": DraftFieldState(
                field_path="title", value="Board freezes", source="structure",
            ),
            "actual_behavior": DraftFieldState(
                field_path="actual_behavior", value="", source="structure",
            ),
        },
        understanding_status="completed",
    )
    edited = draft.user_edit(
        expected_revision=1,
        changes={"actual_behavior": "The board remains unchanged."},
    )
    assert edited.revision == 2
    assert edited.digest != draft.digest
    assert edited.fields["actual_behavior"].user_locked is True
    with pytest.raises(ValueError, match="Draft revision changed"):
        edited.user_edit(expected_revision=1, changes={"title": "New title"})
    assert IssueDraftV3.from_dict(edited.to_dict()) == edited
    root = Path(__file__).parents[1]
    canonical_schema = json.loads(
        (root / "schemas" / "issue-draft.v3.json").read_text(encoding="utf-8")
    )
    portable_schema = json.loads(
        (root / "skills" / "issue" / "schemas" / "issue-draft.v3.json")
        .read_text(encoding="utf-8")
    )
    assert canonical_schema == portable_schema


def test_claim_and_evidence_keep_fact_kind_integrity_and_applicability_separate() -> None:
    evidence = Evidence(
        evidence_id="evidence-1",
        kind="terminal_log",
        uri="artifact://self-issue/draft-1/terminal-log",
        digest="sha256:" + "a" * 64,
        collected_by="investigation-1",
        project_revision="git:abc123",
        captured_at="2026-09-18T00:00:00+00:00",
        disclosure="private",
        redaction_status="checked",
        integrity="verified",
        applicability="current",
        environment_fingerprint="sha256:" + "b" * 64,
        supports=("claim-1",),
    )
    claim = Claim(
        claim_id="claim-1",
        draft_id="draft-1",
        field_path="actual_behavior",
        value="CLI exits with ModuleNotFoundError: httpx",
        kind="observed",
        source={
            "type": "investigation",
            "actor_id": "investigation-1",
            "source_ref": "evidence-1",
        },
        confidence="high",
        verification_status="verified",
        valid_from_revision=2,
        dependency_refs=("input.environment.python_version",),
        created_at="2026-09-18T00:00:00+00:00",
    )
    assert evidence.to_dict()["integrity"] == "verified"
    assert evidence.mark_stale("project revision changed").to_dict()["applicability"] == "stale"
    assert claim.to_dict()["kind"] == "observed"


def test_suggestion_schema_has_no_rejected_state_and_dismissal_is_content_free() -> None:
    suggestion = Suggestion(
        suggestion_id="suggestion-1",
        draft_id="draft-1",
        based_on_revision=3,
        based_on_digest="sha256:" + "c" * 64,
        field_path="actual_behavior",
        current_value="The page does not update.",
        proposed_value="The request remains pending for 30 seconds.",
        reason="A browser trace observed the pending request.",
        claim_refs=("claim-1",),
        evidence_refs=("evidence-1",),
        confidence="high",
    )
    dismissal = dismiss_suggestion(
        suggestion,
        actor="reporter-1",
        current_revision=3,
        dismissed_at="2026-09-18T00:00:00+00:00",
    )
    value = dismissal.to_dict()
    assert value == {
        "schema_version": "suggestion-dismissal.v1",
        "suggestion_id": "suggestion-1",
        "draft_id": "draft-1",
        "actor": "reporter-1",
        "revision": 3,
        "status": "dismissed",
        "dismissed_at": "2026-09-18T00:00:00+00:00",
    }
    serialized = json.dumps(value)
    assert "proposed_value" not in serialized
    assert "browser trace" not in serialized
    with pytest.raises(ValueError, match="unsupported suggestion status"):
        Suggestion.from_dict({**suggestion.to_dict(), "status": "rejected"})


def test_evaluation_cancel_revokes_commit_capability_and_late_run_cannot_commit() -> None:
    run = EvaluationRun.start(
        run_id="evaluation-1",
        draft_id="draft-1",
        draft_revision=4,
        draft_digest="sha256:" + "d" * 64,
        commit_capability="cap-secret",
        started_at="2026-09-18T00:00:00+00:00",
    )
    assert run.can_commit(
        capability="cap-secret", draft_revision=4,
        draft_digest="sha256:" + "d" * 64,
    )
    cancelled = run.cancel(cancelled_at="2026-09-18T00:01:00+00:00")
    assert cancelled.status == "cancelled"
    assert cancelled.commit_capability == ""
    assert not cancelled.can_commit(
        capability="cap-secret", draft_revision=4,
        draft_digest="sha256:" + "d" * 64,
    )


def test_revision_gap_conflict_and_resolution_round_trip() -> None:
    fields = {
        "title": "Board freezes",
        "actual_behavior": "No visible update",
    }
    revision = DraftRevision(
        draft_id="draft-1",
        revision=2,
        parent_revision=1,
        actor="reporter-1",
        changed_fields=("actual_behavior",),
        change_reason="user_edit",
        content_digest=stable_content_digest(fields),
        created_at="2026-09-18T00:00:00+00:00",
    )
    gap = Gap(
        gap_id="gap-1",
        draft_id="draft-1",
        field_path="expected_behavior",
        status="missing",
        acquisition="ask_user",
        blocking_level="blocks_readiness",
        reason="Only the reporter can state the intended outcome.",
    )
    conflict = Conflict(
        conflict_id="conflict-1",
        draft_id="draft-1",
        based_on_revision=2,
        based_on_digest=revision.content_digest,
        field_path="actual_behavior",
        left_claim_ref="claim-reported",
        right_claim_ref="claim-observed",
        reason="mutually_exclusive",
    )
    resolution = Resolution(
        resolution_id="resolution-1",
        suggestion_or_conflict_id="conflict-1",
        action="resolved_both",
        resolved_by="reporter-1",
        based_on_revision=2,
        resulting_revision=3,
        created_at="2026-09-18T00:02:00+00:00",
    )
    assert DraftRevision.from_dict(revision.to_dict()) == revision
    assert Gap.from_dict(gap.to_dict()) == gap
    assert Conflict.from_dict(conflict.to_dict()) == conflict
    assert Resolution.from_dict(resolution.to_dict()) == resolution


def test_portable_enrichment_module_has_no_host_imports() -> None:
    source = (
        Path(__file__).parents[1] / "src" / "issue_core" / "enrichment.py"
    ).read_text(encoding="utf-8")
    assert "import zf" not in source
    assert "from zf" not in source


def test_publication_snapshot_and_private_handoff_are_revision_bound() -> None:
    snapshot = PublicationSnapshot(
        draft_id="draft-1",
        draft_revision=5,
        provider="github",
        payload_digest="sha256:" + "1" * 64,
        draft_digest="sha256:" + "2" * 64,
        attachment_manifest_digest="sha256:" + "3" * 64,
        target_project="owner/repo",
        understanding_status="completed",
        assessment_status="completed",
        unresolved_publication_policy="handoff_private",
    )
    item = HandoffItem(
        item_id="suggestion-1",
        item_type="suggestion",
        field_path="actual_behavior",
        status="pending",
        claim_refs=("claim-1",),
        evidence_refs=("evidence-1",),
    )
    bundle = HandoffBundle(
        handoff_id="handoff-1",
        provider="github",
        repository="owner/repo",
        remote_issue_id="52",
        publication_snapshot_digest="sha256:" + "4" * 64,
        source_revision="remote:etag-1",
        unresolved_items=(item,),
        artifact_ref="artifacts/self-issues/draft-1/handoff.json",
    )
    snapshot.validate()
    bundle.validate()
    assert bundle.to_dict()["unresolved_items"][0]["item_id"] == "suggestion-1"
