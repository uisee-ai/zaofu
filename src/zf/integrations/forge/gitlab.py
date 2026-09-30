"""GitLab.com Forge provider with an injectable HTTP transport."""

from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from zf.integrations.forge.base import (
    AttachmentUploadRequest,
    ForgeCapabilities,
    ForgeResult,
    IssuePublishRequest,
    PublishedIssue,
    UploadedAttachment,
)

Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


def _stdlib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None,
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()


class GitLabComProvider:
    name = "gitlab"
    base_url = "https://gitlab.com"
    capabilities = ForgeCapabilities(binary_attachment_upload=True)

    def __init__(self, transport: Transport | None = None) -> None:
        self.transport = transport or _stdlib_transport

    def publish(self, request: IssuePublishRequest, *, access_token: str) -> ForgeResult:
        payload = urllib.parse.urlencode({
            "title": request.title,
            "description": request.body,
            "labels": ",".join(request.labels),
        }).encode("utf-8")
        url = f"{self.base_url}/api/v4/projects/{urllib.parse.quote(request.project, safe='')}/issues"
        try:
            status, raw = self.transport(
                "POST", url,
                {"Authorization": f"Bearer {access_token}", "Content-Type": "application/x-www-form-urlencoded"},
                payload,
            )
        except (TimeoutError, ConnectionError, OSError) as exc:
            return ForgeResult(status="outcome_unknown", reason=type(exc).__name__)
        if status >= 500:
            return ForgeResult(status="outcome_unknown", reason=f"gitlab_http_{status}")
        if status == 401:
            return ForgeResult(status="authorization_required", reason="credential_rejected")
        if status not in {200, 201}:
            return ForgeResult(status="publish_failed", reason=f"gitlab_http_{status}")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ForgeResult(status="outcome_unknown", reason="gitlab_response_invalid")
        number = str(value.get("iid") or value.get("id") or "")
        issue_url = str(value.get("web_url") or "")
        if not number or not issue_url.startswith("https://gitlab.com/"):
            return ForgeResult(status="outcome_unknown", reason="gitlab_response_invalid")
        return ForgeResult(status="published", issue=PublishedIssue(
            provider=self.name,
            project=request.project,
            number=number,
            url=issue_url,
        ))

    def upload_attachment(
        self, request: AttachmentUploadRequest, *, access_token: str,
    ) -> ForgeResult:
        boundary = f"zf-self-issue-{secrets.token_hex(12)}"
        filename = request.filename.replace('"', "-").replace("\r", "-").replace("\n", "-")
        body = b"".join((
            f"--{boundary}\r\n".encode(),
            (
                'Content-Disposition: form-data; name="file"; '
                f'filename="{filename}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {request.content_type}\r\n\r\n".encode(),
            request.content,
            f"\r\n--{boundary}--\r\n".encode(),
        ))
        url = (
            f"{self.base_url}/api/v4/projects/"
            f"{urllib.parse.quote(request.project, safe='')}/uploads"
        )
        try:
            status, raw = self.transport(
                "POST", url,
                {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                },
                body,
            )
        except (TimeoutError, ConnectionError, OSError) as exc:
            return ForgeResult(status="outcome_unknown", reason=type(exc).__name__)
        if status >= 500:
            return ForgeResult(status="outcome_unknown", reason=f"gitlab_http_{status}")
        if status == 401:
            return ForgeResult(status="authorization_required", reason="credential_rejected")
        if status not in {200, 201}:
            return ForgeResult(status="publish_failed", reason=f"gitlab_http_{status}")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ForgeResult(status="outcome_unknown", reason="gitlab_upload_response_invalid")
        markdown = str(value.get("markdown") or "")
        upload_url = str(value.get("full_path") or value.get("url") or "")
        if not markdown or not upload_url.startswith("/"):
            return ForgeResult(status="outcome_unknown", reason="gitlab_upload_response_invalid")
        return ForgeResult(
            status="published",
            attachment=UploadedAttachment(
                provider=self.name,
                project=request.project,
                filename=request.filename,
                markdown=markdown,
                url=f"{self.base_url}{upload_url}",
                upload_id=str(value.get("id") or ""),
            ),
        )

    def find_by_marker(self, project: str, marker: str, *, access_token: str) -> list[PublishedIssue]:
        query = urllib.parse.urlencode({"search": f"zf-self-issue:{marker}", "in": "description"})
        url = (
            f"{self.base_url}/api/v4/projects/{urllib.parse.quote(project, safe='')}/issues?{query}"
        )
        try:
            status, raw = self.transport(
                "GET", url, {"Authorization": f"Bearer {access_token}"}, None,
            )
        except (TimeoutError, ConnectionError, OSError):
            return []
        if status != 200:
            return []
        try:
            values = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return []
        exact_marker = f"<!-- zf-self-issue:{marker} -->"
        return [PublishedIssue(
            provider=self.name,
            project=project,
            number=str(item.get("iid") or item.get("id") or ""),
            url=str(item.get("web_url") or ""),
        ) for item in values if (
            isinstance(item, dict)
            and exact_marker in str(item.get("description") or "")
            and str(item.get("iid") or item.get("id") or "")
            and str(item.get("web_url") or "").startswith("https://gitlab.com/")
        )]

    def reconcile_issue_labels(
        self,
        project: str,
        issue_number: int,
        labels: tuple[str, ...],
        *,
        access_token: str,
    ) -> ForgeResult:
        """Retry metadata labels on an existing GitLab Issue.

        GitLab accepts ``add_labels`` on the issue update endpoint.  This is
        deliberately separate from ``publish`` so a partial publication can
        never create a second Issue.
        """

        if issue_number < 1:
            return ForgeResult(
                status="label_reconcile_failed", reason="invalid_issue_number",
            )
        payload = urllib.parse.urlencode({
            "add_labels": ",".join(
                dict.fromkeys(str(label).strip() for label in labels if str(label).strip())
            ),
        }).encode("utf-8")
        url = (
            f"{self.base_url}/api/v4/projects/{urllib.parse.quote(project, safe='')}/"
            f"issues/{issue_number}"
        )
        try:
            status, _ = self.transport(
                "PUT", url,
                {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                payload,
            )
        except (TimeoutError, ConnectionError, OSError) as exc:
            return ForgeResult(status="outcome_unknown", reason=type(exc).__name__)
        if status >= 500:
            return ForgeResult(status="outcome_unknown", reason=f"gitlab_http_{status}")
        if status == 401:
            return ForgeResult(status="authorization_required", reason="credential_rejected")
        if status != 200:
            return ForgeResult(status="label_reconcile_pending", reason=f"gitlab_http_{status}")
        return ForgeResult(status="reconciled", reason="updated")
