"""Regression: ClaudeSessionReader must escape '.' in the cwd (worktree dirs)."""
from __future__ import annotations
from pathlib import Path
from zf.runtime.backend_session_reader import ClaudeSessionReader


def test_dot_in_cwd_is_escaped(tmp_path: Path):
    # worktree cwd like ".../hermes-agent/.zf-cj-min-refactor/.../project"
    # must map to a dir with '.' replaced by '-' (claude's real layout).
    reader = ClaudeSessionReader(projects_root=tmp_path)
    cwd = "/home/u/repo/.zf-x/workdirs/dev/project"
    expected_dir = "-home-u-repo--zf-x-workdirs-dev-project"  # '/.'-> '--'
    d = tmp_path / expected_dir
    (d).mkdir(parents=True)
    (d / "abc.jsonl").write_text("")
    path = reader.session_path(cwd, "abc")
    assert path is not None and path.parent.name == expected_dir


def test_opus_window_avoids_false_overcapacity(tmp_path: Path):
    from zf.runtime.backend_session_reader import _window_for_model
    assert _window_for_model("claude-opus-4-8", 200_000) == 1_000_000
    assert _window_for_model("claude-sonnet-4-6", 200_000) == 1_000_000
    assert _window_for_model("claude-haiku-4-5", 200_000) == 200_000  # unknown -> fallback
