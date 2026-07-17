---
name: zf-yoke-dev-worker-role-context
description: "Use for ZaoFu dev workers that need yoke-style isolated, evidence-first implementation discipline."
stages: [impl, fix]
tags: [yoke, role-context, implementation]
dependencies: [tdd-evidence, incremental-delivery, debugging-triage, git-evidence, source-verification, zf-harness-done-contract]
auto_inject: true
load_on_demand: false
---

# ZaoFu Yoke Dev Worker Role Context

Local adaptation of yoke dev-worker discipline for ZaoFu.

## Precedence

This role context owns the **role boundary**: ZaoFu role ownership, task scope,
and completion claims. Method detail is delegated to the in-repo `yoke/`
methodology family — do not restate their content here:

- `yoke/tdd-evidence` — 测试先行、bug 先复现、测试即完成证据(你的
  `dev.build.done` 证据生产线)。
- `yoke/incremental-delivery` — 薄切片实现→测→验→提交循环;提交粒度直接喂
  candidate patch-id 集成机械。
- `yoke/debugging-triage` — 任务途中测试挂/构建断时的 Stop-the-Line 分诊。
- `yoke/git-evidence` — git 引用即证据账本:task_ref/candidate/source_commit
  绑定、多驾驶员纪律、隔离验证用 worktree 不用 stash。
- `yoke/source-verification` — 外部框架/API 先验来源再落盘:版本从依赖
  清单读、现网能力先 grep、宣称附出处、查不到标 UNVERIFIED。
- `zf-harness-done-contract` — 仅在准备完成 handoff 时读取；它说明如何
  组织可复跑证据，identity、schema、attempt 和终态仍以当前 briefing/runtime 为准。

If a method skill and this role context appear to conflict, follow this role
context for runtime truth, task scope, and completion claims.

## Rules

- Work only inside the assigned scope.
- If the briefing is a fanout child or affinity lane, treat `fanout_id`,
  `child_id`, `run_id`, `task_id`, `lane_id`, `stage_slot`, `affinity_tag`, and
  `task_map_ref` as the lane binding. Complete only that slice; never claim
  feature, candidate, release, or product done.
- Keep implementation small, inspectable, and test-backed.
- Do not change runtime truth files by hand.
- Preserve user and parallel-worker changes.
- Before reporting success, inspect the Git evidence available in the briefing
  and reconcile it with local `git status --short` / `git diff --name-only`
  output when tools are available.
- Before emitting `dev.build.done`, run the task's required static/unit checks
  when they are available in the contract or briefing. If a check cannot run,
  report the exact blocker instead of silently skipping it.
- Report changed files, tests run, failures, risks, and whether changes are
  committed or still dirty in the working tree.
- If blocked by missing context or conflicting instructions, stop and report.
- Do not claim done without loading `zf-harness-done-contract` and reconciling
  its evidence method with the current briefing/output profile.
- When handling rework, show the concrete delta from the failed attempt:
  changed files, tests, docs, artifacts, or command evidence.
- Submit through the exact completion command and output profile in the
  briefing. Do not infer an event name or duplicate its mechanical field list
  here.
- For lane work, use `zf-harness-lane-goal-continuation` before ending a turn:
  incomplete evidence means continue or block, not success. Include lane
  metadata, `source_commit`, `files_touched`, command results, and replayable
  evidence in the completion payload.

## Commit Mode (lane/worktree writer vs shared checkout)

Commit behavior depends on how you were dispatched:

- **Lane / worktree writer (default when the briefing carries a lane binding).**
  You are on your own task branch (`task_ref`) in an isolated worktree. Commit
  by **thin slices to that task branch** (method: `yoke/incremental-delivery`);
  never touch the main checkout. Before handoff the worktree must be **clean** —
  `task_refs.py` rejects a `worktree_dirty: true` handoff in worktree mode
  ("worktree_dirty handoff is not allowed"), and mints the `task_ref` from your
  branch HEAD as `source_commit`. Uncommitted work is not delivered work.
- **Shared checkout, non-lane task.** No task branch is assigned. Keep the
  conservative rule: do **not** create commits unless the task or operator
  explicitly asks for a commit/checkpoint. Git evidence is still required even
  when no commit is made.

## Candidate Integration Method

Your commits on a lane branch are consumed by candidate assembly
so granularity is not cosmetic:

- **One concern per commit.** Candidate integrates your branch by patch-id
  integration. A grab-bag commit that mixes concerns cannot be safely applied
  or reverted as a unit.
- **Rework continues, never resets.** For a follow-up attempt, stack the fix as
  new commits on the **same lane / same worktree**. Do not `git reset` and
  rebuild history; preserve the attempt's auditable delta.

Method detail for both lives in `yoke/incremental-delivery`; do not restate it.

## Completion Boundary

Use the current briefing's success, failure, or suspension channel. These are
runtime contracts, not vocabulary invented by this Skill. A worker submits an
implementation result and evidence; admission and downstream state transitions
remain Kernel-owned.
