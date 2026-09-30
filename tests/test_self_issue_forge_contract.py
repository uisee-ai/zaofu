from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from zf.integrations.forge.base import (
    AttachmentUploadRequest,
    ForgeResult,
    IssuePublishRequest,
    PublishedIssue,
    UploadedAttachment,
)
from zf.integrations.forge.gitlab import GitLabComProvider
from zf.integrations.forge.github import GitHubComProvider, GitHubDeviceOAuthClient
from zf.integrations.forge.github_cli_attachments import GitHubCommentAttachmentResult
from zf.integrations.forge.oauth import GitLabOAuthClient, pkce_pair


def test_gitlab_provider_uses_marker_and_does_not_put_token_in_body() -> None:
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        return 201, json.dumps({"iid": 7, "web_url": "https://gitlab.com/a/b/-/issues/7"}).encode()

    provider = GitLabComProvider(transport)
    result = provider.publish(IssuePublishRequest(
        project="a/b", title="bug",
        body="safe\n\n<!-- zf-self-issue:stable -->",
        labels=("runtime",), marker="stable",
    ), access_token="top-secret")

    assert result.status == "published"
    assert b"top-secret" not in calls[0][3]
    posted = parse_qs(calls[0][3].decode())
    assert posted["description"] == ["safe\n\n<!-- zf-self-issue:stable -->"]
    assert calls[0][2]["Authorization"] == "Bearer top-secret"


def test_oauth_uses_s256_and_exact_redirect_uri() -> None:
    verifier, challenge = pkce_pair()
    assert verifier and challenge and "=" not in challenge
    client = GitLabOAuthClient(lambda *_: (500, b"{}"))
    url = client.authorization_url(
        client_id="client", redirect_uri="https://example.test/exact/callback",
        state="once", challenge=challenge,
    )
    query = parse_qs(url.split("?", 1)[1])
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["https://example.test/exact/callback"]
    assert query["scope"] == ["api"]

    exchange = GitLabOAuthClient(lambda *_: (200, json.dumps({
        "access_token": "secret", "refresh_token": "refresh", "scope": ["api"],
    }).encode()))
    token = exchange.exchange(
        code="code", verifier=verifier, client_id="client",
        redirect_uri="https://example.test/exact/callback",
    )
    assert token["scope"] == "api"


def test_oauth_refresh_rotates_public_client_token_without_client_secret() -> None:
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        return 200, json.dumps({
            "access_token": "next-access", "refresh_token": "next-refresh",
            "scope": ["api"], "expires_in": 7200, "created_at": 1_700_000_000,
        }).encode()

    token = GitLabOAuthClient(transport).refresh(
        refresh_token="current-refresh", client_id="client",
        redirect_uri="https://example.test/exact/callback",
    )

    posted = parse_qs(calls[0][3].decode())
    assert posted == {
        "client_id": ["client"],
        "refresh_token": ["current-refresh"],
        "grant_type": ["refresh_token"],
        "redirect_uri": ["https://example.test/exact/callback"],
    }
    assert "client_secret" not in posted
    assert token["access_token"] == "next-access"
    assert token["refresh_token"] == "next-refresh"
    assert token["scope"] == "api"
    assert token["expires_at"] == "2023-11-15T00:13:20+00:00"


def test_gitlab_ambiguous_server_response_is_outcome_unknown() -> None:
    provider = GitLabComProvider(lambda *_: (502, b"bad gateway"))
    result = provider.publish(IssuePublishRequest(
        project="a/b", title="bug", body="safe", labels=(), marker="stable",
    ), access_token="top-secret")
    assert result.status == "outcome_unknown"


def test_gitlab_rejected_credential_is_recoverable_authorization() -> None:
    provider = GitLabComProvider(lambda *_: (401, b'{"message":"401 Unauthorized"}'))
    result = provider.publish(IssuePublishRequest(
        project="a/b", title="bug", body="safe", labels=(), marker="stable",
    ), access_token="expired-secret")

    assert result.status == "authorization_required"
    assert result.reason == "credential_rejected"


def test_gitlab_marker_recovery_requires_exact_marker_and_safe_issue_url() -> None:
    payload = json.dumps([
        {"iid": 1, "web_url": "https://gitlab.com/a/b/-/issues/1",
         "description": "similar zf-self-issue:stable"},
        {"iid": 2, "web_url": "https://evil.test/token",
         "description": "<!-- zf-self-issue:stable -->"},
        {"iid": 3, "web_url": "https://gitlab.com/a/b/-/issues/3",
         "description": "<!-- zf-self-issue:stable -->"},
    ]).encode()
    provider = GitLabComProvider(lambda *_: (200, payload))

    matches = provider.find_by_marker("a/b", "stable", access_token="top-secret")
    assert [item.number for item in matches] == ["3"]


