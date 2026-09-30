"""Provider-neutral Publish → Connect/Publishing → View state machine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping


PUBLICATION_STATES = frozenset({
    "previewed", "connect_required", "publishing", "partially_published",
    "view", "outcome_unknown",
})


@dataclass(frozen=True)
class PublicationSnapshot:
    draft_id: str
    draft_revision: int
    provider: str
    payload_digest: str
    draft_digest: str = ""
    attachment_manifest_digest: str = ""
    target_project: str = ""
    understanding_status: str = "unknown"
    assessment_status: str = "not_started"
    unresolved_publication_policy: str = "handoff_private"
    limited_report_acknowledged: bool = False
    selected_claim_refs: tuple[str, ...] = ()
    selected_evidence_refs: tuple[str, ...] = ()
    schema_version: str = "publication-snapshot.v1"

    def validate(self) -> None:
        if self.schema_version != "publication-snapshot.v1":
            raise ValueError("unsupported publication snapshot schema")
        if not self.draft_id or self.draft_revision < 1:
            raise ValueError("publication snapshot must identify a Draft revision")
        if not self.provider or not self.payload_digest:
            raise ValueError("publication snapshot requires provider and payload digest")
        if self.unresolved_publication_policy not in {"handoff_private", "block"}:
            raise ValueError("unsupported unresolved publication policy")
        if not all(self.selected_claim_refs) or not all(self.selected_evidence_refs):
            raise ValueError("publication snapshot refs must contain ids")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            **asdict(self),
            "selected_claim_refs": list(self.selected_claim_refs),
            "selected_evidence_refs": list(self.selected_evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PublicationSnapshot":
        if str(value.get("schema_version") or "") != "publication-snapshot.v1":
            raise ValueError("unsupported publication snapshot schema")
        snapshot = cls(
            **{
                **dict(value),
                "selected_claim_refs": tuple(value.get("selected_claim_refs") or ()),
                "selected_evidence_refs": tuple(value.get("selected_evidence_refs") or ()),
            },
        )
        snapshot.validate()
        return snapshot


HANDOFF_ITEM_TYPES = frozenset({"suggestion", "conflict", "gap"})
HANDOFF_ITEM_STATUSES = frozenset({"pending", "accepted", "dismissed", "stale"})


@dataclass(frozen=True)
class HandoffItem:
    item_id: str
    item_type: str
    field_path: str
    status: str
    claim_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    schema_version: str = "handoff-item.v1"

    def validate(self) -> None:
        if self.schema_version != "handoff-item.v1":
            raise ValueError("unsupported Handoff item schema")
        if not self.item_id or not self.field_path:
            raise ValueError("Handoff item requires id and field path")
        if self.item_type not in HANDOFF_ITEM_TYPES:
            raise ValueError("unsupported Handoff item type")
        if self.status not in HANDOFF_ITEM_STATUSES:
            raise ValueError("unsupported Handoff item status")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            **asdict(self),
            "claim_refs": list(self.claim_refs),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HandoffItem":
        if str(value.get("schema_version") or "") != "handoff-item.v1":
            raise ValueError("unsupported Handoff item schema")
        item = cls(
            **{
                **dict(value),
                "claim_refs": tuple(value.get("claim_refs") or ()),
                "evidence_refs": tuple(value.get("evidence_refs") or ()),
            },
        )
        item.validate()
        return item


@dataclass(frozen=True)
class HandoffBundle:
    """Private unresolved-item bundle bound to one exact remote Issue."""

    handoff_id: str
    provider: str
    repository: str
    remote_issue_id: str
    publication_snapshot_digest: str
    source_revision: str
    unresolved_items: tuple[HandoffItem, ...]
    artifact_ref: str
    schema_version: str = "handoff-bundle.v1"

    def validate(self) -> None:
        if self.schema_version != "handoff-bundle.v1":
            raise ValueError("unsupported Handoff Bundle schema")
        if not all((
            self.handoff_id, self.provider, self.repository, self.remote_issue_id,
            self.publication_snapshot_digest, self.source_revision, self.artifact_ref,
        )):
            raise ValueError("Handoff Bundle remote binding is incomplete")
        if not self.publication_snapshot_digest.startswith("sha256:"):
            raise ValueError("Handoff Bundle requires a publication snapshot digest")
        for item in self.unresolved_items:
            item.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            **asdict(self),
            "unresolved_items": [item.to_dict() for item in self.unresolved_items],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HandoffBundle":
        if str(value.get("schema_version") or "") != "handoff-bundle.v1":
            raise ValueError("unsupported Handoff Bundle schema")
        bundle = cls(
            **{
                **dict(value),
                "unresolved_items": tuple(
                    HandoffItem.from_dict(item)
                    for item in value.get("unresolved_items") or ()
                ),
            },
        )
        bundle.validate()
        return bundle


@dataclass(frozen=True)
class PublicationState:
    snapshot: PublicationSnapshot
    status: str = "previewed"
    authorization_code: str = ""
    issue_url: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        self.snapshot.validate()
        if self.status not in PUBLICATION_STATES:
            raise ValueError(f"unsupported publication state: {self.status}")

    def request_publish(
        self,
        snapshot: PublicationSnapshot,
        *,
        connected: bool,
        authorization_code: str = "",
    ) -> "PublicationState":
        """Handle the single user-facing Publish action.

        A changed Draft can never reuse an older intent.  When the provider is
        not connected, return ``connect_required`` and preserve the exact
        snapshot; when connected, enter ``publishing``.  Repeated requests are
        idempotent while publishing or after View.
        """

        snapshot.validate()
        if snapshot != self.snapshot:
            return replace(self, status="outcome_unknown", reason="stale_publication_snapshot")
        if self.status in {"publishing", "partially_published", "view", "outcome_unknown"}:
            return self
        if not connected:
            return replace(
                self,
                status="connect_required",
                authorization_code=str(authorization_code or self.authorization_code),
                reason="provider_authorization_required",
            )
        return replace(self, status="publishing", authorization_code="", reason="")

    def complete(self, issue_url: str) -> "PublicationState":
        if self.status not in {"publishing", "partially_published", "view"}:
            return replace(self, status="outcome_unknown", reason="publication_not_in_flight")
        url = str(issue_url or "").strip()
        if not url:
            return replace(self, status="outcome_unknown", reason="published_issue_url_missing")
        return replace(self, status="view", issue_url=url, reason="")

    def mark_partial(self, issue_url: str, reason: str) -> "PublicationState":
        """Record a remote Issue that exists while a post-create step is pending."""

        if self.status not in {"publishing", "partially_published"}:
            return replace(self, status="outcome_unknown", reason="publication_not_in_flight")
        url = str(issue_url or "").strip()
        if not url:
            return replace(self, status="outcome_unknown", reason="published_issue_url_missing")
        return replace(
            self,
            status="partially_published",
            issue_url=url,
            reason=str(reason or "post_create_step_pending"),
        )

    def fail_unknown(self, reason: str = "") -> "PublicationState":
        return replace(self, status="outcome_unknown", reason=str(reason or "outcome_unknown"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft_id": self.snapshot.draft_id,
            "draft_revision": self.snapshot.draft_revision,
            "provider": self.snapshot.provider,
            "payload_digest": self.snapshot.payload_digest,
            "status": self.status,
            "authorization_code": self.authorization_code,
            "issue_url": self.issue_url,
            "reason": self.reason,
        }


__all__ = [
    "HANDOFF_ITEM_STATUSES", "HANDOFF_ITEM_TYPES", "HandoffBundle", "HandoffItem",
    "PUBLICATION_STATES", "PublicationSnapshot", "PublicationState",
]
