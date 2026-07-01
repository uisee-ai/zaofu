"""Small-task fast-path policy helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch

from zf.core.config.schema import WorkflowFastPathConfig


@dataclass(frozen=True)
class FastPathDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


def evaluate_fast_path(
    config: WorkflowFastPathConfig,
    *,
    text: str = "",
    scope: list[str] | None = None,
) -> FastPathDecision:
    """Return whether a task is eligible for the configured fast path.

    This is intentionally pure and conservative. The orchestrator can use it
    for previews/tests, while the runtime briefing renders the same criteria to
    the L2 agent that actually creates contracts.
    """
    if not config.enabled:
        return FastPathDecision(False, ["fast_path disabled"])
    scope = scope or []
    if len(scope) > config.max_scope_files:
        return FastPathDecision(
            False,
            [f"scope has {len(scope)} files > max {config.max_scope_files}"],
        )
    joined = " ".join([text, *scope]).lower()
    for keyword in config.blocked_keywords:
        if keyword and keyword.lower() in joined:
            return FastPathDecision(False, [f"blocked keyword: {keyword}"])
    for path in scope:
        for pattern in config.blocked_file_globs:
            if pattern and fnmatch(path, pattern):
                return FastPathDecision(
                    False,
                    [f"blocked path {path} matches {pattern}"],
                )
    if scope and not config.allow_docs_only:
        docs_only = all(
            path.startswith("docs/")
            or path.startswith("README")
            or path.endswith(".md")
            for path in scope
        )
        if docs_only:
            return FastPathDecision(False, ["docs-only scope is disabled"])
    return FastPathDecision(True, ["eligible"])


def render_fast_path_policy(config: WorkflowFastPathConfig) -> str:
    if not config.enabled:
        return ""
    skipped = ", ".join(config.skip_stages) or "(none)"
    blocked_paths = ", ".join(config.blocked_file_globs) or "(none)"
    blocked_keywords = ", ".join(config.blocked_keywords) or "(none)"
    verification = "required" if config.verification_required else "optional"
    return "\n".join(
        [
            "## Small Task Fast Path",
            "",
            "低风险小任务可以走 fast path, 但必须同时满足:",
            f"- scope 文件数 <= {config.max_scope_files}",
            f"- 不命中 blocked_file_globs: {blocked_paths}",
            f"- 不命中 blocked_keywords: {blocked_keywords}",
            f"- verification evidence: {verification}",
            f"- 可跳过阶段: {skipped}",
            "",
            "命中 fast path 时, user.message 可直接创建最终交付 contract 并派发实现角色;",
            "不要拉起 arch/critic/judge。review/test/static gate 仍按 zf.yaml 和证据执行。",
            "不确定是否低风险时, 走完整设计门。",
        ]
    )
