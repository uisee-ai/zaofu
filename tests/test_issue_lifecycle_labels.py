from __future__ import annotations

import json

from zf.core.issue_triage.lifecycle_states import (
    REMOTE_LIFECYCLE_STATES,
    reconcile_lifecycle_labels,
)
from zf.integrations.forge.github import GitHubComProvider


def test_remote_lifecycle_registry_is_exactly_32_states() -> None:
    assert len(REMOTE_LIFECYCLE_STATES) == 32
    assert len(set(REMOTE_LIFECYCLE_STATES)) == 32
    assert "ready-to-spec" not in REMOTE_LIFECYCLE_STATES


def test_reconcile_lifecycle_labels_preserves_metadata() -> None:
    assert reconcile_lifecycle_labels(
        ["triaging", "bug", "area:web", "priority:p2", "bug"],
        "fix_queued",
    ) == ("bug", "area:web", "priority:p2", "fix_queued")


def test_github_lifecycle_reconcile_removes_only_stale_managed_labels() -> None:
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        if method == "GET":
            return 200, json.dumps([
                {"name": "triaging"},
                {"name": "bug"},
                {"name": "area:web"},
                {"name": "priority:p2"},
            ]).encode()
        if method == "POST" and url.endswith("/issues/8/labels"):
            return 200, b"[]"
        if method == "POST" and url.endswith("/labels"):
            return 201, b"{}"
        if method == "DELETE" and url.endswith("/labels/triaging"):
            return 204, b""
        raise AssertionError((method, url))

    result = GitHubComProvider(transport).reconcile_lifecycle_label(
        "owner/repo", 8, "fix_queued", access_token="secret",
    )

    assert result.status == "reconciled"
    assert [call[0] for call in calls] == ["GET", "POST", "POST", "DELETE"]
    assert json.loads(calls[1][3] or b"{}") == {
        "name": "fix_queued", "color": "6E7781",
    }
    assert json.loads(calls[2][3] or b"{}") == {"labels": ["fix_queued"]}
    assert all(b"secret" not in (call[3] or b"") for call in calls)


def test_github_lifecycle_reconcile_is_idempotent_when_state_matches() -> None:
    calls: list[str] = []

    def transport(method, url, headers, body):
        calls.append(method)
        return 200, json.dumps([
            {"name": "fixing"}, {"name": "bug"},
        ]).encode()

    result = GitHubComProvider(transport).reconcile_lifecycle_label(
        "owner/repo", 8, "fixing", access_token="secret",
    )

    assert result.status == "reconciled"
    assert result.reason == "unchanged"
    assert calls == ["GET"]


def test_existing_repository_label_color_is_returned_with_confirmed_update():
    def transport(method, url, headers, body):
        if method == "GET":
            return 200, b'[{"name":"triage_queued","color":"AAAAAA"},{"name":"bug","color":"D73A4A"}]'
        if method == "POST" and "/issues/" not in url:
            return 422, b'{"message":"already_exists"}'
        if method == "POST":
            return 200, b'[{"name":"triage_queued","color":"AAAAAA"},{"name":"bug","color":"D73A4A"},{"name":"triage_cancelled","color":"b60205"}]'
        assert method == "DELETE"
        return 204, b""
    result = GitHubComProvider(transport).reconcile_lifecycle_label(
        "owner/repo", 8, "triage_cancelled", access_token="test",
    )
    assert result.status == "reconciled"
    assert result.label_colors == {"bug": "D73A4A", "triage_cancelled": "B60205"}


def test_unchanged_label_keeps_remote_color():
    provider = GitHubComProvider(lambda *_: (200, b'[{"name":"triage_cancelled","color":"b60205"}]'))
    result = provider.reconcile_lifecycle_label("owner/repo", 8, "triage_cancelled", access_token="test")
    assert result.status == "reconciled"
    assert result.label_colors == {"triage_cancelled": "B60205"}
