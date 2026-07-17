---
name: git-evidence
description: "ZaoFu 化的 git 纪律:分支/引用模型即证据链——task_ref、candidate、source_commit 绑定、remote_policy 守卫、多驾驶员规则,与 kernel git 合约机械配对。"
---

# ZaoFu Yoke Git Evidence

在 ZaoFu 里 git 不只是版本管理,是**证据账本**:每个引用有 kernel 消费者,
乱动引用 = 伪造账本。

## 引用模型(谁写、谁读、你能动什么)

| 引用 | 写者 | 读者 | 你的纪律 |
|---|---|---|---|
| `task/<TASK-ID>`(task_ref) | 完成时由 harness 按你的分支铸 | verify checkout、candidate 集成、source_commit 绑定 | 完成后不再追加(追加产生 HEAD≠source_commit 的证据分叉) |
| `worker/<instance>` 工作分支 | 你(writer) | task_ref 铸造 | 只在自己 worktree 操作;禁 rebase 已铸 task_ref 的历史 |
| `candidate/<PDD>` | kernel(cherry-pick 幂等) | judge、quality gate、ship | **永远不要手工 commit 到 candidate** |
| 主 checkout | operator/harness | 所有 worktree 共享对象库 | worker 禁触;`git worktree` 隔离是本体安全线 |

## 与 kernel 合约的配对

- **source_commit 绑定**:完成事件带 commit,verify/judge 按 commit 锁定
  审计对象(pin-commit,FIX-9)——你报的 commit 必须真实存在且可达。
- **remote_policy=local_only**:受管 worktree 装有 pre-push 拒绝钩;
  push 被拒是设计,不是故障,别绕(绕 = 破坏发布门)。
- **commit 前缀**是归档审计的机器输入:`feat:/fix:` 面向用户行为,
  `test:/refactor:/chore:/docs:` 各守其位;TDD 的测试+实现同 commit
  用 `feat:`。
- **多驾驶员纪律**(ddd1dd9 事故后立):只许显式 pathspec `git add`
  (禁 `-A`/`.`/`commit -a`);提交前 `git diff --cached --name-only`
  自检不含他人文件;HEAD 意外移动 = 有并行驾驶员,转工作分支。

## 高频事故与正确姿势(实锚)

- **隔离验证用 worktree,不用 stash**:`git stash pop` 会弹出栈里
  **别人的历史 stash**,冲突标记入树可炸全仓收集(2026-07-06 事故,
  120 个 collection error);`git worktree add /tmp/x <commit>` 才是
  安全隔离。
- **等价补丁重复集成**:cherry-pick 拷贝 hash 不同 patch 相同——判断
  "是否已集成"用 `git rev-list --cherry-pick`,不用 hash 相等。
- **冲突处理**:worker 遇集成冲突不硬解主仓,报 `candidate.conflict`
  语义(rework 路由拥有正确的解法上下文)。

## How to test

模拟:完成一个任务后再追加 commit → 应意识到 task_ref 已铸、追加需走
rework 而非静默推进;对 local_only worktree 尝试 push → 接受拒绝并汇报。
