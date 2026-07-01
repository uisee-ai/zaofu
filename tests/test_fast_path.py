from __future__ import annotations

from zf.core.config.schema import WorkflowFastPathConfig
from zf.runtime.fast_path import evaluate_fast_path, render_fast_path_policy


def test_fast_path_allows_small_unblocked_scope() -> None:
    cfg = WorkflowFastPathConfig(
        enabled=True,
        max_scope_files=2,
        blocked_file_globs=["src/zf/runtime/**"],
        blocked_keywords=["security"],
    )

    decision = evaluate_fast_path(
        cfg,
        text="fix README typo",
        scope=["README.md"],
    )

    assert decision.allowed is True


def test_fast_path_blocks_large_scope() -> None:
    cfg = WorkflowFastPathConfig(enabled=True, max_scope_files=1)

    decision = evaluate_fast_path(cfg, scope=["a.py", "b.py"])

    assert decision.allowed is False
    assert "max 1" in decision.reasons[0]


def test_fast_path_blocks_sensitive_scope_and_keyword() -> None:
    cfg = WorkflowFastPathConfig(
        enabled=True,
        blocked_file_globs=["src/zf/runtime/**"],
        blocked_keywords=["auth"],
    )

    by_path = evaluate_fast_path(cfg, scope=["src/zf/runtime/orchestrator.py"])
    by_keyword = evaluate_fast_path(cfg, text="change auth provider")

    assert by_path.allowed is False
    assert "blocked path" in by_path.reasons[0]
    assert by_keyword.allowed is False
    assert "blocked keyword" in by_keyword.reasons[0]


def test_fast_path_policy_renders_only_when_enabled() -> None:
    assert render_fast_path_policy(WorkflowFastPathConfig()) == ""

    text = render_fast_path_policy(WorkflowFastPathConfig(enabled=True))

    assert "Small Task Fast Path" in text
    assert "不要拉起 arch/critic/judge" in text
