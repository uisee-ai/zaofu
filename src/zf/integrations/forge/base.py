"""Provider-neutral Forge issue contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class IssuePublishRequest:
    project: str
    title: str
    body: str
    labels: tuple[str, ...]
    marker: str


@dataclass(frozen=True)
class PublishedIssue:
    provider: str
    project: str
    number: str
    url: str


@dataclass(frozen=True)
class AttachmentUploadRequest:
    project: str
    filename: str
    content_type: str
    content: bytes
    digest: str


@dataclass(frozen=True)
class UploadedAttachment:
    provider: str
    project: str
    filename: str
    markdown: str
    url: str
    upload_id: str = ""


@dataclass(frozen=True)
class ForgeResult:
    status: str
    issue: PublishedIssue | None = None
    reason: str = ""
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    attachment: UploadedAttachment | None = None
    label_colors: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ForgeCapabilities:
    issue_publication: bool = True
    marker_recovery: bool = True
    binary_attachment_upload: bool = False


class ForgeProvider(Protocol):
    name: str
    capabilities: ForgeCapabilities

    def publish(
        self, request: IssuePublishRequest, *, access_token: str,
    ) -> ForgeResult: ...
    def upload_attachment(
        self, request: AttachmentUploadRequest, *, access_token: str,
    ) -> ForgeResult: ...
    def find_by_marker(
        self, project: str, marker: str, *, access_token: str,
    ) -> list[PublishedIssue]: ...
    def reconcile_issue_labels(
        self,
        project: str,
        issue_number: int,
        labels: tuple[str, ...],
        *,
        access_token: str,
    ) -> ForgeResult: ...
    def reconcile_lifecycle_label(
        self,
        project: str,
        issue_number: int,
        state: str,
        *,
        access_token: str,
    ) -> ForgeResult: ...
