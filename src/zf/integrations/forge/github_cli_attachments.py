"""Small, non-interactive adapter for GitHub CLI comment attachments."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Sequence


@dataclass(frozen=True)
class GitHubCommentAttachmentResult:
    status: str
    url: str = ""
    comment_id: str = ""
    attached: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""


Runner = Callable[..., subprocess.CompletedProcess[str]]
_COMMENT_URL = re.compile(r"https://github\.com/[^\s]+/issues/\d+#issuecomment-\d+")


class GitHubCLICommentPublisher:
    """Publish a comment and optional local media without putting the token in argv."""

    def __init__(self, runner: Runner | None = None, *, timeout: float = 60.0) -> None:
        self.runner = runner or subprocess.run
        self.timeout = timeout

    def publish(
        self,
        *,
        project: str,
        issue_number: int,
        body: str,
        attachments: Sequence[Path] = (),
        access_token: str,
    ) -> GitHubCommentAttachmentResult:
        if not project or issue_number < 1 or not body or not access_token:
            raise ValueError("GitHub comment publication input is invalid")
        paths = tuple(Path(item).resolve() for item in attachments)
        if any(not item.is_file() for item in paths):
            raise ValueError("GitHub comment attachment is not a file")
        with TemporaryDirectory(prefix="zf-gh-comment-") as temp_dir:
            body_path = Path(temp_dir) / "body.md"
            body_path.write_text(body, encoding="utf-8")
            command = [
                "gh", "issue", "comment", str(issue_number), "--repo", project,
                "--body-file", str(body_path),
            ]
            for path in paths:
                command.extend(("--attach", str(path)))
            env = os.environ.copy()
            env["GH_TOKEN"] = access_token
            try:
                completed = self.runner(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    env=env,
                )
            except subprocess.TimeoutExpired:
                return GitHubCommentAttachmentResult(
                    status="outcome_unknown", attached=tuple(path.name for path in paths),
                    reason="github_cli_timeout",
                )
            except OSError as exc:
                return GitHubCommentAttachmentResult(
                    status="publish_failed", attached=tuple(path.name for path in paths),
                    reason=f"github_cli_unavailable:{type(exc).__name__}",
                )
        if completed.returncode != 0:
            stderr = str(completed.stderr or "").casefold()
            if paths and "--attach" in stderr and "unknown flag" in stderr:
                return GitHubCommentAttachmentResult(
                    status="publish_failed",
                    attached=tuple(path.name for path in paths),
                    reason=(
                        "github_cli_attachment_unsupported:"
                        "requires_gh_2_99_or_newer"
                    ),
                )
            if paths and "unsupported authentication type" in stderr:
                return GitHubCommentAttachmentResult(
                    status="publish_failed",
                    attached=tuple(path.name for path in paths),
                    reason=(
                        "github_cli_attachment_auth_unsupported:"
                        "requires_oauth_or_personal_access_token"
                    ),
                )
            return GitHubCommentAttachmentResult(
                status="publish_failed", attached=tuple(path.name for path in paths),
                reason=f"github_cli_exit_{completed.returncode}",
            )
        output = f"{completed.stdout}\n{completed.stderr}"
        match = _COMMENT_URL.search(output)
        return GitHubCommentAttachmentResult(
            status="published" if match else "outcome_unknown",
            url=match.group(0) if match else "",
            attached=tuple(path.name for path in paths),
            reason="" if match else "github_cli_comment_url_missing",
        )
