"""GitHub.com Issue provider using documented REST endpoints only."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from zf.integrations.forge.base import (
    AttachmentUploadRequest,
    ForgeCapabilities,
    ForgeResult,
    IssuePublishRequest,
    PublishedIssue,
)
from zf.core.issue_triage.lifecycle_states import (
    REMOTE_LIFECYCLE_STATE_SET,
    validate_lifecycle_state,
)
from zf.integrations.forge.github_cli_attachments import GitHubCLICommentPublisher

Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


def _label_colors(raw: bytes) -> dict[str, str]:
    """Keep confirmed label colors from GitHub's label response."""
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    rows = value if isinstance(value, list) else [value]
    return {
        str(row["name"]): str(row["color"]).upper()
        for row in rows if isinstance(row, dict) and row.get("name")
        and re.fullmatch(r"[0-9a-fA-F]{6}", str(row.get("color") or ""))
    }

_SELF_ISSUE_LABEL_COLORS = {
    "bug": "D73A4A",
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

    def __init__(
        self,
        transport: Transport | None = None,
        *,
        cli_comment_publisher: GitHubCLICommentPublisher | None = None,
    ) -> None:
        self.transport = transport or _stdlib_transport
        self.cli_comment_publisher = cli_comment_publisher or GitHubCLICommentPublisher()

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
        issue = PublishedIssue(
            provider=self.name,
            project=request.project,
            number=number,
            url=issue_url,
        )
        if label_reason:
            # The Issue exists, but the requested metadata labels do not.  Do
            # not claim a complete publication: callers can retain this
            # remote reference and retry only the label reconciliation without
            # creating a duplicate Issue.
            return ForgeResult(
                status="partially_published", issue=issue, reason=label_reason,
            )
        return ForgeResult(status="published", issue=issue)

    def reconcile_issue_labels(
        self,
        project: str,
        issue_number: int,
        labels: tuple[str, ...],
        *,
        access_token: str,
    ) -> ForgeResult:
        """Retry metadata labels for an already-created Issue.

        This endpoint is intentionally separate from ``publish`` so recovery
        cannot create a second Issue after a label write failed.
        """

        if issue_number < 1:
            return ForgeResult(
                status="label_reconcile_failed", reason="invalid_issue_number",
            )
        owner, repo = _project_parts(project)
        reason = self._apply_labels(
            owner, repo, str(issue_number), labels, access_token=access_token,
        )
        if reason:
            return ForgeResult(status="label_reconcile_pending", reason=reason)
        return ForgeResult(status="reconciled", reason="updated")

    def check_issue_publication_access(
        self, project: str, *, access_token: str,
    ) -> ForgeResult:
        """Read target repository capabilities before any publication write."""

        owner, repo = _project_parts(project)
        url = (
            f"{self.base_url}/repos/{urllib.parse.quote(owner)}/"
            f"{urllib.parse.quote(repo)}"
        )
        try:
            status, raw = self.transport(
                "GET", url, self._headers(access_token), None,
            )
        except (TimeoutError, ConnectionError, OSError) as exc:
            return ForgeResult(status="outcome_unknown", reason=type(exc).__name__)
        if status >= 500:
            return ForgeResult(status="outcome_unknown", reason=f"github_http_{status}")
        if status in {401, 403}:
            return ForgeResult(
                status="authorization_required", reason="credential_rejected",
            )
        if status == 404:
            return ForgeResult(
                status="publish_failed", reason="github_repository_not_found",
            )
        if status != 200:
            return ForgeResult(status="publish_failed", reason=f"github_http_{status}")
        try:
            repository = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ForgeResult(
                status="outcome_unknown", reason="github_response_invalid",
            )
        if not isinstance(repository, dict):
            return ForgeResult(
                status="outcome_unknown", reason="github_response_invalid",
            )
        if bool(repository.get("archived")):
            return ForgeResult(
                status="publish_failed", reason="github_repository_archived",
            )
        if repository.get("has_issues") is not True:
            return ForgeResult(
                status="publish_failed", reason="github_issues_disabled",
            )
        return ForgeResult(status="ready")

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

    def publish_issue_comment_with_attachments(
        self,
        project: str,
        issue_number: int,
        body: str,
        *,
        marker: str,
        attachments: tuple[Path, ...] = (),
        access_token: str,
    ) -> dict[str, str]:
        """Publish an evidence comment through ``gh`` with marker idempotency."""

        owner, repo = _project_parts(project)
        if issue_number < 1 or not marker or marker not in body:
            raise ValueError("GitHub Issue comment identity is invalid")
        comments_url = (
            f"{self.base_url}/repos/{urllib.parse.quote(owner)}/"
            f"{urllib.parse.quote(repo)}/issues/{issue_number}/comments?per_page=100"
        )
        try:
            status, raw = self.transport(
                "GET", comments_url, self._headers(access_token), None,
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
        result = self.cli_comment_publisher.publish(
            project=project,
            issue_number=issue_number,
            body=body,
            attachments=attachments,
            access_token=access_token,
        )
        if result.reason.startswith((
            "github_cli_attachment_unsupported:",
            "github_cli_attachment_auth_unsupported:",
        )):
            fallback_marker = marker.replace(
                "zf-self-issue-evidence:",
                "zf-self-issue-evidence-fallback:",
                1,
            )
            fallback_body = body.replace(marker, fallback_marker, 1)
            if result.reason.startswith("github_cli_attachment_unsupported:"):
                recovery = (
                    "Upgrade to GitHub CLI 2.99.0 or newer, then retry the "
                    "evidence comment in ZaoFu."
                )
            else:
                recovery = (
                    "The current GitHub App token can publish comments but GitHub's "
                    "media endpoint requires an OAuth or personal access token."
                )
            fallback_body += f"\n\n> **Evidence media is pending.** {recovery}"
            fallback = self.publish_issue_comment(
                project,
                issue_number,
                fallback_body,
                marker=fallback_marker,
                access_token=access_token,
            )
            if fallback.get("status") == "published":
                return {
                    "status": "publish_failed",
                    "url": str(fallback.get("url") or ""),
                    "id": str(fallback.get("id") or ""),
                    "replayed": str(fallback.get("replayed") or "false"),
                    "reason": f"{result.reason}:summary_comment_published",
                }
        comment_id = ""
        if result.url and "#issuecomment-" in result.url:
            comment_id = result.url.rsplit("#issuecomment-", 1)[1]
        return {
            "status": result.status,
            "url": result.url,
            "id": comment_id,
            "replayed": "false",
            "reason": result.reason,
        }

    def publish_release_assets(
        self,
        project: str,
        triage_id: str,
        attachments: tuple[tuple[str, str, str, bytes], ...],
        *,
        access_token: str,
    ) -> dict[str, object]:
        """Store every original artifact in an immutable per-triage Release.

        GitHub Issue comments only support native media uploads. Release assets
        provide a documented download URL for PDFs, Office files, logs, and
        other binaries while preserving the original bytes and digest.
        """
        owner, repo = _project_parts(project)
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", triage_id).strip("-")[:80] or "triage"
        tag = f"zaofu-triage-{safe_id}-{hashlib.sha256(triage_id.encode()).hexdigest()[:10]}"
        base = f"{self.base_url}/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}"
        headers = self._headers(access_token)
        status, raw = self.transport("GET", f"{base}/releases/tags/{urllib.parse.quote(tag)}", headers, None)
        release: dict = {}
        if status == 200:
            try: release = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError): release = {}
        elif status == 404:
            status, raw = self.transport("POST", f"{base}/releases", headers, json.dumps({
                "tag_name": tag, "name": f"ZaoFu Triage {triage_id}",
                "body": "Immutable ZaoFu evidence assets for one Triage run.",
                "draft": False, "prerelease": False,
            }, separators=(",", ":")).encode("utf-8"))
            if status not in {201, 200}:
                return {"status": "publish_failed", "reason": f"github_release_http_{status}"}
            try: release = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError): release = {}
        if not isinstance(release, dict) or not str(release.get("upload_url") or ""):
            return {"status": "outcome_unknown", "reason": "github_release_response_invalid"}
        upload_url = str(release["upload_url"]).split("{", 1)[0]
        existing = {str(item.get("name") or ""): item for item in release.get("assets", []) if isinstance(item, dict)}
        published: list[dict[str, str]] = []
        for filename, content_type, digest, content in attachments:
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", Path(filename).name).strip("-") or "artifact"
            asset_name = f"{digest.replace('sha256:', '')[:12]}-{safe_name}"
            current = existing.get(asset_name)
            if current and str(current.get("browser_download_url") or ""):
                url = str(current["browser_download_url"])
            else:
                url = upload_url + "?" + urllib.parse.urlencode({"name": asset_name})
                asset_headers = {**headers, "Content-Type": content_type or "application/octet-stream"}
                upload_status, upload_raw = self.transport("POST", url, asset_headers, content)
                if upload_status not in {201, 200}:
                    return {"status": "publish_failed", "reason": f"github_release_asset_http_{upload_status}", "published": published}
                try: uploaded = json.loads(upload_raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError): uploaded = {}
                url = str(uploaded.get("browser_download_url") or "")
                if not url:
                    return {"status": "outcome_unknown", "reason": "github_release_asset_response_invalid", "published": published}
            published.append({"filename": filename, "sha256": digest, "url": url, "asset_name": asset_name})
        manifest = json.dumps({
            "schema_version": "zaofu-triage-evidence-manifest.v1",
            "triage_id": triage_id,
            "assets": [{"filename": row["filename"], "sha256": row["sha256"], "url": row["url"]} for row in published],
        }, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        manifest_digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
        manifest_name = f"{manifest_digest[7:19]}-manifest.json"
        existing_manifest = existing.get(manifest_name)
        if existing_manifest and str(existing_manifest.get("browser_download_url") or ""):
            manifest_download_url = str(existing_manifest["browser_download_url"])
        else:
            manifest_url = upload_url + "?" + urllib.parse.urlencode({"name": manifest_name})
            manifest_status, manifest_raw = self.transport(
                "POST", manifest_url, {**headers, "Content-Type": "application/json"}, manifest,
            )
            if manifest_status not in {200, 201}:
                return {"status": "publish_failed", "reason": f"github_release_manifest_http_{manifest_status}", "published": published}
            try: manifest_value = json.loads(manifest_raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError): manifest_value = {}
            manifest_download_url = str(manifest_value.get("browser_download_url") or "")
        if not manifest_download_url:
            return {"status": "outcome_unknown", "reason": "github_release_manifest_response_invalid", "published": published}
        return {"status": "published", "release_url": str(release.get("html_url") or ""), "tag": tag,
                "manifest_url": manifest_download_url, "assets": published}

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

    def reconcile_lifecycle_label(
        self,
        project: str,
        issue_number: int,
        state: str,
        *,
        access_token: str,
    ) -> ForgeResult:
        """Reconcile one canonical lifecycle label without deleting metadata.

        GitHub's issue-label endpoint is append-oriented, so we first read the
        current labels, add the desired canonical label, and then delete only
        stale labels from the 32-state registry.  Labels outside that registry
        are never included in the delete set.
        """

        try:
            desired = validate_lifecycle_state(state)
        except ValueError as exc:
            return ForgeResult(status="label_reconcile_failed", reason=str(exc))
        if issue_number < 1:
            return ForgeResult(status="label_reconcile_failed", reason="invalid_issue_number")
        owner, repo = _project_parts(project)
        base = (
            f"{self.base_url}/repos/{urllib.parse.quote(owner)}/"
            f"{urllib.parse.quote(repo)}/issues/{issue_number}/labels"
        )
        headers = self._headers(access_token)
        try:
            status, raw = self.transport(
                "GET", f"{base}?per_page=100", headers, None,
            )
        except (TimeoutError, ConnectionError, OSError) as exc:
            return ForgeResult(status="outcome_unknown", reason=type(exc).__name__)
        if status in {401, 403}:
            return ForgeResult(status="authorization_required", reason="credential_rejected")
        if status != 200:
            return ForgeResult(status="outcome_unknown", reason=f"github_labels_read_http_{status}")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ForgeResult(status="outcome_unknown", reason="github_labels_response_invalid")
        if not isinstance(value, list):
            return ForgeResult(status="outcome_unknown", reason="github_labels_response_invalid")
        current = tuple(
            str(item.get("name") or "").strip()
            for item in value
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        )
        stale = [
            label for label in current
            if label in REMOTE_LIFECYCLE_STATE_SET and label != desired
        ]
        colors = _label_colors(raw)
        if not stale and desired in current:
            return ForgeResult(status="reconciled", reason="unchanged", label_colors=colors)

        # Ensure the desired label exists at repository level.  A 422 means
        # GitHub reports that the same-named label already exists.
        label_url = (
            f"{self.base_url}/repos/{urllib.parse.quote(owner)}/"
            f"{urllib.parse.quote(repo)}/labels"
        )
        if desired not in current:
            try:
                create_status, created_raw = self.transport(
                    "POST", label_url, headers,
                    json.dumps({"name": desired, "color": "6E7781"}, separators=(",", ":")).encode(),
                )
            except (TimeoutError, ConnectionError, OSError) as exc:
                return ForgeResult(status="outcome_unknown", reason=type(exc).__name__)
            if create_status not in {201, 422}:
                return ForgeResult(status="label_reconcile_failed", reason=f"github_label_create_http_{create_status}")
            if create_status == 201:
                colors[desired] = "6E7781"
                colors.update(_label_colors(created_raw))
            try:
                add_status, added_raw = self.transport(
                    "POST", base, headers,
                    json.dumps({"labels": [desired]}, separators=(",", ":")).encode(),
                )
            except (TimeoutError, ConnectionError, OSError) as exc:
                return ForgeResult(status="outcome_unknown", reason=type(exc).__name__)
            if add_status != 200:
                return ForgeResult(status="label_reconcile_failed", reason=f"github_issue_label_add_http_{add_status}")
            colors.update(_label_colors(added_raw))

        for stale_label in stale:
            delete_url = f"{base}/{urllib.parse.quote(stale_label, safe='')}"
            try:
                delete_status, _ = self.transport("DELETE", delete_url, headers, None)
            except (TimeoutError, ConnectionError, OSError) as exc:
                return ForgeResult(status="outcome_unknown", reason=type(exc).__name__)
            if delete_status not in {200, 204, 404}:
                return ForgeResult(
                    status="label_reconcile_pending",
                    reason=f"github_issue_label_delete_http_{delete_status}",
                )
        return ForgeResult(
            status="reconciled",
            reason="updated",
            evidence_refs=(f"github:{project}#{issue_number}:{desired}",),
            label_colors={name: color for name, color in colors.items() if name not in stale},
        )

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

    def start(self, *, client_id: str, scope: str = "") -> dict[str, str]:
        form = {"client_id": client_id}
        if scope:
            form["scope"] = scope
        status, raw = self.transport(
            "POST", f"{self.base_url}/login/device/code",
            {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            urllib.parse.urlencode(form).encode("utf-8"),
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
            "scope": str(value.get("scope") or ""),
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
