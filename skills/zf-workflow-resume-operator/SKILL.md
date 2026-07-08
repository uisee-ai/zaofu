---
name: zf-workflow-resume-operator
description: "ZaoFu workflow resume operator playbook for stuck autoresearch, supervisor, orchestrator, fanout, PRD, issue, refactor, or build runs. Use when asked to inspect current workflow status, handle workflow_resume checkpoints, resume pending tasks, distinguish per-task pending from batch checkpoints, or decide whether a blocked ZaoFu run needs manual recovery."
---

# ZaoFu Workflow Resume Operator

## Overview

Use this skill to inspect and resume ZaoFu workflows without corrupting runtime truth. The skill guides operator behavior; deterministic state transitions still belong to ZaoFu CLI/runtime helpers.

## Invariants

- Treat `zf.yaml` as the only control-plane config.
- Resolve and pass the configured `project.state_dir`; do not assume `.zf`.
- Never edit `events.jsonl`, `kanban.json`, `session.yaml`, `feature_list.json`, or `role_sessions.yaml` directly.
- Use `zf recover workflow` and kernel helpers for mutations.
- Do not synthesize terminal events such as `*.passed`, `*.approved`, or `task.done`.
- Do not claim a workflow is complete until both resume projection and downstream gates support that claim.

## Read-Only First

Start with a dry-run from the target project root. Use a project-relative launcher so the command is portable across checkouts — pick whichever your project provides:

```bash
# repo-root uv (recommended when the project ships a uv env)
ZF_STATE_DIR=<state-dir> uv run zf recover workflow --dry-run --json

# or an editable venv checkout
ZF_STATE_DIR=<state-dir> .venv/bin/zf recover workflow --dry-run --json
```

Inspect:

- `summary.pending`: per-task checkpoints that need action.
- `summary.batch_pending`: batch/fanout checkpoints that may be current or historical.
- `checkpoints[].safe_resume_action`: task-level action such as `needs_stage_dispatch`, `needs_rework_dispatch`, `needs_gate_dispatch`, `needs_terminal_closeout`, `blocked_external_gate`, or `no_action`.
- `batch_checkpoints[].safe_resume_action`: batch-level action such as `repair_failed_children` or `reemit_candidate_ready`.
- `worker_registry.stale`: stale worker evidence.

## Decision Rules

`safe_resume_action` is the enum the kernel writes on each checkpoint. Map the value to the guidance below. The "expected downstream" column is what a successful `--resume-pending` must produce (see `src/zf/runtime/run_manager_router.py:818-830`); if you do not see those events after applying, treat the resume as not landed.

| `safe_resume_action` | level | expected downstream after apply | operator guidance |
|---|---|---|---|
| `no_action` | task | — | Do not re-apply; already settled. |
| `needs_stage_dispatch` | task | `task.dispatched` + `workflow.resume.applied` | Apply single checkpoint by id, then dry-run again. |
| `needs_rework_dispatch` | task | `task.rework.requested` + `workflow.resume.applied` | Apply single checkpoint by id, then dry-run again. |
| `needs_gate_dispatch` | task | `stage.transition.stalled` + `workflow.resume.applied` | Apply single checkpoint; confirm the stalled marker landed. |
| `needs_terminal_closeout` | task | `stage.transition.stalled` + `workflow.resume.applied` (same group as `needs_gate_dispatch`, router:824-825) | Terminal-stage closeout. Before manual apply, check the Tier-2 diagnosis channel (below) — a blocked terminal often means the kernel already opened a diagnosis loop. |
| `blocked_external_gate` | task | `stage.transition.stalled` + `workflow.resume.applied` | Do not guess approval. Check whether a route or gate event is genuinely missing, and check the diagnosis channel first. |
| `repair_failed_children` | batch | `task_map.ready` + `workflow.resume.applied` | Triage superseded first (see Batch Checkpoint Triage). Only resume `current` batches. |
| `reemit_candidate_ready` | batch | `candidate.ready` + `workflow.resume.applied` (router:826-827; classifier in `workflow_resume.py:630`) | The batch has a completed candidate ref+head but no `candidate.ready` emission. Re-emit only if no later equivalent patch already landed the candidate (patch-id check below). |

Additional rules that do not fit a single row:

- If `pending=0` and `batch_pending=0`, do not resume anything.
- If `pending=0` but `batch_pending>0`, say the active task path is clear but historical or batch checkpoints remain. Do not call the whole run done.
- If the checkpoint source was rejected earlier, remember that `workflow.resume.rejected` is not success. Fix the rejection cause and retry the same checkpoint.
- `SAFE_BATCH_ACTIONS` in the kernel is exactly `{repair_failed_children, reemit_candidate_ready}` (`src/zf/runtime/run_manager_router.py:11`). A batch checkpoint outside that set is not auto-safe; inspect before acting.

## Tier-2 Diagnosis First

Before you manually recover any `blocked_external_gate` / `needs_terminal_closeout` checkpoint, or any run that looks stuck because a judge is not converging or a rework quota is exhausted, look at the Tier-2 diagnosis channel. The kernel opens it automatically; a manual resume that races it will duplicate work the kernel already suppressed.

