#!/bin/bash
# 安装 ZaoFu git 钩子到当前 clone(含 worktree:hooks 随主 .git 共享)。
set -e
root=$(git rev-parse --git-common-dir)
cp "$(dirname "$0")/git-hooks/pre-commit" "$root/hooks/pre-commit"
chmod +x "$root/hooks/pre-commit"
echo "installed: $root/hooks/pre-commit"
