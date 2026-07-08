---
name: zf-self-repair
description: "ZaoFu AUTHORIZED self-repair playbook for Claude or Codex. Use ONLY when the operator has authorized auto-repair (ZF_AUTORESEARCH_AUTO_REPAIR=authorized) and an autoresearch bug_candidate / supervisor attention names a harness self-bug to fix. Turns a detected harness failure into a tracked loop: write a backlog, fix in an isolated worktree, verify with the candidate's success criteria, then mark the backlog done with the commit hash — or, on red verify or over the attempt cap, leave it un-merged and escalate. Never merges an unverified change; the backlog + commit trail is the audit record the operator follows."
---

# ZaoFu Self-Repair (authorized, tracked)

## Objective

Close the autonomous-recovery gap surfaced by the cj-min R8 stall: the harness
DETECTS its own failures well (autoresearch bug_candidate, supervisor
attention, worker.stuck, human.escalate) but, in unattended mode, the
escalation goes to a human who isn't there and the run stalls forever.

When the operator has **authorized** auto-repair, this skill runs the loop:

```
detect (bug_candidate) → write backlog → fix in isolated worktree → verify
  → green: mark backlog done + commit hash → kernel mints
           autoresearch.repair.closeout.required → operator applies via
           zf-self-repair-apply-closeout (human apply gate)
  → red / over cap:  leave un-merged, mark backlog blocked, human.escalate
```

The safety is NOT "trust the fix". It is **authorization + auditability +
isolation + a hard verify gate + a bounded cap**: every auto-repair is opt-in,
leaves a backlog created→done trail with a commit hash, is verified in
isolation before any merge, and is one `git revert` away from undo. This turns
an irreversible "harness silently rewrites its own kernel" into an auditable,
reversible automatic PR.

Default repository-facing output is Chinese unless the operator asks otherwise.

## Pre-conditions (refuse if any fail)

1. **Authorization on (dual gate).** The kernel opens auto-repair dispatch when
   EITHER gate is set (`repair_authorization.auto_repair_consumer_enabled`,
   `src/zf/runtime/repair_authorization.py:81-98`):
   - **env override**: `ZF_AUTORESEARCH_AUTO_REPAIR=authorized` — the deliberate
     override for legacy/manual runs that have not encoded policy in config; or
   - **control-plane authorization**: `zf.yaml`
     `autoresearch.trigger_policy.repair_mode: bounded_repair` — the config-side
     grant to consume budget on already-gated repair dispatches (default
     `proposal_only` = do nothing but propose).
   The kernel gates dispatch, but re-assert here: if NEITHER gate is set → do
   nothing, say so. Default off (`proposal_only`, env unset).
2. **A concrete self-bug to fix.** An autoresearch `bug_candidate` (with
   `repair_task_payload`: `hypothesis`, `contract.scope`, `success_criteria`)
   or a supervisor attention naming a harness self-bug. No vague "improve X".
3. **Scope is the harness itself.** `src/zf/**` + `tests/**` only. Never touch
   another project, runtime truth (`events.jsonl`, `kanban.json`,
   `session.yaml`, `role_sessions.yaml`, `feature_list.json`), `zf.yaml` as a
   second control plane, or any credential (`llm_env`/`.env`).
4. **Under the attempt cap.** If a self-repair backlog for the same failure
   `fingerprint` already exists at `done` (or N attempts reached) → do NOT retry;
   `human.escalate` instead. Default N=2.

## The loop

### 1. Intake the bug_candidate (consume diagnosis products first)
Read the candidate's `hypothesis`, `contract.scope`, `success_criteria`
(usually `event_exists` + a focused `pytest` target), evidence event ids, and
`fingerprint`. **Before repro-from-scratch, check for a Tier-2 diagnosis
product.** Tier-2 diagnosis is productized (`diagnosis.requested` /
`diagnosis.completed`, `src/zf/core/events/known_types.py:381`;
`src/zf/runtime/diagnosis.py`, doc131 §5): a dispatch/stall-class candidate may
already carry a `diagnosis.completed` conclusion (root-cause layer, event
window, worktree evidence). Prefer that structured conclusion over re-deriving.
For worker-frozen / dispatch-stall triage method see `yoke/debugging-triage`;
for Tier-2 stall-diagnosis method see `yoke/diagnosis`. Then reproduce the
failure signature against current HEAD (the backlog Validate-First rule); if it
no longer reproduces, mark "verified resolved", note the closing commit, stop.

### 2. Write the backlog (this is the audit anchor — skill, not kernel)
Create `backlogs/YYYY-MM-DD-HHMM-self-repair-<short-fingerprint>.md` (UTC
`date -u +%Y-%m-%d-%H%M`) following `.claude/rules/backlogs.md`:
- `> 状态: active` (it is being worked now)
- the bug: fingerprint, signal, evidence event ids
- diagnosis: the candidate hypothesis + your repro
- fix plan: the surgical change, scoped to the candidate's `scope`
- acceptance in `step → verify:` form, using the candidate's `success_criteria`
Reuse the backlog conventions from `[[zf-harness-self-improve]]`. This file is
what the operator tracks — it must exist BEFORE you change code.

