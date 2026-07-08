---
name: zf-harness-lane-goal-continuation
description: "Use for ZaoFu fanout writer children, affinity lanes, product-delivery waves, or provider goal-mode runs that must complete a scoped lane without shrinking the feature objective or claiming global done."
---

# ZaoFu Lane Goal Continuation

This skill turns provider goal continuation into a ZaoFu lane-level worker
contract. It shapes the worker prompt and terminal claim; it does not replace
task-map admission, affinity scheduling, candidate integration, or deterministic
gates.

## When To Use

Use when a worker receives a fanout child, especially:

- `topology: fanout_writer_scoped`
- `fanout.assignment.strategy: affinity_stage_slots`
- `product_delivery.wave.ready`
- a task-map slice with `affinity_tag`, `lane_id`, `stage_slot`,
  `allowed_paths`, `exclusive_files`, or `blocked_by`
- provider-native goal mode continues one scoped task across turns

## Core Rule

The lane goal is a scoped objective, not product truth.

- Keep the full feature / wave objective visible.
- Complete only the assigned lane task.
- Do not redefine success around an easier subset.
- Do not claim feature, candidate, release, or product done.
- Emit only the configured role result event with evidence; ZaoFu kernel gates
  decide whether the lane, fanout, candidate, and task are accepted.

## Lane Goal Envelope

Every lane briefing should carry this shape, either rendered directly or through
equivalent fields:

```text
## Lane Goal Continuation

Feature objective:
  <full feature / PDD / wave objective>

Lane binding:
  fanout_id: <fanout_id>
  child_id: <child_id>
  run_id: <run_id>
  task_id: <task_id>
  lane_id: <lane_id, if any>
  stage_slot: <impl|review|verify, if any>
  affinity_tag: <module/domain tag>
  task_map_ref: <accepted task-map ref>
  source_index_ref: <source index ref, if any>

Scope:
  allowed_paths: [...]
  exclusive_files: [...]
  shared_files: [...]
  blocked_by: [...]
  verification: <command or evidence contract>

Completion evidence:
  writer lane: source_commit == worker branch HEAD
  reader lane (review|verify): the audit object is the pinned target_commit
    written into the dispatched child payload — not "whatever HEAD is now".
    Run `git rev-parse HEAD` and compare it to the briefing/payload
    target_commit; on mismatch report the workdir mismatch instead of
    auditing the wrong tree.
  files_touched stays inside allowed/exclusive scope
  required verification commands or artifacts are present
  evidence_refs and artifact_refs are replayable; echo the audited commit
    (e.g. target-commit:<sha>) so the claim binds to the pinned audit object
```

## Continuation Behavior

Before ending a turn, audit the lane:

1. Derive concrete lane requirements from `task_map_ref`, task capsule,
   briefing, acceptance, verification, and referenced artifacts.
2. Inspect current state: workdir, git diff/status, produced files, command
   outputs, and event/task refs.
3. If any lane requirement lacks evidence, keep working or emit a blocking
   event; do not emit the success event.
4. If the same true blocker repeats across repeated continuation turns, emit
   the configured blocked/suspend event with concrete blocker evidence — do
   not keep re-emitting a claim on the same failing attempt. The kernel
   enforces the matching convergence contract from its side: re-triggering a
   reader stage on an audit-object `target_commit` that already carries a
   rejection for that stage, with no delta, emits
   `fanout.retrigger.suppressed` (no new review runs); ≥3 consecutive
   rejections without convergence escalate `human.escalate` with
   `reason: judge_nonconvergence` and a rejection-chain summary
   (`src/zf/runtime/orchestrator_fanout.py`). Surface an unresolved blocker as
   a real blocked/suspend event so the run routes, rather than bounce a claim
   the delta gate will suppress.
5. If lane evidence is complete, emit the role success event only for this lane.

Provider-native goal status is advisory. If a provider goal says complete but
the ZaoFu lane evidence is missing, treat it as incomplete and continue.

## Rework Continuation (lane micro-loop)

When `goal.micro_loop` is enabled, a reader-stage rejection
(`review.rejected` / `verify.failed` / `test.failed`,
`src/zf/runtime/lane_micro_loop.py`) does **not** automatically spin a new
fanout generation. The kernel injects the findings back into the **same live
lane session** as a continuation briefing and records
`task.rework.continuation_injected`
(`src/zf/runtime/lane_micro_loop.py:23`, `src/zf/runtime/candidate_rework.py:241`)
— same pane, same `task_id`, same identity, **no re-dispatch and no new child
generation**.

If you receive a `REWORK CONTINUATION` briefing:

