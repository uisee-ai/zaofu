"""GitHub.com Issue provider using documented REST endpoints only."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable

from zf.integrations.forge.base import (
    AttachmentUploadRequest,
    ForgeCapabilities,
    ForgeResult,
    IssuePublishRequest,
    PublishedIssue,
)

Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]

_SELF_ISSUE_LABEL_COLORS = {
    "runtime": "5319E7",
    "kernel/state": "1D76DB",
    "provider/integration": "0052CC",
    "web/ui": "0E8A16",
    "configuration": "FBCA04",
    "security": "B60205",
    "performance": "D93F0B",
    "test/regression": "C5DEF5",
    "unknown": "E4E669",
    "p0": "B60205",
    "p1": "D93F0B",
    "p2": "FBCA04",
    "p3": "0E8A16",
}


def _stdlib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None,
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()


class GitHubComProvider:
    name = "github"
    base_url = "https://api.github.com"
    capabilities = ForgeCapabilities(binary_attachment_upload=False)

    def __init__(self, transport: Transport | None = None) -> None:
        self.transport = transport or _stdlib_transport

    @staticmethod
    def _headers(access_token: str) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def publish(self, request: IssuePublishRequest, *, access_token: str) -> ForgeResult:
        owner, repo = _project_parts(request.project)
        body = json.dumps({
            "title": request.title,
            "body": request.body,
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        url = f"{self.base_url}/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/issues"
        try:
            status, raw = self.transport("POST", url, self._headers(access_token), body)
        except (TimeoutError, ConnectionError, OSError) as exc:
            return ForgeResult(status="outcome_unknown", reason=type(exc).__name__)
        if status >= 500:
            return ForgeResult(status="outcome_unknown", reason=f"github_http_{status}")
        if status == 401:
            return ForgeResult(status="authorization_required", reason="credential_rejected")
        if status != 201:
            return ForgeResult(status="publish_failed", reason=f"github_http_{status}")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ForgeResult(status="outcome_unknown", reason="github_response_invalid")
        number = str(value.get("number") or "")
        issue_url = str(value.get("html_url") or "")
        expected_prefix = f"https://github.com/{owner}/{repo}/issues/"
        if not number or not issue_url.startswith(expected_prefix):
            return ForgeResult(status="outcome_unknown", reason="github_response_invalid")
        label_reason = self._apply_labels(
            owner, repo, number, request.labels, access_token=access_token,
        )
        return ForgeResult(status="published", issue=PublishedIssue(
            provider=self.name,
            project=request.project,
            number=number,
            url=issue_url,
        ), reason=label_reason)

    def publish_issue_comment(
        self,
        project: str,
        issue_number: int,
        body: str,
        *,
        marker: str,
        access_token: str,
    ) -> dict[str, str]:
        """Publish one idempotent Issue comment through an exact marker."""

        owner, repo = _project_parts(project)
        if issue_number < 1 or not marker or marker not in body:
            raise ValueError("GitHub Issue comment identity is invalid")
        url = (
            f"{self.base_url}/repos/{urllib.parse.quote(owner)}/"
            f"{urllib.parse.quote(repo)}/issues/{issue_number}/comments"
        )
        try:
            status, raw = self.transport(
                "GET", f"{url}?per_page=100", self._headers(access_token), None,
            )
        except (TimeoutError, ConnectionError, OSError):
            status, raw = 0, b""
        if status == 200:
            try:
                comments = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                comments = []
            for comment in comments if isinstance(comments, list) else []:
                if isinstance(comment, dict) and marker in str(comment.get("body") or ""):
                    return {
                        "status": "published",
                        "url": str(comment.get("html_url") or ""),
                        "id": str(comment.get("id") or ""),
                        "replayed": "true",
                    }
        encoded = json.dumps(
            {"body": body}, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        try:
            status, raw = self.transport(
                "POST", url, self._headers(access_token), encoded,
            )
        except (TimeoutError, ConnectionError, OSError) as exc:
            return {"status": "outcome_unknown", "reason": type(exc).__name__}
        if status >= 500:
            return {"status": "outcome_unknown", "reason": f"github_http_{status}"}
        if status == 401:
            return {"status": "authorization_required", "reason": "credential_rejected"}
        if status != 201:
            return {"status": "publish_failed", "reason": f"github_http_{status}"}
        try:
            comment = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"status": "outcome_unknown", "reason": "github_response_invalid"}
        if not isinstance(comment, dict) or not str(comment.get("id") or ""):
            return {"status": "outcome_unknown", "reason": "github_response_invalid"}
        return {
            "status": "published",
            "url": str(comment.get("html_url") or ""),
            "id": str(comment.get("id") or ""),
            "replayed": "false",
        }

    def _apply_labels(
        self,
        owner: str,
        repo: str,
        number: str,
        labels: tuple[str, ...],
        *,
        access_token: str,
    ) -> str:
        requested = tuple(dict.fromkeys(str(label).strip() for label in labels if str(label).strip()))
        if not requested:
            return ""
        headers = self._headers(access_token)
        label_url = (
            f"{self.base_url}/repos/{urllib.parse.quote(owner)}/"
            f"{urllib.parse.quote(repo)}/labels"
        )
        # The repository may not have the allowlisted labels yet. Creating them
        # is idempotent: GitHub returns 422 when a same-named label already exists.
        for label in requested:
            color = _SELF_ISSUE_LABEL_COLORS.get(label.casefold(), "6E7781")
            try:
                status, _ = self.transport(
                    "POST", label_url, headers,
                    json.dumps({"name": label, "color": color}, separators=(",", ":")).encode(),
                )
            except (TimeoutError, ConnectionError, OSError) as exc:
                return f"github_labels_not_applied:{type(exc).__name__}"
            if status not in {201, 422}:
                return f"github_labels_not_applied_http_{status}"
        issue_labels_url = (
            f"{self.base_url}/repos/{urllib.parse.quote(owner)}/"
            f"{urllib.parse.quote(repo)}/issues/{urllib.parse.quote(number)}/labels"
        )
        try:
            status, _ = self.transport(
                "POST", issue_labels_url, headers,
                json.dumps({"labels": list(requested)}, separators=(",", ":")).encode(),
            )
        except (TimeoutError, ConnectionError, OSError) as exc:
            return f"github_labels_not_applied:{type(exc).__name__}"
        return "" if status == 200 else f"github_labels_not_applied_http_{status}"

    def upload_attachment(
        self, request: AttachmentUploadRequest, *, access_token: str,
    ) -> ForgeResult:
        del request, access_token
        return ForgeResult(
            status="publish_failed", reason="github_attachment_upload_unsupported",
        )

    def find_by_marker(
        self, project: str, marker: str, *, access_token: str,
    ) -> list[PublishedIssue]:
        owner, repo = _project_parts(project)
        query = urllib.parse.urlencode({
            "state": "all", "per_page": "100", "sort": "created", "direction": "desc",
        })
        url = (
            f"{self.base_url}/repos/{urllib.parse.quote(owner)}/"
            f"{urllib.parse.quote(repo)}/issues?{query}"
        )
        try:
            status, raw = self.transport("GET", url, self._headers(access_token), None)
        except (TimeoutError, ConnectionError, OSError):
            return []
        if status != 200:
            return []
        try:
            values = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return []
        exact_marker = f"<!-- zf-self-issue:{marker} -->"
        expected_prefix = f"https://github.com/{owner}/{repo}/issues/"
        return [PublishedIssue(
            provider=self.name,
            project=project,
            number=str(item.get("number") or ""),
            url=str(item.get("html_url") or ""),
        ) for item in values if (
            isinstance(item, dict)
            and "pull_request" not in item
            and exact_marker in str(item.get("body") or "")
            and str(item.get("number") or "")
            and str(item.get("html_url") or "").startswith(expected_prefix)
        )]


class GitHubDeviceOAuthClient:
    """GitHub App Device Flow client; no client secret is distributed."""

    base_url = "https://github.com"

    def __init__(self, transport: Transport | None = None) -> None:
        self.transport = transport or _stdlib_transport

    def start(self, *, client_id: str) -> dict[str, str]:
        status, raw = self.transport(
            "POST", f"{self.base_url}/login/device/code",
            {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            urllib.parse.urlencode({"client_id": client_id}).encode("utf-8"),
        )
        if status != 200:
            raise ValueError(f"github_device_start_failed:{status}")
        value = _json_object(raw, "github_device_response_invalid")
        result = {
            "device_code": str(value.get("device_code") or ""),
            "user_code": str(value.get("user_code") or ""),
            "verification_uri": str(value.get("verification_uri") or ""),
            "expires_in": str(value.get("expires_in") or ""),
            "interval": str(value.get("interval") or "5"),
        }
        if (
            not result["device_code"] or not result["user_code"]
            or result["verification_uri"] != "https://github.com/login/device"
        ):
            raise ValueError("github_device_response_invalid")
        return result

    def poll(self, *, client_id: str, device_code: str) -> dict[str, str]:
        status, raw = self.transport(
            "POST", f"{self.base_url}/login/oauth/access_token",
            {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            urllib.parse.urlencode({
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }).encode("utf-8"),
        )
        if status != 200:
            raise ValueError(f"github_device_poll_failed:{status}")
        value = _json_object(raw, "github_device_response_invalid")
        if value.get("error"):
            return {
                "status": str(value.get("error") or "github_device_error"),
                "interval": str(value.get("interval") or ""),
            }
        return self._token_response(value)

    def refresh(self, *, client_id: str, refresh_token: str) -> dict[str, str]:
        status, raw = self.transport(
            "POST", f"{self.base_url}/login/oauth/access_token",
            {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            urllib.parse.urlencode({
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }).encode("utf-8"),
        )
        if status != 200:
            raise ValueError(f"github_oauth_refresh_failed:{status}")
        value = _json_object(raw, "github_oauth_refresh_invalid")
        if value.get("error"):
            raise ValueError(str(value.get("error")))
        return self._token_response(value)

    @staticmethod
    def _token_response(value: dict[str, object]) -> dict[str, str]:
        token = str(value.get("access_token") or "")
        if not token:
            raise ValueError("github_oauth_missing_access_token")
        result = {
            "status": "connected",
            "access_token": token,
            "refresh_token": str(value.get("refresh_token") or ""),
            "scope": "issues:write",
            "token_type": str(value.get("token_type") or "bearer"),
            "token_source": "device_flow",
        }
        now = datetime.now(timezone.utc).replace(microsecond=0)
        expires_in = int(value.get("expires_in") or 0)
        refresh_expires_in = int(value.get("refresh_token_expires_in") or 0)
        if expires_in > 0:
            result["expires_at"] = (now + timedelta(seconds=expires_in)).isoformat()
        if refresh_expires_in > 0:
            result["refresh_expires_at"] = (
                now + timedelta(seconds=refresh_expires_in)
            ).isoformat()
        return result


def _project_parts(project: str) -> tuple[str, str]:
    parts = str(project or "").strip().split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError("GitHub project must be owner/repository")
    return parts[0], parts[1]


def _json_object(raw: bytes, error: str) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(error) from exc
    if not isinstance(value, dict):
        raise ValueError(error)
    return value
