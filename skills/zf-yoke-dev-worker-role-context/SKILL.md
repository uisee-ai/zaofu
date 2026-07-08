---
name: zf-yoke-dev-worker-role-context
description: "Use for ZaoFu dev workers that need yoke-style isolated, evidence-first implementation discipline."
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
- Do not claim done without the ZaoFu done evidence fields.
- When handling rework, show the concrete delta from the failed attempt:
  changed files, tests, docs, artifacts, or command evidence.
- Use the briefing's dispatch id in the completion event when present.
- Include changed files, commands run, command results, and evidence refs in
  `dev.build.done`; review is expected to reject missing evidence rather than
  repair it.
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

## Candidate Integration Constraints (kernel-mechanical)

Your commits on a lane branch are consumed by candidate assembly
(`candidates.py`), so granularity is not cosmetic:

- **One concern per commit.** Candidate integrates your branch by patch-id
  cherry-pick (FIX-10: `rev-list --cherry-pick`, so equivalent patches already
  in base are skipped — idempotent). A grab-bag commit that mixes concerns
  cannot be partially applied: a conflict on any commit **aborts the whole
  cherry-pick for that task ref** and emits `candidate.conflict`, rolling the
  entire package back.
- **Rework continues, never resets.** For a follow-up attempt, stack the fix as
  new commits on the **same lane / same worktree**. Do not `git reset` and
  rebuild history — that breaks attempt binding and gets the completion judged
  stale (`task.completion.stale_rejected`).

Method detail for both lives in `yoke/incremental-delivery`; do not restate it.

## Completion States

Reporting vocabulary only — **not** a kernel-validated enum. The kernel consumes
the completion **event type** plus fields (`dispatch_id`, `source_commit`,
`files_touched`, `task_ref`, `worktree_dirty`), not these words:

- `DONE` — emit `dev.build.done` with the evidence fields above.
- `DONE_WITH_CONCERNS` — skill-owned 约定(无内核校验);still `dev.build.done`,
  carry the concern in the payload for review to weigh.
- `BLOCKED` — skill-owned 约定;stop and report the concrete blocker.
- `NEEDS_CONTEXT` — skill-owned 约定;stop and request the missing context.

Adjacent kernel events you may see referenced in vocab: test evidence flows as
`test.passed` (skill vocab `TEST_PASS_*` maps to this event, not a kernel enum);
abstain/hold flows as the `*.suspended` family (skill vocab `SUSPEND`).