### 3. Fix in the isolated worktree (surgical)
Make the minimal change inside the candidate's `scope`. Obey
`.claude/rules/code.md` changeset-simplicity: `git diff --stat` ≈ the root-cause
size, not 4×. New behavior in an oversized file → new sibling module. Add/adjust
the test that codifies the fix (TDD: red → green).

### 4. Verify (HARD gate — never skip, never merge on red)
Run the candidate's `success_criteria` (the focused `pytest` target) + any
relevant regression. It MUST be green. If RED:
- do NOT commit-to-live / merge.
- mark the backlog `> 状态: blocked` with the failing output.
- emit / request `human.escalate` (reuse the bounded-cap path).
- stop. A red self-repair is a refusal, not a retry-forever.

### 5. Close + commit (only on green) — COMMIT-ONLY, never push/merge
- Commit via the **[[zf-harness-commit-push]]** discipline (inspect → classify →
  secret-scan → explicit `git add -- <path>` → conventional prefix; never
  `git add -A` / `--amend` / `--no-verify` / force) — but **run its COMMIT step
  only, NOT its push step (step 6).** A self-repair commits to its isolated
  worktree branch and stops there.
- Message names the fingerprint + candidate id, ends with the Co-Authored-By line.
- Mark the backlog `> 状态: done (<commit> "<title>")` and `git mv` it to
  `tasks/` per `[[zf-backlog-batch-closeout]]`.
- **Do NOT merge to the live `src/zf`, and do NOT push to any remote.** The
  verified fix stays on the isolated branch as the deliverable. The authorization
  gates whether the loop RUNS, NOT whether the agent merges to live or pushes.
  The fix is one `git revert` away from undo precisely because it is not
  auto-merged.
- **The handoff is productized — do not stop at "backlogs/ + git, apply by
  hand".** After the green commit lands on the isolated worktree branch, the
  kernel runner scans the worktree and mints
  `autoresearch.repair.closeout.required`
  (`src/zf/runtime/self_repair_runner.py:34,233-269`), carrying
  `risk_classification`, `verification_plan`, and a `continuation` block
  (restart/resume policy). The Feishu projection maps this event to an
  **approval** card (`src/zf/integrations/feishu/projection.py:97`). That is the
  human apply gate. The operator then runs **`[[zf-self-repair-apply-closeout]]`**
  to review the branch + backlog trail, merge / cherry-pick, and tear down the
  isolated worktree/branch. This skill (`zf-self-repair`) NEVER applies the
  closeout itself.

## Reuse (do not re-derive)
- `[[zf-harness-self-improve]]` — backlog authoring conventions (step 2).
- `[[zf-backlog-batch-closeout]]` — implement → mark done → archive (steps 3-5).
- `[[zf-harness-commit-push]]` — the COMMIT discipline for step 5 (secret-scan,
  explicit paths, conventional prefix, no force). Self-repair uses its commit
  step ONLY — never its push step (the isolated branch is the deliverable).
- `yoke/debugging-triage` — worker-frozen / dispatch-stall triage method for a
  dispatch/stall-class candidate at step 1 (replaces the retired
  `zf-dispatch-stall-diagnose`).
- `yoke/diagnosis` — Tier-2 stall-diagnosis method; pair with the
  `diagnosis.completed` product consumed at step 1.
- `[[zf-self-repair-apply-closeout]]` — the OPERATOR-side apply gate that
  consumes the `autoresearch.repair.closeout.required` event minted after the
  green commit (step 5). Never run it from inside this skill.

## On false-positive candidates
A `bug_candidate` may name the WRONG layer: the real bug can be the detector /
tooling that PRODUCED the false candidate, not the layer its hypothesis blames.
At step 1, reproduce the fingerprint's signal against the real run's event
window; if the signal does not reproduce as described, the fix may be to the
detector itself (make it not false-positive) rather than "mark resolved + stop".
See memory `[[self_repair_candidate_can_be_false_positive]]`.

## Output / evidence (what the operator tracks)
A one-line result: `<fingerprint> → <backlog path> → <done|blocked> → <commit|escalated>`.
The durable trail is the backlog (created→done/blocked) + the commit + the
events — auditable and revertable. Never report a fix "done" without a green
verify and a commit hash.

## Hard NOs
- No merge / "done" without a green verify.
- No retry past the cap — escalate.
- No scope beyond `src/zf/**` + `tests/**`.
- No writing runtime truth, no second control plane, no credentials.
- No merge to the live `src/zf` and no push to any remote — commit-only, on the
  isolated branch; the operator applies (the human apply gate).
- No backlog-skip: the backlog audit trail is the safety mechanism, not optional.
