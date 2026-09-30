from __future__ import annotations

import json
from pathlib import Path

import pytest

from issue_core.enrichment import Claim, Evidence, Suggestion
from zf.core.self_issue.enrichment_store import (
    EnrichmentProjection,
    EnrichmentRevisionConflict,
    IssueEnrichmentStore,
)
from zf.core.self_issue.models import IssueDraft
from zf.core.self_issue.store import IssueDraftStore


def _claim() -> Claim:
    return Claim(
        claim_id="claim-1",
        draft_id="draft-1",
        field_path="actual_behavior",
        value="The request remains pending.",
        kind="observed",
        source={"type": "investigation", "source_ref": "evidence-1"},
        confidence="high",
        verification_status="verified",
        valid_from_revision=2,
        dependency_refs=("input.reproduction.command",),
        created_at="2026-09-18T00:00:00+00:00",
    )


def _evidence() -> Evidence:
    return Evidence(
        evidence_id="evidence-1",
        kind="browser_trace",
        uri="artifact://self-issue/draft-1/trace",
        digest="sha256:" + "a" * 64,
        collected_by="evaluation-1",
        project_revision="git:abc",
        captured_at="2026-09-18T00:00:00+00:00",
        disclosure="private",
        redaction_status="checked",
        integrity="verified",
        applicability="current",
        environment_fingerprint="sha256:" + "b" * 64,
        supports=("claim-1",),
    )


def _suggestion() -> Suggestion:
    return Suggestion(
        suggestion_id="suggestion-1",
        draft_id="draft-1",
        based_on_revision=2,
        based_on_digest="sha256:" + "c" * 64,
        field_path="actual_behavior",
        current_value="No visible update.",
        proposed_value="The request remains pending.",
        reason="The browser trace observed it.",
        claim_refs=("claim-1",),
        evidence_refs=("evidence-1",),
        confidence="high",
    )


def test_enrichment_store_round_trips_without_copying_draft_field_values(tmp_path: Path) -> None:
    store = IssueEnrichmentStore(tmp_path / "enrichment.json")
    projection = EnrichmentProjection(
        draft_id="draft-1",
        draft_revision=2,
        draft_digest="sha256:" + "c" * 64,
        field_metadata={
            "actual_behavior": {
                "source": "reporter",
                "user_locked": True,
                "confidence": "high",
                "last_changed_revision": 2,
                "evidence_refs": [],
            },
        },
        claims=(_claim(),),
        evidences=(_evidence(),),
        suggestions=(_suggestion(),),
    )
    store.save(projection)
    loaded = store.get("draft-1")
    assert loaded == projection
    raw = json.loads((tmp_path / "enrichment.json").read_text(encoding="utf-8"))
    assert "value" not in raw["drafts"]["draft-1"]["field_metadata"]["actual_behavior"]


def test_enrichment_store_cas_rejects_stale_projection(tmp_path: Path) -> None:
    store = IssueEnrichmentStore(tmp_path / "enrichment.json")
    projection = EnrichmentProjection(
        draft_id="draft-1",
        draft_revision=2,
        draft_digest="sha256:" + "c" * 64,
    )
    store.save(projection)
    with pytest.raises(EnrichmentRevisionConflict):
        store.save(
            projection,
            expected_draft_revision=1,
            expected_draft_digest="sha256:" + "0" * 64,
        )


def test_dismiss_removes_suggestion_semantics_but_keeps_claim_and_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "enrichment.json"
    store = IssueEnrichmentStore(path)
    store.save(EnrichmentProjection(
        draft_id="draft-1",
        draft_revision=2,
        draft_digest="sha256:" + "c" * 64,
        claims=(_claim(),),
        evidences=(_evidence(),),
        suggestions=(_suggestion(),),
    ))
    updated, dismissal = store.dismiss_suggestion(
        draft_id="draft-1",
        suggestion_id="suggestion-1",
        actor="reporter-1",
        expected_draft_revision=2,
        expected_draft_digest="sha256:" + "c" * 64,
        dismissed_at="2026-09-18T00:01:00+00:00",
    )
    assert updated.suggestions == ()
    assert len(updated.claims) == 1
    assert len(updated.evidences) == 1
    assert dismissal.status == "dismissed"
    persisted = path.read_text(encoding="utf-8")
    assert "The browser trace observed it" not in persisted
    assert "The request remains pending" in persisted  # independent Claim remains


def test_canonical_draft_store_syncs_only_revision_digest_and_metadata(tmp_path: Path) -> None:
    enrichment = IssueEnrichmentStore(tmp_path / "enrichment.json")
    drafts = IssueDraftStore(tmp_path / "drafts.json", on_save=enrichment.sync_draft)
    draft = IssueDraft(
        draft_id="draft-legacy",
        subject_scope="project",
        target_binding={"provider": "github", "project": "owner/repo"},
        incident_fingerprint="fingerprint",
        title="Board freezes",
        summary="The board remains unchanged.",
    )
    drafts.save(draft)
    projection = enrichment.get(draft.draft_id)
    assert projection is not None
    assert projection.draft_revision == draft.revision
    assert projection.draft_digest == enrichment.draft_digest(draft)
    persisted = json.loads((tmp_path / "enrichment.json").read_text(encoding="utf-8"))
    assert "Board freezes" not in json.dumps(persisted)