- Treat it as a continuation of the **same** task, not a new task or a fresh
  claim. Resolve every injected finding using the **current workdir**, re-run
  the focused verification, and re-emit the **original** terminal event —
  writer: `dev.build.done`, or the controller/profile-workflow form
  `impl.child.completed`; reader: the same `review.child.completed` /
  `verify.child.completed` shape. Do not mint a new child identity or claim a
  new lane.
- **Actually change the approach.** Injection is idempotent per rejection
  (`rework_of`), and a same-`fingerprint` re-rejection is treated as **stalled**
  — the kernel then falls back to full re-dispatch. Re-sending the same failing
  fix wastes the one continuation you get; use it to converge.

A terminal claim re-emitted after a generation flip can still carry the prior
identity. The kernel adopts such a completion into the current generation and
records `fanout.child.completion_adopted`
(`src/zf/runtime/orchestrator_fanout.py:4557`) rather than dropping it as stale
— so re-emitting the original terminal event from the same session stays valid
across the flip. This continuation loop is lane/fanout-child scoped; the full
long-horizon goal lifecycle (`run.goal.*`) is owned by
`zf-harness-goal-loop-contract` and not restated here.

## Terminal Claim Payload

The fanout terminal-claim field roster — `fanout_id`, `stage_id`, `child_id`,
`run_id`, `task_id`, `lane_id`, `stage_slot`, `affinity_tag`, `task_map_ref`,
`source_index_ref`, `workdir`, `source_branch`, `source_commit`,
`files_touched`, plus `commands` / `artifact_refs` / `evidence_refs` — is owned
by `zf-harness-done-contract`. Build the claim from there; do not restate the
roster. This skill adds the lane-continuation constraints below on top of that
shape. It applies to `stage_slot` = `impl` (writer) **and** `review` / `verify`
(reader), but the two lanes emit different report shapes — do not send the
writer shape on a reader child.

### Writer lane (`stage_slot: impl`)

- Keep the claim lane-scoped: never assert feature / candidate / release /
  product done — kernel gates decide those.
- If a field is unavailable, report why. Do not silently drop the identity
  fields `fanout_id`, `child_id`, `run_id`, `task_id`, `task_map_ref`, or the
  audit-object commit (`source_commit` for the worker branch).

### Reader lane (`stage_slot: review` | `verify`)

A reader child's terminal report (`review.child.completed` /
`verify.child.completed`) is more than status + summary. When the workflow
event schema marks these report fields under the `non_empty` tier
(`src/zf/core/verification/event_schema.py`), the kernel injects a
schema-education placeholder into the reader briefing
(`_SCHEMA_EDU_PLACEHOLDERS`, `src/zf/runtime/orchestrator_fanout.py`) and an
empty matrix is rejected — sending the writer-only shape gets the report
bounced. The report must carry:

- `requirement_coverage_matrix` — non-empty, at least one row; each
  `requirement_id` bound to a task-contract acceptance clause, with
  `source_ref`, `status`, `evidence_refs`, `gap_summary`, and `replan_action`.
- `gap_findings` — concrete gaps (an empty list asserts none remain).
- `replan_recommendation` — `continue` or the replan directive.
- `evidence_refs` — replayable primary-evidence paths, echoing the audited
  `target-commit:<sha>`.

```json
{
  "fanout_id": "<fanout_id>",
  "child_id": "<child_id>",
  "status": "<pass|reject>",
  "summary": "<one-sentence>",
  "git_refs": ["target-commit:<sha>"],
  "requirement_coverage_matrix": [
    {
      "requirement_id": "<acceptance-id-from-task-contract>",
      "source_ref": "<prd-or-contract-path#section>",
      "status": "covered",
      "evidence_refs": ["<test-or-log-path>"],
      "gap_summary": "",
      "replan_action": "continue"
    }
  ],
  "gap_findings": [],
  "replan_recommendation": "continue",
  "evidence_refs": ["<primary-evidence-path>"]
}
```

The coverage-matrix taxonomy and the runner-vs-product failure classification
are owned by `yoke/verify-review`; this skill only requires you emit the shape
the reader schema demands.

## Boundaries

- Skills may generate the lane goal text and report shape.
- `zf.yaml` declares topology, roles, lane profiles, and stage slots.
- The kernel assigns slots, queues overflow children, releases lanes, validates
  current task-map admission, and performs candidate integration.
- Gates decide completion from events and evidence, not from narrative claims.

## Related Skills

- `zf-harness-goal-loop-contract` — feature / long-horizon goal condition.
- `zf-harness-backlog-synthesis` — task-map and lane metadata synthesis.
- `zf-harness-done-contract` — authoritative fanout terminal-claim roster +
  done evidence shape (this skill references it, does not restate it).
- `yoke/verify-review` — reader coverage-matrix taxonomy and runner-vs-product
  report contract.
- `zf-harness-state-sync` — event/truth boundary.
- `zf-yoke-dev-worker-role-context` — worker scope and evidence discipline.
