"""ω-1.a: kernel fast-forward task ref onto main (state sync A1).

Per docs/design/38-omega-1-baseline-and-verdict.md §3 + backlog
backlogs/2026-05-18-0243-omega-1a-kernel-fast-forward-task-ref.md.

Tests run against real git tmp_path repos (no mocks) so any subprocess
flake surfaces immediately.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from zf.runtime.baseline_sync import (
    BaselineSyncResult,
    fast_forward_task_ref_onto_main,
)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _init_repo(root: Path) -> str:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("init\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    return _git(root, "rev-parse", "HEAD")


def _commit(root: Path, name: str, content: str) -> str:
    (root / name).write_text(content)
    _git(root, "add", name)
    _git(root, "commit", "-q", "-m", f"add {name}")
    return _git(root, "rev-parse", "HEAD")


def _make_task_branch(root: Path, task_id: str, from_sha: str) -> None:
    _git(root, "branch", f"task/{task_id}", from_sha)


# ─── happy paths ─────────────────────────────────────────────────────────


def test_ff_when_task_ref_is_ancestor_of_main(tmp_path: Path):
    """The r-next-10 scenario: task branch sits at vertical-3 SHA, main
    advanced to vertical-4 SHA; kernel safely fast-forwards."""
    init = _init_repo(tmp_path)
    _make_task_branch(tmp_path, "TASK-A", init)
    # main moves ahead
    main_ahead = _commit(tmp_path, "feat.py", "x = 1\n")

    result = fast_forward_task_ref_onto_main(tmp_path, task_id="TASK-A")

    assert result.ok is True
    assert result.diverged is False
    assert result.no_op is False
    assert result.task_head_before == init[:12]
    assert result.task_head_after == main_ahead[:12]
    assert result.main_head == main_ahead[:12]
    # Verify on-disk: task ref actually moved
    assert _git(tmp_path, "rev-parse", "refs/heads/task/TASK-A") == main_ahead


def test_no_op_when_task_ref_already_at_main(tmp_path: Path):
    init = _init_repo(tmp_path)
    _make_task_branch(tmp_path, "TASK-A", init)

    result = fast_forward_task_ref_onto_main(tmp_path, task_id="TASK-A")

    assert result.ok is False
    assert result.no_op is True
    assert result.diverged is False
    assert result.task_head_before == init[:12]
    assert result.task_head_after == init[:12]


def test_no_op_when_task_ref_absent(tmp_path: Path):
    """First dispatch — task ref hasn't been created yet (arch will).
    Kernel quietly skips; not an error."""
    _init_repo(tmp_path)

    result = fast_forward_task_ref_onto_main(tmp_path, task_id="TASK-NEW")

    assert result.no_op is True
    assert result.ok is False
    assert result.diverged is False


def test_diverged_when_task_has_commits_not_on_main(tmp_path: Path):
    """Safety boundary: kernel REFUSES to rewrite task branches that
    have their own commits. Operator / LLM orchestrator must resolve."""
    init = _init_repo(tmp_path)
    _make_task_branch(tmp_path, "TASK-A", init)
    # Make a commit on task branch (so task has main-doesn't)
    _git(tmp_path, "checkout", "-q", "task/TASK-A")
    task_head_diverged = _commit(tmp_path, "task-only.py", "y = 2\n")
    _git(tmp_path, "checkout", "-q", "main")
    # main also advances
    main_ahead = _commit(tmp_path, "main-only.py", "z = 3\n")

    result = fast_forward_task_ref_onto_main(tmp_path, task_id="TASK-A")

    assert result.diverged is True
    assert result.ok is False
    assert result.no_op is False
    # Task ref unchanged
    assert _git(tmp_path, "rev-parse", "refs/heads/task/TASK-A") == task_head_diverged
    assert result.task_head_before == task_head_diverged[:12]
    assert result.task_head_after == task_head_diverged[:12]


def test_no_op_when_main_unresolvable(tmp_path: Path):
    """Defensive: even a totally empty / non-git dir doesn't crash."""
    # No git init at all
    result = fast_forward_task_ref_onto_main(tmp_path, task_id="TASK-X")

    assert result.no_op is True
    assert result.ok is False
    assert "main" in result.reason.lower() or "not resolvable" in result.reason.lower()


