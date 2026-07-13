---
name: zf-self-repair-apply-closeout
description: "Apply and clean up a verified ZaoFu self-repair closeout. Use when an operator explicitly asks to process an `autoresearch.repair.closeout.required` event, review and merge/cherry-pick a `self-repair/...` branch, then remove the isolated repair worktree and branch. This is the human apply gate after `zf-self-repair`; never use it to perform the repair itself, never apply an unverified or dirty worktree, and never push unless separately requested."
---

# ZaoFu Self-Repair Apply Closeout

## Objective

Apply a verified isolated self-repair branch into the live ZaoFu repo, run the
required verification, then clean up the repair worktree and branch.

This skill is the apply/cleanup gate after `zf-self-repair`. It does not create
the fix. It decides whether an already-committed repair branch can be applied.

Default repository-facing output is Chinese unless the operator asks otherwise.

## Preconditions

Proceed only when all are true:

- The operator explicitly asks to apply/merge/cherry-pick a self-repair closeout.
- Evidence points to an `autoresearch.repair.closeout.required` event or gives
  an equivalent tuple: `branch`, `worktree`, `source_commit`, verification
  evidence, and target branch.
- The repair branch name starts with `self-repair/`.
- The repair worktree exists, is clean, and `HEAD` equals the closeout
  `source_commit` when that field is available.
- The repair backlog/task is marked done or contains equivalent green
  verification evidence.
- There is no unrelated staged work in the target checkout.

Refuse and report blockers if any check fails. Do not "fix forward" inside this
skill; send the branch back to `zf-self-repair` or a human reviewer.

## Hard Boundaries

- Do not merge without explicit operator approval.
- Do not push. If the operator asks to push, use `zf-harness-commit-push` after
  this skill completes.
- Do not use `git add -A`, `git add .`, `git commit --amend`, `--no-verify`, or
  force-push.
- Do not delete a dirty worktree.
- Do not delete a branch until the applied commit is reachable from the target
  branch and verification is green.
- Do not write runtime truth files directly. If an event is needed, use `zf emit`
  through the configured state dir.

## Workflow

1. Inspect the closeout
   - Read the closeout event/report and capture `branch`, `worktree`,
     `source_commit`, `source_title`, `fingerprint`, `candidate_id`,
     verification evidence, and intended target branch.
   - Also read the contract fields the kernel now ships on
     `autoresearch.repair.closeout.required` (see
     `src/zf/runtime/self_repair_runner.py`): `risk_classification`,
     `verification_plan`, the restart contract (`restart_strategy`,
     `safe_boundary`, `state_snapshot_required`, `replay_required`), and the
     `continuation` block (`schema_version: self-repair.closeout-continuation.v1`
     — carries `resume_original_workflow`, `resume_strategy`, `blocked_until`,
     `action`). These drive steps 5 and 7; do not guess them.
   - Record what `risk_classification.risk` (low / medium / high) implies for
     verification breadth: `low` sets `controlled_apply_allowed`, medium/high
     set `human_approval_required` and widen the verification you must run.
   - If target branch is not explicit, default to the current branch only after
     confirming with `git branch --show-current`.

2. Verify git state before apply
   - In the target checkout:
     - `git status --short --branch`
     - `git diff --stat`
     - `git log -1 --oneline`
   - In the repair worktree:
     - `git -C <worktree> status --short --branch`
     - `git -C <worktree> rev-parse HEAD`
     - `git -C <worktree> branch --show-current`
     - `git -C <worktree> log --oneline --max-count=5`
   - Block if target has unrelated staged files or the repair worktree is dirty.

3. Review the repair diff
   - Compare target base to repair commit:
     - `git diff --stat <target>..<source_commit>`
     - `git diff --name-only <target>..<source_commit>`
     - targeted `git diff <target>..<source_commit> -- <paths>`
   - Confirm the diff stays within the repair scope and contains no runtime
     truth, credentials, generated state, or unrelated refactors.
   - Idempotency check (optional but preferred): confirm the repair commit is
     not already present as an equivalent patch before applying. Use patch-id,
     not hash equality (FIX-10 semantics — see `yoke/git-evidence` and
     `src/zf/runtime/candidates.py` `_task_commits`):
     `git rev-list --cherry-pick --right-only --count <target>...<source_commit>`.
     If the source side reports `0`, the fix already landed — skip apply and go
     to cleanup + report so you do not re-apply a duplicate series.

