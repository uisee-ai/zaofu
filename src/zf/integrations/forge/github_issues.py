"""Read-only GitHub Issues API normalization and reconciliation."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from zf.core.issue_triage.models import (
    IssueComment,
    IssueMirror,
    SyncState,
    derived_triage_group,
)
from zf.core.issue_triage.store import IssueMirrorStore
from zf.core.state.locks import locked_path

ResponseHeaders = Mapping[str, str]
IssuesTransport = Callable[[str, str, dict[str, str]], tuple[int, bytes, ResponseHeaders]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat()


def _stdlib_transport(
    method: str,
    url: str,
    headers: dict[str, str],
) -> tuple[int, bytes, ResponseHeaders]:
    request = urllib.request.Request(url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            raw = response.read(10_000_001)
            if len(raw) > 10_000_000:
                raise ValueError("GitHub API response exceeds safe limit")
            return int(response.status), raw, dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read(1_000_000), dict(exc.headers.items())


def _object(raw: bytes, reason: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(reason) from exc
    if not isinstance(value, dict):
        raise ValueError(reason)
    return value


def _array(raw: bytes, reason: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(reason) from exc
    if not isinstance(value, list):
        raise ValueError(reason)
    return [dict(item) for item in value if isinstance(item, dict)]


def _text(value: object, *, maximum: int) -> str:
    text = str(value or "").strip()
    if len(text) > maximum:
        raise ValueError("GitHub Issue field exceeds safe limit")
    return text


def normalize_github_issue(
    payload: Mapping[str, Any],
    *,
    repository: str,
    repository_id: str,
    seen_at: str,
) -> tuple[IssueMirror, str]:
    if "pull_request" in payload:
        raise ValueError("pull_request_not_issue")
    number = int(payload.get("number") or 0)
    author = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    milestone_value = (
        payload.get("milestone") if isinstance(payload.get("milestone"), dict) else {}
    )
    label_values: list[str] = []
    label_colors: dict[str, str] = {}
    for item in (payload.get("labels") or []):
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = _text(item.get("name"), maximum=200)
        if name in label_values:
            continue
        label_values.append(name)
        color = _text(item.get("color"), maximum=20).lstrip("#")
        if len(color) == 6 and all(char in "0123456789abcdefABCDEF" for char in color):
            label_colors[name] = color.upper()
    labels = tuple(label_values)
    assignee_values: list[str] = []
    assignee_avatar_urls: dict[str, str] = {}
    for item in (payload.get("assignees") or []):
        if not isinstance(item, dict) or not item.get("login"):
            continue
        login = _text(item.get("login"), maximum=200)
        if login not in assignee_values:
            assignee_values.append(login)
        avatar_url = _text(item.get("avatar_url"), maximum=500)
        if avatar_url:
            assignee_avatar_urls[login] = avatar_url
    assignees = tuple(assignee_values)
    body = str(payload.get("body") or "")
    source = "self_issue" if "<!-- zf-self-issue:" in body else "github_web"
    state = _text(payload.get("state"), maximum=20).lower()
    mirror = IssueMirror(
        issue_key=f"github:{repository_id}:{number}",
        provider="github",
        repository_id=repository_id,
        repository=repository,
        number=number,
        node_id=_text(payload.get("node_id"), maximum=200),
        html_url=_text(payload.get("html_url"), maximum=500),
        title=_text(payload.get("title"), maximum=1024),
        author_login=_text(author.get("login"), maximum=200),
        github_state=state,
        created_at=_text(payload.get("created_at"), maximum=100),
        updated_at=_text(payload.get("updated_at"), maximum=100),
        closed_at=_text(payload.get("closed_at"), maximum=100),
        labels=labels,
        label_colors=label_colors,
        assignees=assignees,
        assignee_avatar_urls=assignee_avatar_urls,
        comment_count=int(payload.get("comments") or 0),
        milestone=_text(milestone_value.get("title"), maximum=500),
        source=source,
        derived_group=derived_triage_group(state, labels),
        last_seen_at=seen_at,
        author_avatar_url=_text(author.get("avatar_url"), maximum=500),
    )
    mirror.validate()
    return mirror, body


def normalize_github_comment(payload: Mapping[str, Any]) -> IssueComment:
    author = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    comment = IssueComment(
        id=int(payload.get("id") or 0),
        node_id=_text(payload.get("node_id"), maximum=200),
        html_url=_text(payload.get("html_url"), maximum=500),
        author_login=_text(author.get("login"), maximum=200),
        author_avatar_url=_text(author.get("avatar_url"), maximum=500),
        body=str(payload.get("body") or ""),
        created_at=_text(payload.get("created_at"), maximum=100),
        updated_at=_text(payload.get("updated_at"), maximum=100),
        author_association=_text(payload.get("author_association"), maximum=50),
    )
    comment.validate()
    return comment


class GitHubIssueReconciler:
    """Incrementally refresh one fixed public GitHub repository."""

    base_url = "https://api.github.com"

    def __init__(
        self,
        state_dir,
        repository: str,
        *,
        transport: IssuesTransport | None = None,
        now: Callable[[], datetime] = _utc_now,
        minimum_interval_seconds: int = 30,
    ) -> None:
        parts = repository.strip().split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("GitHub repository must be owner/name")
        self.repository = repository.strip()
        self.owner, self.repo = parts
        self.store = IssueMirrorStore(state_dir)
        self.transport = transport or _stdlib_transport
        self.now = now
        self.minimum_interval_seconds = minimum_interval_seconds

    @staticmethod
    def _headers(*, etag: str = "") -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ZaoFu-Issue-Triage",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if etag:
            headers["If-None-Match"] = etag
        return headers

    def refresh(self, *, force: bool = False) -> dict[str, Any]:
        try:
            with locked_path(self.store.refresh_lock_path, timeout_seconds=0.1):
                return self._refresh_locked(force=force)
        except TimeoutError:
            return {"ok": True, "status": "syncing", "changed": 0}

    def refresh_issue(self, issue_number: int) -> dict[str, Any]:
        """Fetch one configured-repository Issue for an explicit operator action."""
        if issue_number < 1:
            return {
                "ok": False,
                "status": "invalid_issue",
                "changed": 0,
                "error": "GitHub Issue number must be positive",
            }
        try:
            with locked_path(self.store.refresh_lock_path, timeout_seconds=0.1):
                return self._refresh_issue_locked(issue_number)
        except TimeoutError:
            return {"ok": False, "status": "syncing", "changed": 0, "error": "Issue sync is already running"}

    def _refresh_issue_locked(self, issue_number: int) -> dict[str, Any]:
        now = self.now()
        previous = self.store.sync_state()
        attempt_at = _iso(now)
        self.store.save_sync_state(replace(
            previous,
            status="syncing",
            repository=self.repository,
            last_attempt_at=attempt_at,
            error="",
        ))
        try:
            repository_id, star_count = self._verify_repository()
            if previous.repository_id and previous.repository_id != repository_id:
                raise ValueError("GitHub repository identity changed")
            url = (
                f"{self.base_url}/repos/{urllib.parse.quote(self.owner)}/"
                f"{urllib.parse.quote(self.repo)}/issues/{issue_number}"
            )
            status, raw, response_headers = self.transport("GET", url, self._headers())
            normalized_headers = {
                str(key).lower(): str(value) for key, value in response_headers.items()
            }
            if status == 404:
                raise ValueError(f"GitHub Issue #{issue_number} was not found")
            if status == 403 and _header_int(normalized_headers, "x-ratelimit-remaining") == 0:
                raise ValueError("GitHub API rate limit reached")
            if status != 200:
                raise ValueError(f"GitHub Issue lookup failed: HTTP {status}")
            payload = _object(raw, "invalid GitHub Issue response")
            if "pull_request" in payload:
                raise ValueError("GitHub pull requests cannot enter Issue Triage")
            mirror, body = normalize_github_issue(
                payload,
                repository=self.repository,
                repository_id=repository_id,
                seen_at=attempt_at,
            )
            if mirror.number != issue_number:
                raise ValueError("GitHub Issue identity mismatch")
            saved, issue_changed = self.store.upsert(mirror, body)
            comments = self._comments(issue_number) if saved.comment_count else []
            _, comments_changed = self.store.write_comments(saved, comments)
            changed = int(issue_changed) + int(comments_changed)
            self.store.save_sync_state(SyncState(
                status="fresh",
                repository=self.repository,
                repository_id=repository_id,
                last_attempt_at=attempt_at,
                last_success_at=attempt_at,
                etag=previous.etag,
                rate_limit_remaining=_header_int(normalized_headers, "x-ratelimit-remaining"),
                rate_limit_reset_at=_rate_limit_reset(normalized_headers),
                star_count=star_count,
            ))
            return {
                "ok": True,
                "status": "fresh",
                "changed": changed,
                "issue_number": issue_number,
            }
        except (ConnectionError, OSError, TimeoutError, ValueError) as exc:
            message = str(exc)[:300] or type(exc).__name__
            status = "rate_limited" if "rate limit" in message.casefold() else "failed"
            self.store.save_sync_state(replace(
                previous,
                status=status,
                repository=self.repository,
                last_attempt_at=attempt_at,
                error=message,
            ))
            return {"ok": False, "status": status, "changed": 0, "error": message}

    def _refresh_locked(self, *, force: bool) -> dict[str, Any]:
        now = self.now()
        previous = self.store.sync_state()
        if not force and previous.last_attempt_at:
            last_attempt = datetime.fromisoformat(
                previous.last_attempt_at.replace("Z", "+00:00")
            )
            if now - last_attempt < timedelta(seconds=self.minimum_interval_seconds):
                return {"ok": True, "status": "fresh", "changed": 0, "debounced": True}
        attempt_at = _iso(now)
        self.store.save_sync_state(replace(
            previous,
            status="syncing",
            repository=self.repository,
            last_attempt_at=attempt_at,
            error="",
        ))
        try:
            repository_id, star_count = self._verify_repository()
            if previous.repository_id and previous.repository_id != repository_id:
                raise ValueError("GitHub repository identity changed")
            changed, etag, response_headers = self._sync_issues(
                repository_id,
                previous=previous,
                seen_at=attempt_at,
                force=force,
            )
            state = SyncState(
                status="fresh",
                repository=self.repository,
                repository_id=repository_id,
                last_attempt_at=attempt_at,
                last_success_at=attempt_at,
                etag=etag or previous.etag,
                rate_limit_remaining=_header_int(response_headers, "x-ratelimit-remaining"),
                rate_limit_reset_at=_rate_limit_reset(response_headers),
                star_count=star_count,
            )
            self.store.save_sync_state(state)
            return {"ok": True, "status": "fresh", "changed": changed}
        except (ConnectionError, OSError, TimeoutError, ValueError) as exc:
            message = str(exc)[:300] or type(exc).__name__
            status = "rate_limited" if "rate limit" in message.casefold() else "failed"
            self.store.save_sync_state(replace(
                previous,
                status=status,
                repository=self.repository,
                last_attempt_at=attempt_at,
                error=message,
            ))
            return {"ok": False, "status": status, "changed": 0, "error": message}

    def _verify_repository(self) -> tuple[str, int]:
        url = (
            f"{self.base_url}/repos/{urllib.parse.quote(self.owner)}/"
            f"{urllib.parse.quote(self.repo)}"
        )
        status, raw, _ = self.transport("GET", url, self._headers())
        if status == 403:
            raise ValueError("GitHub API rate limit reached")
        if status != 200:
            raise ValueError(f"GitHub repository lookup failed: HTTP {status}")
        value = _object(raw, "invalid GitHub repository response")
        full_name = str(value.get("full_name") or "")
        repository_id = str(value.get("id") or "")
        if full_name.casefold() != self.repository.casefold() or not repository_id:
            raise ValueError("GitHub repository identity mismatch")
        return repository_id, max(0, int(value.get("stargazers_count") or 0))

    def _sync_issues(
        self,
        repository_id: str,
        *,
        previous: SyncState,
        seen_at: str,
        force: bool = False,
    ) -> tuple[int, str, ResponseHeaders]:
        changed = 0
        page = 1
        first_etag = ""
        last_headers: ResponseHeaders = {}
        since = ""
        needs_metadata_backfill = any(
            not item.author_avatar_url
            or set(item.label_colors) != set(item.labels)
            or (item.assignees and set(item.assignee_avatar_urls) != set(item.assignees))
            or (item.comment_count > 0 and not item.comments_ref)
            for item in self.store.list()
        )
        if previous.last_success_at and not needs_metadata_backfill and not force:
            last_success = datetime.fromisoformat(
                previous.last_success_at.replace("Z", "+00:00")
            ) - timedelta(seconds=2)
            since = _iso(last_success)
        while page <= 100:
            params = {
                "state": "all",
                "per_page": "100",
                "sort": "updated",
                "direction": "asc",
                "page": str(page),
            }
            if since:
                params["since"] = since
            query = urllib.parse.urlencode(params)
            url = (
                f"{self.base_url}/repos/{urllib.parse.quote(self.owner)}/"
                f"{urllib.parse.quote(self.repo)}/issues?{query}"
            )
            use_etag = previous.etag if page == 1 and not since else ""
            status, raw, response_headers = self.transport(
                "GET", url, self._headers(etag=use_etag),
            )
            last_headers = {str(k).lower(): str(v) for k, v in response_headers.items()}
            if status == 304:
                return changed, previous.etag, last_headers
            if status == 403 and _header_int(last_headers, "x-ratelimit-remaining") == 0:
                raise ValueError("GitHub API rate limit reached")
            if status != 200:
                raise ValueError(f"GitHub Issues sync failed: HTTP {status}")
            if page == 1:
                first_etag = str(last_headers.get("etag") or "")
            values = _array(raw, "invalid GitHub Issues response")
            for payload in values:
                if "pull_request" in payload:
                    continue
                mirror, body = normalize_github_issue(
                    payload,
                    repository=self.repository,
                    repository_id=repository_id,
                    seen_at=seen_at,
                )
                saved, did_change = self.store.upsert(mirror, body)
                changed += int(did_change)
                comments = self._comments(mirror.number) if mirror.comment_count else []
                _, comments_changed = self.store.write_comments(saved, comments)
                changed += int(comments_changed)
            if len(values) < 100:
                break
            page += 1
        if page > 100:
            raise ValueError("GitHub Issues pagination exceeds safe limit")
        return changed, first_etag, last_headers

    def _comments(self, issue_number: int) -> list[IssueComment]:
        comments: list[IssueComment] = []
        page = 1
        while page <= 100:
            query = urllib.parse.urlencode({"per_page": "100", "page": str(page)})
            url = (
                f"{self.base_url}/repos/{urllib.parse.quote(self.owner)}/"
                f"{urllib.parse.quote(self.repo)}/issues/{issue_number}/comments?{query}"
            )
            status, raw, headers = self.transport("GET", url, self._headers())
            normalized_headers = {str(key).lower(): str(value) for key, value in headers.items()}
            if status == 403 and _header_int(normalized_headers, "x-ratelimit-remaining") == 0:
                raise ValueError("GitHub API rate limit reached")
            if status != 200:
                raise ValueError(f"GitHub Issue comments sync failed: HTTP {status}")
            values = _array(raw, "invalid GitHub Issue comments response")
            comments.extend(normalize_github_comment(value) for value in values)
            if len(values) < 100:
                break
            page += 1
        if page > 100:
            raise ValueError("GitHub Issue comments pagination exceeds safe limit")
        comments.sort(key=lambda item: (item.created_at, item.id))
        return comments


def _header_int(headers: ResponseHeaders, name: str) -> int | None:
    raw = next((value for key, value in headers.items() if key.lower() == name), None)
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _rate_limit_reset(headers: ResponseHeaders) -> str:
    value = _header_int(headers, "x-ratelimit-reset")
    if value is None:
        return ""
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