def test_gitlab_reconciles_labels_without_creating_another_issue() -> None:
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        return 200, b"{}"

    result = GitLabComProvider(transport).reconcile_issue_labels(
        "a/b", 7, ("runtime", "p2"), access_token="top-secret",
    )

    assert result.status == "reconciled"
    assert calls[0][0] == "PUT"
    assert calls[0][1].endswith("/api/v4/projects/a%2Fb/issues/7")
    assert parse_qs(calls[0][3].decode()) == {"add_labels": ["runtime,p2"]}


def test_gitlab_attachment_upload_uses_multipart_without_leaking_token() -> None:
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        return 201, json.dumps({
            "id": 9,
            "markdown": "[safe.log](/uploads/abc/safe.log)",
            "full_path": "/a/b/-/uploads/abc/safe.log",
        }).encode()

    result = GitLabComProvider(transport).upload_attachment(
        AttachmentUploadRequest(
            project="a/b", filename="safe.log", content_type="text/plain",
            content=b"safe evidence", digest="abc",
        ),
        access_token="top-secret",
    )

    assert result.status == "published"
    assert result.attachment is not None
    assert result.attachment.url == "https://gitlab.com/a/b/-/uploads/abc/safe.log"
    assert b"top-secret" not in calls[0][3]
    assert b"safe evidence" in calls[0][3]
    assert calls[0][2]["Authorization"] == "Bearer top-secret"


def test_fake_github_implements_the_provider_neutral_issue_and_attachment_contract() -> None:
    class FakeGithub:
        name = "github"

        def publish(self, request, *, access_token):
            return ForgeResult(status="published", issue=PublishedIssue(
                provider=self.name, project=request.project, number="1",
                url="https://example.test/issues/1",
            ))

        def upload_attachment(self, request, *, access_token):
            return ForgeResult(status="published", attachment=UploadedAttachment(
                provider=self.name, project=request.project, filename=request.filename,
                markdown="[file](https://example.test/file)",
                url="https://example.test/file",
            ))

        def find_by_marker(self, project, marker, *, access_token):
            return []

    provider = FakeGithub()
    issue = provider.publish(IssuePublishRequest(
        project="owner/repo", title="bug", body="body", labels=("runtime",), marker="m",
    ), access_token="fake")
    attachment = provider.upload_attachment(AttachmentUploadRequest(
        project="owner/repo", filename="safe.log", content_type="text/plain",
        content=b"safe", digest="abc",
    ), access_token="fake")
    assert issue.issue.provider == attachment.attachment.provider == "github"


def test_github_provider_uses_rest_issue_contract_and_exact_marker() -> None:
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        if url.endswith("/issues/9/labels"):
            return 200, b"[]"
        if url.endswith("/labels"):
            return 201, b"{}"
        return 201, json.dumps({
            "number": 9,
            "html_url": "https://github.com/owner/repo/issues/9",
        }).encode()

    provider = GitHubComProvider(transport)
    result = provider.publish(IssuePublishRequest(
        project="owner/repo",
        title="bug",
        body="safe\n\n<!-- zf-self-issue:stable -->",
        labels=("runtime",),
        marker="stable",
    ), access_token="top-secret")

    assert result.status == "published"
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/repos/owner/repo/issues")
    assert calls[0][2]["Authorization"] == "Bearer top-secret"
    assert b"top-secret" not in calls[0][3]
    assert json.loads(calls[0][3]) == {
        "title": "bug",
        "body": "safe\n\n<!-- zf-self-issue:stable -->",
    }
    assert provider.capabilities.binary_attachment_upload is False


def test_github_provider_creates_and_applies_requested_labels() -> None:
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        if url.endswith("/issues/9/labels"):
            return 200, b"[]"
        if url.endswith("/issues"):
            return 201, json.dumps({
                "number": 9,
                "html_url": "https://github.com/owner/repo/issues/9",
            }).encode()
        if url.endswith("/labels"):
            return 201, b"{}"
        raise AssertionError(url)

    result = GitHubComProvider(transport).publish(IssuePublishRequest(
        project="owner/repo", title="bug", body="safe",
        labels=("runtime", "p2"), marker="stable",
    ), access_token="top-secret")

    assert result.status == "published"
    assert [call[0] for call in calls] == ["POST", "POST", "POST", "POST"]
    assert json.loads(calls[1][3]) == {"name": "runtime", "color": "5319E7"}
    assert json.loads(calls[2][3]) == {"name": "p2", "color": "FBCA04"}
    assert json.loads(calls[3][3]) == {"labels": ["runtime", "p2"]}
    assert all(b"top-secret" not in call[3] for call in calls)


