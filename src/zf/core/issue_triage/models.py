"""Provider-neutral models for the read-only Issue Triage mirror."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import re
from typing import Any

CLASSIFICATION_LABELS = frozenset({
    "runtime",
    "kernel/state",
    "provider/integration",
    "web/ui",
    "configuration",
    "security",
    "performance",
    "test/regression",
    "unknown",
})
SEVERITY_LABELS = frozenset({"p0", "p1", "p2", "p3"})
_LABEL_COLOR_RE = re.compile(r"^[0-9a-fA-F]{6}$")


def _required_text(value: object, name: str, *, maximum: int = 500) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise ValueError(f"invalid {name}")
    return text


def _optional_text(value: object, *, maximum: int = 500) -> str:
    text = str(value or "").strip()
    if len(text) > maximum:
        raise ValueError("field exceeds safe limit")
    return text


def _iso_timestamp(value: object, name: str, *, optional: bool = False) -> str:
    text = str(value or "").strip()
    if optional and not text:
        return ""
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {name}") from exc
    return text


def derived_triage_group(state: str, labels: tuple[str, ...]) -> str:
    if state == "closed":
        return "closed"
    normalized = {item.casefold() for item in labels}
    if normalized & CLASSIFICATION_LABELS and normalized & SEVERITY_LABELS:
        return "triaged"
    return "untriaged"


@dataclass(frozen=True)
class IssueMirror:
    issue_key: str
    provider: str
    repository_id: str
    repository: str
    number: int
    node_id: str
    html_url: str
    title: str
    author_login: str
    github_state: str
    created_at: str
    updated_at: str
    closed_at: str = ""
    labels: tuple[str, ...] = field(default_factory=tuple)
    label_colors: dict[str, str] = field(default_factory=dict)
    assignees: tuple[str, ...] = field(default_factory=tuple)
    assignee_avatar_urls: dict[str, str] = field(default_factory=dict)
    comment_count: int = 0
    milestone: str = ""
    source: str = "unknown"
    body_digest: str = ""
    body_ref: str = ""
    comments_digest: str = ""
    comments_ref: str = ""
    derived_group: str = "untriaged"
    last_seen_at: str = ""
    author_avatar_url: str = ""

    def validate(self) -> None:
        if self.provider != "github":
            raise ValueError("Issue Triage P0 supports GitHub mirrors only")
        _required_text(self.repository_id, "repository_id", maximum=64)
        _required_text(self.repository, "repository", maximum=200)
        if self.issue_key != f"github:{self.repository_id}:{self.number}":
            raise ValueError("invalid issue_key")
        if self.number < 1:
            raise ValueError("invalid issue number")
        _required_text(self.node_id, "node_id", maximum=200)
        expected_url = f"https://github.com/{self.repository}/issues/{self.number}"
        if self.html_url != expected_url:
            raise ValueError("unexpected GitHub Issue URL")
        _required_text(self.title, "title", maximum=1024)
        _required_text(self.author_login, "author_login", maximum=200)
        avatar_url = _optional_text(self.author_avatar_url, maximum=500)
        if avatar_url and not avatar_url.startswith("https://"):
            raise ValueError("invalid author avatar URL")
        if self.github_state not in {"open", "closed"}:
            raise ValueError("invalid GitHub state")
        _iso_timestamp(self.created_at, "created_at")
        _iso_timestamp(self.updated_at, "updated_at")
        _iso_timestamp(self.closed_at, "closed_at", optional=True)
        _iso_timestamp(self.last_seen_at, "last_seen_at")
        if self.comment_count < 0:
            raise ValueError("invalid comment count")
        if len(self.labels) > 100 or len(self.assignees) > 100:
            raise ValueError("Issue metadata exceeds safe limit")
        if any(name not in self.assignees for name in self.assignee_avatar_urls):
            raise ValueError("Issue assignee avatar metadata contains an unknown assignee")
        if any(not url.startswith("https://") for url in self.assignee_avatar_urls.values()):
            raise ValueError("invalid assignee avatar URL")
        if len(self.label_colors) > len(self.labels):
            raise ValueError("Issue label color metadata exceeds labels")
        if any(name not in self.labels for name in self.label_colors):
            raise ValueError("Issue label color metadata contains an unknown label")
        if any(not _LABEL_COLOR_RE.fullmatch(color) for color in self.label_colors.values()):
            raise ValueError("invalid GitHub label color")
        if self.derived_group != derived_triage_group(self.github_state, self.labels):
            raise ValueError("invalid derived triage group")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        value = asdict(self)
        value["labels"] = list(self.labels)
        value["label_colors"] = dict(self.label_colors)
        value["assignees"] = list(self.assignees)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> IssueMirror:
        raw_label_colors = value.get("label_colors")
        if not isinstance(raw_label_colors, dict):
            raw_label_colors = {}
        raw_assignee_avatars = value.get("assignee_avatar_urls")
        if not isinstance(raw_assignee_avatars, dict):
            raw_assignee_avatars = {}
        item = cls(
            issue_key=str(value.get("issue_key") or ""),
            provider=str(value.get("provider") or ""),
            repository_id=str(value.get("repository_id") or ""),
            repository=str(value.get("repository") or ""),
            number=int(value.get("number") or 0),
            node_id=str(value.get("node_id") or ""),
            html_url=str(value.get("html_url") or ""),
            title=str(value.get("title") or ""),
            author_login=str(value.get("author_login") or ""),
            github_state=str(value.get("github_state") or ""),
            created_at=str(value.get("created_at") or ""),
            updated_at=str(value.get("updated_at") or ""),
            closed_at=str(value.get("closed_at") or ""),
            labels=tuple(str(item) for item in value.get("labels") or ()),
            label_colors={
                str(name): str(color).lstrip("#")
                for name, color in raw_label_colors.items()
                if str(name) and str(color).lstrip("#")
            },
            assignees=tuple(str(item) for item in value.get("assignees") or ()),
            assignee_avatar_urls={
                str(name): str(url)
                for name, url in raw_assignee_avatars.items()
                if str(name) and str(url)
            },
            comment_count=int(value.get("comment_count") or 0),
            milestone=str(value.get("milestone") or ""),
            source=str(value.get("source") or "unknown"),
            body_digest=str(value.get("body_digest") or ""),
            body_ref=str(value.get("body_ref") or ""),
            comments_digest=str(value.get("comments_digest") or ""),
            comments_ref=str(value.get("comments_ref") or ""),
            derived_group=str(value.get("derived_group") or "untriaged"),
            last_seen_at=str(value.get("last_seen_at") or ""),
            author_avatar_url=str(value.get("author_avatar_url") or ""),
        )
        item.validate()
        return item


@dataclass(frozen=True)
class IssueComment:
    id: int
    node_id: str
    html_url: str
    author_login: str
    author_avatar_url: str
    body: str
    created_at: str
    updated_at: str
    author_association: str = ""

    def validate(self) -> None:
        if self.id < 1:
            raise ValueError("invalid GitHub comment id")
        _required_text(self.node_id, "comment node_id", maximum=200)
        if not self.html_url.startswith("https://github.com/"):
            raise ValueError("invalid GitHub comment URL")
        _required_text(self.author_login, "comment author", maximum=200)
        if self.author_avatar_url and not self.author_avatar_url.startswith("https://"):
            raise ValueError("invalid comment author avatar URL")
        if len(self.body.encode("utf-8")) > 1_000_000:
            raise ValueError("GitHub comment body exceeds safe limit")
        _iso_timestamp(self.created_at, "comment created_at")
        _iso_timestamp(self.updated_at, "comment updated_at")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> IssueComment:
        item = cls(
            id=int(value.get("id") or 0),
            node_id=str(value.get("node_id") or ""),
            html_url=str(value.get("html_url") or ""),
            author_login=str(value.get("author_login") or ""),
            author_avatar_url=str(value.get("author_avatar_url") or ""),
            body=str(value.get("body") or ""),
            created_at=str(value.get("created_at") or ""),
            updated_at=str(value.get("updated_at") or ""),
            author_association=str(value.get("author_association") or ""),
        )
        item.validate()
        return item


@dataclass(frozen=True)
class SyncState:
    status: str = "never"
    repository: str = ""
    repository_id: str = ""
    last_attempt_at: str = ""
    last_success_at: str = ""
    last_full_sync_at: str = ""
    etag: str = ""
    rate_limit_remaining: int | None = None
    rate_limit_reset_at: str = ""
    error: str = ""
    star_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SyncState:
        return cls(
            status=str(value.get("status") or "never"),
            repository=str(value.get("repository") or ""),
            repository_id=str(value.get("repository_id") or ""),
            last_attempt_at=str(value.get("last_attempt_at") or ""),
            last_success_at=str(value.get("last_success_at") or ""),
            last_full_sync_at=str(value.get("last_full_sync_at") or ""),
            etag=str(value.get("etag") or ""),
            rate_limit_remaining=(
                int(value["rate_limit_remaining"])
                if value.get("rate_limit_remaining") is not None else None
            ),
            rate_limit_reset_at=str(value.get("rate_limit_reset_at") or ""),
            error=str(value.get("error") or ""),
            star_count=max(0, int(value.get("star_count") or 0)),
        )
