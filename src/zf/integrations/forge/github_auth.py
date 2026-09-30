"""Pure GitHub credential compatibility checks for Issue publication."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class GitHubCredentialAssessment:
    compatible: bool
    reason: str
    token_kind: str


def github_token_kind(access_token: str) -> str:
    """Classify a token without returning or logging any secret material."""

    if access_token.startswith("gho_"):
        return "oauth"
    if access_token.startswith("ghu_"):
        return "github_app_user"
    if access_token.startswith("ghp_"):
        return "classic_pat"
    if access_token.startswith("github_pat_"):
        return "fine_grained_pat"
    return "unknown"


def github_scope_covers(actual: str, required: str) -> bool:
    scopes = {
        item for item in re.split(r"[\s,]+", str(actual or "").strip()) if item
    }
    required = str(required or "").strip()
    if not required:
        return True
    if required in scopes:
        return True
    return required == "public_repo" and "repo" in scopes


def assess_github_credential(
    secret: Mapping[str, str],
    *,
    auth_mode: str,
    required_scope: str,
    require_media: bool,
) -> GitHubCredentialAssessment:
    token_kind = github_token_kind(str(secret.get("access_token") or ""))
    if not str(secret.get("access_token") or ""):
        return GitHubCredentialAssessment(False, "github_credential_missing", token_kind)

    if auth_mode == "oauth_device_flow":
        if token_kind not in {"oauth", "classic_pat", "fine_grained_pat"}:
            return GitHubCredentialAssessment(
                False,
                (
                    "github_attachment_token_unsupported"
                    if require_media
                    else "github_oauth_token_required"
                ),
                token_kind,
            )
        if not github_scope_covers(str(secret.get("scope") or ""), required_scope):
            return GitHubCredentialAssessment(
                False, "github_oauth_scope_insufficient", token_kind,
            )
        return GitHubCredentialAssessment(True, "", token_kind)

    if auth_mode == "device_flow":
        actual_scope = str(secret.get("scope") or "issues:write")
        if actual_scope != "issues:write":
            return GitHubCredentialAssessment(
                False, "github_app_scope_incompatible", token_kind,
            )
        if require_media and token_kind == "github_app_user":
            return GitHubCredentialAssessment(
                False, "github_attachment_token_unsupported", token_kind,
            )
        return GitHubCredentialAssessment(True, "", token_kind)

    return GitHubCredentialAssessment(False, "github_auth_mode_unsupported", token_kind)


__all__ = [
    "GitHubCredentialAssessment",
    "assess_github_credential",
    "github_scope_covers",
    "github_token_kind",
]