How it works (`src/zf/runtime/diagnosis.py`, wired in `src/zf/runtime/orchestrator.py:1661-1693`):

- On a non-convergence / rework-exhausted escalation, the orchestrator sweep mints `diagnosis.requested` keyed by the **stall fingerprint** — one diagnosis per fingerprint, so a recurring failure does not loop the diagnostician.
- A configured diagnostician stage (`trigger: diagnosis.requested`) attaches to the scene (logs / event window / worktree) and emits a structured `diagnosis.completed`. The report's `next_action` is one of `route_to_lane`, `fix_target`, or `needs_owner` (`NEXT_ACTIONS` in `diagnosis.py`).
- A `needs_owner` conclusion auto-escalates to the owner via `human.escalate` (one escalation per conclusion). A `route_to_lane` conclusion flows back through the `candidate_rework` feedback pipeline to replan.
- Boundary (v1): diagnosis is **propose-only** — the kernel does not execute the report's `proposed_commands`.

Operator implication:

- If you see `diagnosis.requested` without a matching `diagnosis.completed`, the diagnostician is still working — wait, do not hand-resume.
- If `diagnosis.completed` says `needs_owner` and a `human.escalate` already exists, the decision is genuinely yours; that is a real owner gate, not a resume target.
- Do not manually re-drive a retry path the kernel has already suppressed by fingerprint. Repeating it just re-mints noise.

## Apply Safely

Apply only the selected checkpoint (project-relative launcher, same choice as the dry-run):

```bash
ZF_STATE_DIR=<state-dir> uv run zf recover workflow --resume-pending --checkpoint-id <checkpoint-id> --json
# or: ZF_STATE_DIR=<state-dir> .venv/bin/zf recover workflow --resume-pending --checkpoint-id <checkpoint-id> --json
```

After applying, verify:

- `workflow.resume.applied` exists for the checkpoint.
- The action's expected downstream event(s) from the Decision Rules table exist (for example `task.dispatched`, `candidate.ready`, or `stage.transition.stalled`).
- `workflow.resume.rejected` does not remain the latest outcome for that idempotency key.
- A follow-up dry-run shows the checkpoint as `no_action` or removes it from pending.

## Batch Checkpoint Triage

For each batch checkpoint, compare the checkpoint's `pdd_id`, `trace_id`, `fanout_id`, `failed_children`, and candidate against later events.

Classify it as (these labels are skill-owned triage vocabulary, not a kernel enum):

- `current`: no later scoped rework, candidate, review, verification, or gate has taken over.
- `superseded`: a later event has already continued or replaced the failed batch.
- `historical-noise`: the projection still shows it but it should not drive action.
- `needs-runtime-fix`: the projection cannot distinguish old and current evidence.

**Superseded judgement is patch-id equivalence, not commit-hash equality.** Candidate integration is idempotent by patch-id: the kernel selects new commits with `git rev-list --cherry-pick --right-only` (`src/zf/runtime/candidates.py:1373-1382`), so an equivalent patch that already landed on the base side carries a *different* commit hash. Comparing the checkpoint's candidate commits by hash against later events will mis-flag a batch as `current` when an equivalent patch has already been cherry-picked in. Use patch-id equivalence (`git rev-list --cherry-pick` semantics): if the failed batch's changes already exist on the current head as an equivalent patch, treat it as `superseded`.

**Expected-suppression evidence.** Two kernel events are deliberate no-op decisions as of the 2026-07-06 kernel, not stalls to resume — classify them as "expected suppression", never as work to re-drive:

- `fanout.retrigger.suppressed` with reason `no_delta_since_failure` (`src/zf/runtime/orchestrator_fanout.py:6492-6499`): the fanout was not retriggered because there is no new delta since the last failure. This is the intended guard, not a dropped retry.
- `plan.minting.suppressed` with reason `pending_plan_same_fingerprint` (`src/zf/runtime/orchestrator_fanout.py:3086-3093`): a new plan was not minted because a pending plan with the same fingerprint already exists.

Only resume `current` checkpoints. For `superseded` or `historical-noise`, record the evidence and update runtime projection logic if the noise is repeatable.

## Status Report Shape

When the user asks for status, report:

- Target project and state dir.
- `pending`, `batch_pending`, and stale worker count.
- Current actionable checkpoint ids, if any.
- Whether remaining checkpoints are task-level, batch-level, historical, or current.
- Any open Tier-2 diagnosis (`diagnosis.requested` without `diagnosis.completed`, or a `diagnosis.completed`/`human.escalate` owner gate).
- Any `fanout.retrigger.suppressed` / `plan.minting.suppressed` events classified as expected suppression, so they are not misread as stalls.
- Last applied command and event ids.
- What must happen before calling the run complete.

## How To Test

Ask an agent to inspect a ZaoFu run where `pending=0` and `batch_pending>0`. The expected output should clearly say the active task path is clear, avoid applying historical checkpoints blindly, classify any suppression events as expected, and list the evidence needed before closeout.