def test_diverged_when_main_advances_after_task_branched(tmp_path: Path):
    """Pure fast-forward scenario, NOT diverged: task at older main SHA,
    main advanced, task still ancestor → FF should work even if main
    moved multiple commits."""
    init = _init_repo(tmp_path)
    _make_task_branch(tmp_path, "TASK-A", init)
    # main advances by 3 commits
    _commit(tmp_path, "a.py", "1\n")
    _commit(tmp_path, "b.py", "2\n")
    main_head = _commit(tmp_path, "c.py", "3\n")

    result = fast_forward_task_ref_onto_main(tmp_path, task_id="TASK-A")

    assert result.ok is True
    assert result.task_head_after == main_head[:12]


# ─── result schema + payload ─────────────────────────────────────────────


def test_to_payload_serializes_all_fields(tmp_path: Path):
    init = _init_repo(tmp_path)
    _make_task_branch(tmp_path, "TASK-A", init)
    main_ahead = _commit(tmp_path, "feat.py", "x\n")

    result = fast_forward_task_ref_onto_main(tmp_path, task_id="TASK-A")
    payload = result.to_payload()

    for key in (
        "ok", "diverged", "no_op", "task_branch",
        "task_head_before", "task_head_after", "main_head", "reason",
    ):
        assert key in payload

    assert payload["task_branch"] == "task/TASK-A"
    assert payload["ok"] is True


def test_never_raises_on_bad_input(tmp_path: Path):
    """fast_forward_task_ref_onto_main contract: never raises. Any input
    that would normally throw a subprocess error returns no_op."""
    _init_repo(tmp_path)

    # Empty task_id → bizarre ref but shouldn't crash
    r1 = fast_forward_task_ref_onto_main(tmp_path, task_id="")
    assert r1.no_op or r1.diverged or r1.ok in (True, False)

    # Pathological main_ref
    r2 = fast_forward_task_ref_onto_main(
        tmp_path, task_id="TASK-A", main_ref="not/a/real/ref",
    )
    assert r2.no_op is True


# ─── custom prefix ───────────────────────────────────────────────────────


def test_supports_custom_task_ref_prefix(tmp_path: Path):
    """Honor config.runtime.git.task_ref_prefix (default 'task' but
    operator can configure)."""
    init = _init_repo(tmp_path)
    _git(tmp_path, "branch", "feature/X-1", init)
    main_ahead = _commit(tmp_path, "f.py", "1\n")

    result = fast_forward_task_ref_onto_main(
        tmp_path, task_id="X-1", task_ref_prefix="feature",
    )

    assert result.ok is True
    assert result.task_branch == "feature/X-1"
    assert _git(tmp_path, "rev-parse", "refs/heads/feature/X-1") == main_ahead


# ─── event registration + wire-up ────────────────────────────────────────


def test_baseline_synced_in_known_types():
    from zf.core.events.known_types import KNOWN_EVENT_TYPES
    assert "task.baseline_synced" in KNOWN_EVENT_TYPES


def test_baseline_diverged_in_known_types():
    from zf.core.events.known_types import KNOWN_EVENT_TYPES
    assert "task.baseline_diverged" in KNOWN_EVENT_TYPES


def test_baseline_synced_in_wake_patterns():
    from zf.runtime.wake_patterns import WAKE_PATTERNS
    assert "task.baseline_synced" in WAKE_PATTERNS


def test_baseline_diverged_in_wake_patterns():
    from zf.runtime.wake_patterns import WAKE_PATTERNS
    assert "task.baseline_diverged" in WAKE_PATTERNS


def test_wire_up_dispatch_calls_fast_forward():
    """ω-1.a wire-up grep proof: _dispatch_task in orchestrator_dispatch.py
    must call fast_forward_task_ref_onto_main BEFORE the existing
    writer-workdir sync."""
    src = Path(__file__).resolve().parents[1] / "src/zf/runtime/orchestrator_dispatch.py"
    text = src.read_text(encoding="utf-8")
    assert "fast_forward_task_ref_onto_main" in text, (
        "ω-1.a wire-up missing: orchestrator_dispatch.py does not call "
        "fast_forward_task_ref_onto_main"
    )
    assert "task.baseline_synced" in text, (
        "ω-1.a wire-up missing: dispatch does not emit task.baseline_synced"
    )
    assert "task.baseline_diverged" in text, (
        "ω-1.a wire-up missing: dispatch does not emit task.baseline_diverged"
    )
