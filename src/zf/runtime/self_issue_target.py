"""Canonical source/target root for ZaoFu Self-Issue workflows.

Self-Issue may be viewed from a registered product project, but the issue is
about the ZaoFu harness itself.  Keep the selected project's state directory
as the run ledger while resolving source, assessment, worktree, and delivery
paths to the repository that contains this runtime package.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


SELF_ISSUE_TARGET_ROOT = "zaofu"


def zaofu_v7_root() -> Path:
    """Return the checkout root that owns the running ZaoFu runtime."""

    return Path(__file__).resolve().parents[3]


def resolve_self_issue_target_root(
    project_root: Path,
    policy: Any | None = None,
) -> Path:
    """Resolve the actual Self-Issue source/Fix root.

    A locked Self-Issue policy deliberately cannot redirect repairs into a
    product project's nested repository.  Unconfigured/unit-test callers keep
    their supplied root so isolated tests remain hermetic.
    """

    supplied = Path(project_root).resolve()
    if policy is None:
        return supplied
    if (
        bool(getattr(policy, "enabled", False))
        and bool(getattr(policy, "target_locked", False))
        and (
            bool(getattr(policy, "target_project", ""))
            or bool(getattr(policy, "targets", {}))
        )
    ):
        return zaofu_v7_root().resolve()
    return supplied


__all__ = [
    "SELF_ISSUE_TARGET_ROOT",
    "resolve_self_issue_target_root",
    "zaofu_v7_root",
]
