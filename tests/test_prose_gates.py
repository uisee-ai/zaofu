"""K5:prose 三高危规则补门(turn WARN / push 守卫 / memory.note 降级)。"""

from __future__ import annotations

import subprocess
from pathlib import Path


class TestMemoryNoteDowngrade:
    def test_briefing_renders_single_optional_hint(self):
        src = Path("src/zf/runtime/injection.py").read_text(encoding="utf-8")
        assert "可选:记录跨会话经验" in src
        assert "decide before emitting completion event" not in src


class TestTurnBudgetWarn:
    def test_warn_emitted_above_threshold(self):
        src = Path("src/zf/runtime/orchestrator.py").read_text(encoding="utf-8")
        assert "orchestrator.turn_budget.warn" in src
        assert "len(actions) > 25" in src


class TestLocalOnlyPushGuard:
    def _git(self, cwd, *args):
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
        )

    def test_managed_worktree_push_rejected_main_checkout_unaffected(
        self, tmp_path,
    ):
        # 真实 git 布局:主仓 + bare remote + 受管 worktree
        repo = tmp_path / "repo"
        repo.mkdir()
        self._git(repo, "init", "-q", "-b", "master")
        self._git(repo, "-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-q", "--allow-empty", "-m", "init")
        remote = tmp_path / "remote.git"
        self._git(tmp_path, "init", "-q", "--bare", str(remote))
        self._git(repo, "remote", "add", "origin", str(remote))

        wt = tmp_path / "wt"
        self._git(repo, "worktree", "add", "-q", str(wt), "-b", "lane", "master")

        # 模拟 K5-2 安装(与 workdirs._install_local_only_push_guard 同步骤)
        hooks = wt / ".zf-hooks"
        hooks.mkdir()
        hook = hooks / "pre-push"
        hook.write_text("#!/bin/sh\necho blocked >&2\nexit 1\n")
        hook.chmod(0o755)
        self._git(repo, "config", "extensions.worktreeConfig", "true")
        self._git(wt, "config", "--worktree", "core.hooksPath", str(hooks))

        # 受管 worktree push 被拒
        r = self._git(wt, "push", "origin", "lane")
        assert r.returncode != 0 and "blocked" in r.stderr
        # 主 checkout 不受影响(最大误伤面验证)
        r2 = self._git(repo, "push", "-q", "origin", "master")
        assert r2.returncode == 0, r2.stderr

    def test_guard_only_for_local_only_policy(self):
        src = Path("src/zf/runtime/workdirs.py").read_text(encoding="utf-8")
        assert 'policy != "local_only"' in src
        assert "--worktree" in src  # 绝不写共享 hooks