4. Apply
   - Prefer a normal merge when the branch is intended as a single repair
     branch:
     - `git merge --no-ff <branch>`
   - Use cherry-pick only when the closeout or operator asks for a specific
     commit:
     - `git cherry-pick <source_commit>`
   - On conflict: abort (`git merge --abort` or `git cherry-pick --abort`),
     leave the worktree and branch intact, and report blocked.

5. Verify after apply
   - First consume any Run Manager pre-validation result instead of inventing
     your own focused verification. The Run Manager can allowlist-execute the
     closeout's `verification_plan` in the repair worktree before merge
     (`execute_repair_verification_plan` in
     `src/zf/runtime/run_manager_repair_validation.py`, envelope
     `run-manager.repair-validation-result.v1`); a pass surfaces as a
     `run.manager.action.applied` event carrying `validation_result`, a red as
     `run.manager.action.failed`. If a green pre-validation exists for this
     `source_commit`, treat its steps as done and do not re-guess them.
   - Then re-run the closeout's structured `verification_plan` steps against the
     applied target (each step's `command` with `required: true`), rather than a
     hand-picked focused test set. Widen per `risk_classification` from step 1.
   - Run `uv run zf validate --cold-start`.
   - If the apply touches broad runtime/config/Web surfaces beyond the plan, run
     full `PYTEST_ADDOPTS=--no-cov uv run pytest -q`.
   - On red verification: revert or reset only with explicit operator
     direction; otherwise leave the target branch in a clear blocked state and
     report exact failing commands.

6. Clean up only after green verification
   - Confirm the applied commit is reachable:
     - `git merge-base --is-ancestor <source_commit> HEAD`
   - Remove the repair worktree:
     - `git worktree remove <worktree>`
   - Delete the local repair branch:
     - `git branch -d <branch>`
   - Prune stale worktree metadata:
     - `git worktree prune`
   - If branch deletion fails because Git says it is not fully merged, stop and
     report; do not use `git branch -D` without explicit approval.

7. Record the restart / continuation decision
   - The closeout is not done at cleanup: the contract sets
     `action: operator_merge_or_cherry_pick_then_restart_decision` and
     `continuation.blocked_until: verification_passed_and_apply_decision_recorded`.
     Once verification is green (step 5), you must make and record an apply
     decision or the run stays blocked.
   - Follow the `continuation` block from step 1:
     - `resume_strategy` / `restart_strategy` — for a `low` risk closeout this is
       `apply_for_next_run_without_runtime_restart` (no live restart; the fix is
       picked up on the next run). For medium/high it is
       `snapshot_replay_then_preserve_run_manager_control_plane_restart`.
     - `state_snapshot_required` / `replay_required` — if true, capture the
       state snapshot and plan the replay before restarting.
     - `safe_boundary` — only restart at the declared boundary
       (`terminal_or_next_run`, or `terminal_or_operator_approved_checkpoint`).
     - `resume_original_workflow` is true — after apply, the original workflow is
       meant to continue, not to dead-end at the closeout.
   - Record the decision as an artifact/event through the configured state dir
     (`zf emit`), not by editing truth files directly, so
     `blocked_until` is satisfied. Do not execute a control-plane restart
     yourself unless the operator explicitly authorizes it; recording the
     decision and the required boundary is this skill's obligation.

8. Closeout report
   - Report target branch, applied commit, merge/cherry-pick command,
     verification commands/results, the recorded restart/continuation decision,
     removed worktree, deleted branch, and any unrelated dirty files left
     untouched.

## Reuse

- Use `zf-harness-commit-push` discipline for any final commit/push request, but
  this skill itself does not push.
- Use `zf-backlog-batch-closeout` only if the apply itself creates or archives a
  task. Most self-repair branches already contain their backlog closeout.
- Delegate git reference/patch discipline to `yoke/git-evidence` — source_commit
  binding, the `git rev-list --cherry-pick` equivalence check (step 3), and the
  local-only push guard all live there.

## Output Shape

```text
已处理 self-repair apply closeout。

应用: <merge|cherry-pick|already-landed> <branch-or-commit> -> <target-branch>
验证: <pre-validation + verification_plan commands> -> pass
续跑决策: <resume_strategy>; 边界=<safe_boundary>; snapshot/replay=<yes|no>; 已记录
清理: worktree removed=<path>; branch deleted=<branch>
未处理/阻断: <none or reason>
未 push。
```
