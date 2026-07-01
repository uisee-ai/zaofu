"""Sprint §6 — CLI hookup tests."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from zf.cli.autoresearch import register


def _build_parser() -> argparse.ArgumentParser:
    """Mirror what zf.cli.main does — build the root parser, register
    autoresearch, then we can parse argv against it."""
    root = argparse.ArgumentParser(prog="zf")
    subs = root.add_subparsers(dest="cmd")
    register(subs)
    return root


def test_loop_subcommand_registered() -> None:
    parser = _build_parser()
    args = parser.parse_args([
        "autoresearch", "loop",
        "--scenarios", "self-eval-backlog",
        "--worktree", "/tmp/wt",
        "--max-iterations", "1",
    ])
    assert args.autoresearch_command == "loop"
    assert args.scenarios == ["self-eval-backlog"]
    assert args.worktree == Path("/tmp/wt")
    assert args.max_iterations == 1


def test_loop_multiple_scenarios_via_nargs() -> None:
    parser = _build_parser()
    args = parser.parse_args([
        "autoresearch", "loop",
        "--scenarios", "scen-A", "scen-B",
        "--worktree", "/tmp/wt",
    ])
    assert args.scenarios == ["scen-A", "scen-B"]


def test_loop_defaults() -> None:
    parser = _build_parser()
    args = parser.parse_args([
        "autoresearch", "loop",
        "--scenarios", "s",
        "--worktree", "/tmp/wt",
    ])
    assert args.max_iterations == 10
    assert args.budget_usd == 200.0
    assert args.reflect_backend == "claude-code"
    assert args.fix_wait_strategy == "head_change"
    assert args.fix_wait_timeout == 1800
    assert args.parent_state_dir is None
    assert args.output_dir is None
    assert args.review_gate == "off"


def test_loop_reflect_backend_defaults_from_env(monkeypatch) -> None:
    monkeypatch.setenv("ZF_AUTORESEARCH_REFLECT_BACKEND", "codex")
    parser = _build_parser()
    args = parser.parse_args([
        "autoresearch", "loop",
        "--scenarios", "s",
        "--worktree", "/tmp/wt",
    ])

    assert args.reflect_backend == "codex"


def test_loop_reflect_backend_cli_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv("ZF_AUTORESEARCH_REFLECT_BACKEND", "codex")
    parser = _build_parser()
    args = parser.parse_args([
        "autoresearch", "loop",
        "--scenarios", "s",
        "--worktree", "/tmp/wt",
        "--reflect-backend", "claude-code",
    ])

    assert args.reflect_backend == "claude-code"


def test_run_and_loop_parse_review_gate_modes() -> None:
    parser = _build_parser()
    run_args = parser.parse_args([
        "autoresearch", "run",
        "--worktree", "/tmp/wt",
        "--review-gate", "auto",
    ])
    loop_args = parser.parse_args([
        "autoresearch", "loop",
        "--scenarios", "s",
        "--worktree", "/tmp/wt",
        "--review-gate", "always",
    ])

    assert run_args.review_gate == "auto"
    assert loop_args.review_gate == "always"


def test_loop_fix_wait_strategy_choices() -> None:
    parser = _build_parser()
    # Valid choice should parse.
    args = parser.parse_args([
        "autoresearch", "loop",
        "--scenarios", "s", "--worktree", "/tmp/wt",
        "--fix-wait-strategy", "manual",
    ])
    assert args.fix_wait_strategy == "manual"
    # Invalid choice should fail.
    with pytest.raises(SystemExit):
        parser.parse_args([
            "autoresearch", "loop",
            "--scenarios", "s", "--worktree", "/tmp/wt",
            "--fix-wait-strategy", "telepathy",
        ])


def test_loop_required_args_missing() -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit):
        # --scenarios is required.
        parser.parse_args(["autoresearch", "loop", "--worktree", "/tmp/wt"])


def test_loop_handler_invokes_run_loop(tmp_path: Path) -> None:
    """Smoke: ensure CLI plumbing reaches zf.autoresearch.loop.run_loop
    with an injectable LoopConfig. We patch run_loop to record the cfg
    it was called with and exit cleanly."""
    parser = _build_parser()
    args = parser.parse_args([
        "autoresearch", "loop",
        "--scenarios", "scen-A",
        "--worktree", str(tmp_path / "wt"),
        "--parent-state-dir", str(tmp_path / ".zf"),
        "--max-iterations", "1",
        "--output-dir", str(tmp_path / "loop"),
        "--review-gate", "auto",
    ])

    from zf.autoresearch.loop import LoopResult

    captured = {}

    def _fake_run_loop(cfg, **kwargs):
        captured["cfg"] = cfg
        captured["kwargs"] = kwargs
        return LoopResult(
            iterations=0, final_status="dry-run",
            journal_path=tmp_path / "j.jsonl",
            report_path=tmp_path / "r.md",
        )

    with patch("zf.cli.autoresearch.run_loop", _fake_run_loop):
        exit_code = args.func(args)

    # Smoke test: handler must return some int exit code.
    assert isinstance(exit_code, int)
    cfg = captured["cfg"]
    assert cfg.scenarios == ["scen-A"]
    assert cfg.max_iterations == 1
    assert cfg.review_gate == "auto"
    # Injectable callables must be wired by the CLI handler.
    kwargs = captured["kwargs"]
    expected_fns = {
        "autoresearch_fn", "eval_collector_fn", "reflect_fn",
        "git_head_fn", "git_diff_fn", "backlog_fn", "wait_for_fix_fn",
    }
    assert expected_fns.issubset(kwargs.keys()), (
        f"missing injectables: {expected_fns - set(kwargs.keys())}"
    )


def test_loop_handler_returns_zero_for_done_status(tmp_path: Path) -> None:
    parser = _build_parser()
    args = parser.parse_args([
        "autoresearch", "loop",
        "--scenarios", "scen-A",
        "--worktree", str(tmp_path / "wt"),
        "--parent-state-dir", str(tmp_path / ".zf"),
        "--max-iterations", "1",
        "--output-dir", str(tmp_path / "loop"),
    ])

    from zf.autoresearch.loop import LoopResult

    def _fake_run_loop(cfg, **kwargs):
        return LoopResult(
            iterations=1,
            final_status="done",
            journal_path=tmp_path / "j.jsonl",
            report_path=tmp_path / "r.md",
        )

    with patch("zf.cli.autoresearch.run_loop", _fake_run_loop):
        exit_code = args.func(args)

    assert exit_code == 0


def test_loop_handler_defaults_to_project_state_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: test\n  state_dir: runtime-state\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    parser = _build_parser()
    args = parser.parse_args([
        "autoresearch", "loop",
        "--scenarios", "scen-A",
        "--worktree", str(tmp_path / "wt"),
        "--max-iterations", "1",
    ])

    from zf.autoresearch.loop import LoopResult

    captured = {}

    def _fake_run_loop(cfg, **kwargs):
        captured["cfg"] = cfg
        return LoopResult(
            iterations=0,
            final_status="dry-run",
            journal_path=tmp_path / "j.jsonl",
            report_path=tmp_path / "r.md",
        )

    with patch("zf.cli.autoresearch.run_loop", _fake_run_loop):
        args.func(args)

    cfg = captured["cfg"]
    assert cfg.parent_state_dir == project / "runtime-state"
    assert cfg.output_dir == project / "runtime-state" / "autoresearch" / "loop"