def test_github_provider_reports_partial_when_issue_labels_fail() -> None:
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        if url.endswith("/issues"):
            return 201, json.dumps({
                "number": 12,
                "html_url": "https://github.com/owner/repo/issues/12",
            }).encode()
        if url.endswith("/labels"):
            return 503, b"service unavailable"
        raise AssertionError(url)

    result = GitHubComProvider(transport).publish(IssuePublishRequest(
        project="owner/repo", title="bug", body="safe",
        labels=("runtime",), marker="stable",
    ), access_token="top-secret")

    assert result.status == "partially_published"
    assert result.issue is not None
    assert result.issue.number == "12"
    assert result.reason == "github_labels_not_applied_http_503"
    assert [call[0] for call in calls] == ["POST", "POST"]


def test_github_provider_reconciles_labels_without_creating_another_issue() -> None:
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        if url.endswith("/issues/12/labels"):
            return 200, b"[]"
        if url.endswith("/labels"):
            return 201, b"{}"
        raise AssertionError(url)

    result = GitHubComProvider(transport).reconcile_issue_labels(
        "owner/repo", 12, ("runtime", "p2"), access_token="top-secret",
    )

    assert result.status == "reconciled"
    assert [call[0] for call in calls] == ["POST", "POST", "POST"]
    assert all(not (call[1].endswith("/issues")) for call in calls)


def test_github_attachment_comment_falls_back_to_recoverable_text_comment() -> None:
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        if method == "GET":
            return 200, b"[]"
        return 201, json.dumps({
            "id": 42,
            "html_url": "https://github.com/owner/repo/issues/9#issuecomment-42",
        }).encode()

    class UnsupportedPublisher:
        def publish(self, **kwargs):
            return GitHubCommentAttachmentResult(
                status="publish_failed",
                reason=(
                    "github_cli_attachment_auth_unsupported:"
                    "requires_oauth_or_personal_access_token"
                ),
            )

    marker = "<!-- zf-self-issue-evidence:stable -->"
    result = GitHubComProvider(
        transport, cli_comment_publisher=UnsupportedPublisher(),
    ).publish_issue_comment_with_attachments(
        "owner/repo", 9, f"{marker}\nEvidence",
        marker=marker,
        attachments=(Path("screen.png"),),
        access_token="top-secret",
    )

    assert result["status"] == "publish_failed"
    assert result["url"].endswith("#issuecomment-42")
    assert result["reason"].endswith(":summary_comment_published")
    posted = json.loads(calls[-1][3])
    assert "Evidence media is pending" in posted["body"]
    assert marker not in posted["body"]
    assert "zf-self-issue-evidence-fallback:stable" in posted["body"]


def test_github_marker_recovery_rejects_pull_requests_and_unsafe_urls() -> None:
    payload = json.dumps([
        {
            "number": 1,
            "html_url": "https://github.com/owner/repo/issues/1",
            "body": "<!-- zf-self-issue:stable -->",
            "pull_request": {},
        },
        {
            "number": 2,
            "html_url": "https://evil.test/issues/2",
            "body": "<!-- zf-self-issue:stable -->",
        },
        {
            "number": 3,
            "html_url": "https://github.com/owner/repo/issues/3",
            "body": "<!-- zf-self-issue:stable -->",
        },
    ]).encode()
    provider = GitHubComProvider(lambda *_: (200, payload))

    matches = provider.find_by_marker("owner/repo", "stable", access_token="secret")

    assert [item.number for item in matches] == ["3"]


def test_github_device_flow_uses_client_id_without_client_secret() -> None:
    calls = []
    responses = iter([
        (200, json.dumps({
            "device_code": "device-secret",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 900,
            "interval": 5,
        }).encode()),
        (200, json.dumps({
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "expires_in": 28800,
            "refresh_token_expires_in": 15897600,
            "token_type": "bearer",
            "scope": "public_repo",
        }).encode()),
    ])

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        return next(responses)

    client = GitHubDeviceOAuthClient(transport)
    started = client.start(client_id="oauth-client", scope="public_repo")
    token = client.poll(client_id="Iv-client", device_code=started["device_code"])

    assert started["user_code"] == "ABCD-EFGH"
    assert token["status"] == "connected"
    assert token["scope"] == "public_repo"
    assert parse_qs(calls[0][3].decode())["scope"] == ["public_repo"]
    assert all(b"client_secret" not in call[3] for call in calls)


