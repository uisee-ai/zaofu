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
   - Run the closeout's focused verification first.
   - Run `uv run zf validate --cold-start`.
   - Run additional focused tests covering changed files.
   - If the apply touches broad runtime/config/Web surfaces, run full
     `PYTEST_ADDOPTS=--no-cov uv run pytest -q`.
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

7. Closeout report
   - Report target branch, applied commit, merge/cherry-pick command,
     verification commands/results, removed worktree, deleted branch, and any
     unrelated dirty files left untouched.

## Reuse

- Use `zf-harness-commit-push` discipline for any final commit/push request, but
  this skill itself does not push.
- Use `zf-backlog-batch-closeout` only if the apply itself creates or archives a
  task. Most self-repair branches already contain their backlog closeout.

## Output Shape

```text
已处理 self-repair apply closeout。

应用: <merge|cherry-pick> <branch-or-commit> -> <target-branch>
验证: <command> -> pass
清理: worktree removed=<path>; branch deleted=<branch>
未处理/阻断: <none or reason>
未 push。
```
