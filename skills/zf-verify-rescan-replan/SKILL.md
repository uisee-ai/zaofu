---
name: zf-verify-rescan-replan
description: "Use in discovery, module-parity, verify-bridge, or replan roles when completed work must be rescanned against the original goal and remaining gaps converted into bounded incremental work; not for ordinary task Verify or Thin Judge."
stages: [verify, discovery, replan]
tags: [verification, parity, rescan, replan]
auto_inject: false
load_on_demand: true
---

# ZaoFu Verify Rescan Replan

## When To Trigger

Trigger this skill when global discovery, parity, or bridge evidence shows any
of these:

- required P0/P1 behavior is missing or only partially implemented;
- implementation passed local tests but does not satisfy the original issue,
  PRD, or refactor parity goal;
- the produced UI/API/CLI is a stub or talks to the wrong backend;
- source comparison finds uncovered original capability, tool, provider,
  memory, context, gateway, or dashboard behavior.

Do not activate it for an ordinary task-slice Verify verdict or the final Thin
Judge. Task Verify returns its typed result; the semantic router dispatches a
separate discovery/replan owner when the admitted result exposes a wider gap.

## Bind The Audit Object First

Before comparing anything, confirm which tree you are auditing:

- reader children are dispatched with a pinned `target_commit` written into
  the child payload of `fanout.child.dispatched` (FIX-9); an unresolved or
  unpinnable target is rejected with `fanout.child.workdir_mismatch` and the
  child is never dispatched;
- confirm the rescan workdir `HEAD` equals the child payload `target_commit`,
  and record that commit in the rescan report;
- rule out the baseline-tree failure mode first: the bizsim r4 root cause was
  five judge audits of a baseline tree — a rescan of the wrong commit
  produces confident but worthless gap findings;
- `yoke/git-evidence` owns the pin-commit / `source_commit` binding
  discipline this step pairs with.

## Required Rescan Output

The rescan must write a durable report with:

- `goal_id`, `goal_kind`, and `gap_category`;
- the audited `target_commit`, plus source artifacts compared, including
  original goal and produced code;
- inventory artifacts compared, when the workflow exposes `inventory_refs` or
  `source_inventory_ref`;
- capability/test rows with priority and status;
- `open_p0_p1_gap_count`;
- candidate `gap_tasks` with source refs and verification commands;
- `runtime_evidence_refs` when runtime behavior is relevant.

If `open_p0_p1_gap_count` is greater than zero, do not emit final pass. Produce
a `goal-gap-plan.v1` artifact and route it through task-map amend
(`zf-verify-gap-producer-contract` covers the report/event side of the same
surface; `zf-gap-task-synth` owns gap-task shaping detail).

If you re-produce a gap plan with the same stage + pdd + task set while an
earlier one is still pending, the kernel emits `plan.minting.suppressed` with
`duplicate_of` pointing at the pending plan — the plan is deduped, not lost.
Do not mint reworded variants to force it through.

## Gate Behavior

Use artifact/matrix gate config to check:

- the rescan report exists;
- required inventory refs are mapped by acceptance/test/gap matrix rows when
  `inventory_coverage` is configured;
- required report fields are non-empty — when the project configures
  `workflow.dag.event_schemas`, this is mechanical, not aspirational: the
  `non_empty` schema tier rejects empty strings/lists, e.g.
  `requirement_coverage_matrix` on `verify.child.completed` must carry at
  least one row, and reject verdicts must fill `gap_findings`. The briefing's
  report skeleton is auto-seeded from the same schema, so its placeholder
  fields are the contract, not suggestions. `yoke/verify-review` covers the
  per-child report contract beneath this skill's stage-level rescan;
- open P0/P1 gaps are zero before final judge pass;
- gap task-map artifacts are valid before re-entering implementation.

The gate is evidence-based. It should block final closure for missing behavior,
but it should not restart unrelated finished tasks.

Do not mutate `events.jsonl`, `kanban.json`, `feature_list.json`, `progress.md`,
or `memory/` directly. Produce artifacts and emit the configured event intent;
the kernel owns state transitions and projections.

## Re-entry Rule

Re-entry must be bounded, and looping is not a strategy:

- dispatch only generated gap tasks;
- preserve lane affinity and parent task context;
- include `replan_history_ref` and affected task ids in worker briefings;
- when the stage sets `retrigger_requires_delta: true` (a zf.yaml stage
  field, default false), the kernel enforces convergence on re-verify:
  - re-verifying requires a new commit delta — re-triggering the same
    commit that already failed is refused with `fanout.retrigger.suppressed`
    (`reason=no_delta_since_failure`); wait for gap tasks to land new
    commits before requesting another rescan;
  - after 3 consecutive judge failures on the stage, expect `human.escalate`
    (`reason=judge_nonconvergence`) with a failure-chain summary, and a
    Tier-2 `diagnosis.requested` hand-off (`yoke/diagnosis`);
- once escalation fires, stop looping: either produce the gap plan or hand
  off to the diagnostician — never schedule another rescan of the same
  audit object.
