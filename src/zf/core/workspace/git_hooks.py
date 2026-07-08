"""Git pre-commit hook scaffolding for ZaoFu-managed projects.

`zf init` 安装(可 `--no-git-hooks` 跳过)。两条守卫:
1) 运行时真相文件不入库(events.jsonl / kanban.json / session.yaml /
   role_sessions.yaml / feature_list.json 及 state_dir 内容);
2) 大暂存集熔断——多驾驶员共享 index 时,超大 staged 集是误带并行内容的
   高置信信号,须显式 `ZF_ALLOW_LARGE_COMMIT=1` 放行。
已有 pre-commit 钩子时不覆盖(返回 "exists"),尊重项目既有配置。
"""

from __future__ import annotations

from pathlib import Path

PRE_COMMIT_HOOK = """#!/bin/bash
# ZaoFu pre-commit 自检(zf init 安装;重装:zf init --force)。
# 守卫 1:运行时真相文件不入库;守卫 2:大暂存集熔断(多驾驶员 index 误带信号)。

staged=$(git diff --cached --name-only)
[ -z "$staged" ] && exit 0
count=$(printf '%s\\n' "$staged" | wc -l)

runtime_truth=$(printf '%s\\n' "$staged" | grep -E '(^|/)\\.zf[^/]*/|(^|/)(events\\.jsonl|kanban\\.json|session\\.yaml|role_sessions\\.yaml|feature_list\\.json)$')
if [ -n "$runtime_truth" ]; then
  echo "pre-commit BLOCK: 暂存区含运行时真相文件(kernel API 管理,禁入 git):" >&2
  printf '  %s\\n' $runtime_truth >&2
  exit 1
fi

if [ "$count" -gt 25 ] && [ -z "$ZF_ALLOW_LARGE_COMMIT" ]; then
  echo "pre-commit BLOCK: 暂存 $count 个文件(>25)。" >&2
  echo "多驾驶员共享 index 下,大暂存集常是误带并行内容的信号。" >&2
  echo "逐一核对以下清单确属本次意图后,ZF_ALLOW_LARGE_COMMIT=1 重试:" >&2
  printf '  %s\\n' $staged >&2
  exit 1
fi

echo "pre-commit: 即将提交 $count 个文件:" >&2
printf '  %s\\n' $staged | head -30 >&2
exit 0
"""


def install_pre_commit_hook(project_root: Path) -> str:
    """Install the ZaoFu pre-commit hook. Returns installed|exists|no-git."""
    git_dir = project_root / ".git"
    if not git_dir.is_dir():
        # 非 git 仓库,或 worktree(.git 为文件,钩子归主仓管理)。
        return "no-git"
    hooks_dir = git_dir / "hooks"
    target = hooks_dir / "pre-commit"
    if target.exists():
        return "exists"
    try:
        hooks_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(PRE_COMMIT_HOOK, encoding="utf-8")
        target.chmod(0o755)
    except OSError:
        return "no-git"
    return "installed"