def test_github_attachment_credential_policy_rejects_app_user_token() -> None:
    from zf.integrations.forge.github_auth import assess_github_credential

    oauth = assess_github_credential(
        {
            "access_token": "gho_example",
            "scope": "public_repo",
            "auth_mode": "oauth_device_flow",
        },
        auth_mode="oauth_device_flow",
        required_scope="public_repo",
        require_media=True,
    )
    app_user = assess_github_credential(
        {
            "access_token": "ghu_example",
            "scope": "issues:write",
            "auth_mode": "device_flow",
        },
        auth_mode="oauth_device_flow",
        required_scope="public_repo",
        require_media=True,
    )

    assert oauth.compatible is True
    assert oauth.token_kind == "oauth"
    assert app_user.compatible is False
    assert app_user.reason == "github_attachment_token_unsupported"


def test_github_oauth_scope_repo_covers_public_repo_but_not_the_reverse() -> None:
    from zf.integrations.forge.github_auth import assess_github_credential

    broad = assess_github_credential(
        {"access_token": "gho_broad", "scope": "repo"},
        auth_mode="oauth_device_flow",
        required_scope="public_repo",
        require_media=True,
    )
    narrow = assess_github_credential(
        {"access_token": "gho_narrow", "scope": "public_repo"},
        auth_mode="oauth_device_flow",
        required_scope="repo",
        require_media=True,
    )

    assert broad.compatible is True
    assert narrow.compatible is False
    assert narrow.reason == "github_oauth_scope_insufficient"


def test_github_oauth_target_rejects_unclassified_token_without_media() -> None:
    from zf.integrations.forge.github_auth import assess_github_credential

    assessment = assess_github_credential(
        {"access_token": "opaque", "scope": "public_repo"},
        auth_mode="oauth_device_flow",
        required_scope="public_repo",
        require_media=False,
    )

    assert assessment.compatible is False
    assert assessment.reason == "github_oauth_token_required"


@pytest.mark.parametrize(("status", "payload", "expected", "reason"), [
    (200, {"archived": False, "has_issues": True}, "ready", ""),
    (401, {}, "authorization_required", "credential_rejected"),
    (403, {}, "authorization_required", "credential_rejected"),
    (404, {}, "publish_failed", "github_repository_not_found"),
    (200, {"archived": True, "has_issues": True}, "publish_failed", "github_repository_archived"),
    (200, {"archived": False, "has_issues": False}, "publish_failed", "github_issues_disabled"),
    (500, {}, "outcome_unknown", "github_http_500"),
])
def test_github_repository_publication_preflight_is_read_only(
    status: int,
    payload: dict[str, object],
    expected: str,
    reason: str,
) -> None:
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        return status, json.dumps(payload).encode()

    result = GitHubComProvider(transport).check_issue_publication_access(
        "owner/repo", access_token="secret",
    )

    assert result.status == expected
    assert result.reason == reason
    assert [call[0] for call in calls] == ["GET"]


def test_github_release_assets_are_idempotent_downloadable_links() -> None:
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        if method == "GET" and "/releases/tags/" in url:
            return 404, b"{}"
        if method == "POST" and url.endswith("/releases"):
            return 201, json.dumps({
                "html_url": "https://github.com/owner/repo/releases/tag/triage",
                "upload_url": "https://uploads.github.com/repos/owner/repo/releases/1/assets{?name,label}",
                "assets": [],
            }).encode()
        if method == "POST" and "uploads.github.com" in url:
            return 201, json.dumps({
                "name": "abc123-report.pdf",
                "browser_download_url": "https://github.com/owner/repo/releases/download/triage/abc123-report.pdf",
            }).encode()
        raise AssertionError((method, url))

    result = GitHubComProvider(transport).publish_release_assets(
        "owner/repo", "TRIAGE-1",
        (("report.pdf", "application/pdf", "sha256:abc123", b"pdf"),),
        access_token="secret",
    )
    assert result["status"] == "published"
    assert result["assets"][0]["url"].startswith("https://github.com/owner/repo/releases/download/")
    assert any(call[3] == b"pdf" for call in calls)
    assert any(b"zaofu-triage-evidence-manifest" in call[3] for call in calls if isinstance(call[3], bytes))
