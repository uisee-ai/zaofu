"""Resolve Issue source roots independently from external publication targets.

Warp's workspace model makes the active repository an explicit session
context.  Self-Issue follows the same rule: ``target_project`` identifies the
forge destination, while ``source_mode`` controls which checkout is inspected.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any


SELF_ISSUE_TARGET_ROOT = "zaofu"


def zaofu_v7_root() -> Path:
    """Return the checkout root that owns the running ZaoFu runtime."""

    return Path(__file__).resolve().parents[3]


def resolve_self_issue_target_root(
    project_root: Path,
    policy: Any | None = None,
    *,
    source_root: Path | None = None,
    source_mode: str | None = None,
) -> Path:
    """Resolve the user-side report source; developer execution is separate."""

    supplied = Path(project_root).resolve()
    mode = str(
        source_mode
        or getattr(policy, "source_mode", "")
        or "current_project"
    ).strip().lower()
    if source_root is not None:
        return Path(source_root).resolve()
    configured_root = str(getattr(policy, "source_root", "") or "").strip()
    if mode == "explicit_target" and configured_root:
        candidate = Path(configured_root).expanduser()
        return (candidate if candidate.is_absolute() else supplied / candidate).resolve()
    if mode == "zaofu_self_issue":
        return zaofu_v7_root().resolve()
    # current_project, explicit_target and issue_repository are resolved by the
    # caller's workspace/repository binding.  Never infer ZaoFu from a locked
    # publication target.
    return supplied


def source_binding_for(root: Path, *, mode: str = "current_project") -> dict[str, str]:
    """Return a durable, provider-neutral workspace/repository binding."""

    resolved = Path(root).resolve()
    revision = ""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=resolved,
            capture_output=True, text=True, timeout=5, check=False,
        )
        if completed.returncode == 0:
            revision = completed.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "mode": str(mode or "current_project"),
        "workspace_root": str(resolved),
        "repository_root": str(resolved),
        "revision": revision,
    }


__all__ = [
    "SELF_ISSUE_TARGET_ROOT",
    "resolve_self_issue_target_root",
    "zaofu_v7_root",
    "source_binding_for",
]
