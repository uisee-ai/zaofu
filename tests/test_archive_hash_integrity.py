"""归档 hash 漂移防线(2026-06-11-0723)。

dev 分支高频 rebase 已三轮腐蚀 tasks/done 的 '实现 commit: <hash>' 引用,
每轮人肉重映射。本测试机械化:hash 不在 first-parent 时按 title 在
git log 中唯一回退匹配 → fail 并打印 sed 映射表;title 也找不到才属
真损坏。配套纪律:docs/ 正文禁钉 hash(.claude/rules/docs.md)。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_LINE = re.compile(r"^>?\s*实现 commit:\s*([0-9a-f]{7,40})\s+(.+)$")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=_REPO, capture_output=True, text=True,
    )


def _is_ancestor(commit: str) -> bool:
    return _git("merge-base", "--is-ancestor", commit, "HEAD").returncode == 0


def _find_by_title(title: str) -> list[str]:
    out = _git(
        "log", "--fixed-strings", f"--grep={title.strip()}",
        "--format=%h", "-n", "5", "HEAD",
    ).stdout.split()
    return out


def test_done_task_implementation_hashes_resolve_or_map_by_title():
    drifted: list[str] = []
    broken: list[str] = []
    for path in sorted((_REPO / "tasks" / "done").glob("*.md")):
        for line in path.read_text(encoding="utf-8").splitlines():
            m = _LINE.match(line.strip())
            if not m:
                continue
            commit, title = m.group(1), m.group(2)
            # title 可能含 '(+ xxx)' 附注,取主标题段匹配
            main_title = title.split("(+")[0].strip()
            if _is_ancestor(commit):
                continue
            matches = _find_by_title(main_title)
            if len(matches) >= 1:
                drifted.append(
                    f"s/{commit}/{matches[0]}/g  # {path.name}: {main_title[:60]}"
                )
            else:
                broken.append(f"{path.name}: {commit} {main_title[:60]}")
    assert not broken, (
        "归档引用既不在 first-parent 也无 title 匹配(真损坏):\n"
        + "\n".join(broken)
    )
    assert not drifted, (
        "归档 hash 因 rebase 漂移;按标题已找到新 hash,应用以下映射后重跑:\n"
        + "\n".join(drifted)
    )


def test_docs_rules_forbid_pinning_hashes():
    rules = (_REPO / ".claude" / "rules" / "docs.md").read_text(
        encoding="utf-8",
    )
    assert "禁钉 commit hash" in rules, (
        ".claude/rules/docs.md 应包含 docs 正文禁钉 hash 纪律"
    )
