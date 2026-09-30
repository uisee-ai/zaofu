from __future__ import annotations

import json

import pytest

from issue_core import (
    AttachmentRef,
    InMemoryIssueSessionStore,
    IssueReportSnapshot,
    IssueSessionRunner,
    JsonIssueSessionStore,
)


def _ready(store):
    session = store.create(
        "Refresh fails",
        attachments=(AttachmentRef(ref="att-1", name="trace.txt", digest="sha256:trace"),),
    )
    working = store.load_working_set(session.session_id)
    working = working.__class__(
        session_id=session.session_id,
        revision=session.revision,
        claims=({"ref": "claim-1", "value": "reported"},),
        evidences=({"ref": "evidence-1", "summary": "pending"},),
        report={"title": "Refresh remains pending"},
    ).with_digest()
    store.save_working_set(working, expected_revision=1)
    return store.load_session(session.session_id), working


def test_snapshot_binds_report_evidence_attachments_and_confirmation() -> None:
    store = InMemoryIssueSessionStore()
    session, working = _ready(store)
    snapshot = IssueReportSnapshot.from_session(
        session,
        working,
        confirmed_by="alice",
        confirmed_at="2026-09-20T00:00:00+00:00",
        provider="github",
        target="org/repo",
    )
    snapshot.validate()
    assert snapshot.report_digest.startswith("sha256:")
    assert snapshot.evidence_digest.startswith("sha256:")
    assert snapshot.attachment_manifest_digest.startswith("sha256:")
    assert snapshot.selected_claim_refs == ("claim-1",)
    assert snapshot.selected_evidence_refs == ("evidence-1",)
    restored = IssueReportSnapshot.from_dict(json.loads(json.dumps(snapshot.to_dict())))
    assert restored.digest == snapshot.digest


def test_json_store_persists_publication_snapshot(tmp_path) -> None:
    first = JsonIssueSessionStore(tmp_path)
    session, working = _ready(first)
    snapshot = IssueReportSnapshot.from_session(
        session,
        working,
        confirmed_by="alice",
        confirmed_at="2026-09-20T00:00:00+00:00",
    )
    first.save_publication_snapshot(snapshot)
    second = JsonIssueSessionStore(tmp_path)
    assert second.load_publication_snapshot(session.session_id).digest == snapshot.digest


def test_snapshot_rejects_mutation_after_digest() -> None:
    store = InMemoryIssueSessionStore()
    session, working = _ready(store)
    snapshot = IssueReportSnapshot.from_session(
        session,
        working,
        confirmed_by="alice",
        confirmed_at="2026-09-20T00:00:00+00:00",
    )
    with pytest.raises(ValueError, match="report digest"):
        IssueReportSnapshot.from_dict({**snapshot.to_dict(), "report": {"title": "tampered"}})
