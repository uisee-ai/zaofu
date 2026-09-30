from __future__ import annotations

import subprocess

from zf.integrations.forge.github_cli_attachments import GitHubCLICommentPublisher


def test_github_cli_attachment_publisher_uses_token_env_and_media_args(tmp_path):
    media = tmp_path / "screen.png"
    media.write_bytes(b"png")
    calls = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args[0], 0,
            stdout="https://github.com/acme/app/issues/7#issuecomment-42\n",
            stderr="",
        )

    result = GitHubCLICommentPublisher(runner=runner).publish(
        project="acme/app", issue_number=7,
        body="<!-- marker -->\nEvidence", attachments=(media,),
        access_token="secret-token",
    )

    assert result.status == "published"
    assert result.url.endswith("#issuecomment-42")
    (command,), options = calls[0]
    assert command[:6] == ["gh", "issue", "comment", "7", "--repo", "acme/app"]
    assert "--attach" in command and str(media) in command
    assert options["env"]["GH_TOKEN"] == "secret-token"
    assert "secret-token" not in " ".join(command)


def test_github_cli_attachment_publisher_distinguishes_timeout():
    def runner(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 60)

    result = GitHubCLICommentPublisher(runner=runner).publish(
        project="acme/app", issue_number=7,
        body="<!-- marker -->", access_token="secret-token",
    )
    assert result.status == "outcome_unknown"
    assert result.reason == "github_cli_timeout"


def test_github_cli_attachment_publisher_reports_unsupported_attach_flag(tmp_path):
    media = tmp_path / "screen.png"
    media.write_bytes(b"png")

    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0], 1, stdout="", stderr="unknown flag: --attach\n",
        )

    result = GitHubCLICommentPublisher(runner=runner).publish(
        project="acme/app", issue_number=7,
        body="<!-- marker -->", attachments=(media,),
        access_token="secret-token",
    )

    assert result.status == "publish_failed"
    assert result.reason == "github_cli_attachment_unsupported:requires_gh_2_99_or_newer"


def test_github_cli_attachment_publisher_reports_unsupported_app_token(tmp_path):
    media = tmp_path / "screen.png"
    media.write_bytes(b"png")

    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0], 1, stdout="", stderr="unsupported authentication type\n",
        )

    result = GitHubCLICommentPublisher(runner=runner).publish(
        project="acme/app", issue_number=7,
        body="<!-- marker -->", attachments=(media,),
        access_token="secret-token",
    )

    assert result.status == "publish_failed"
    assert result.reason == (
        "github_cli_attachment_auth_unsupported:"
        "requires_oauth_or_personal_access_token"
    )
