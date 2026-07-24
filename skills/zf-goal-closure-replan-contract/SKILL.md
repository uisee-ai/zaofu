---
name: zf-goal-closure-replan-contract
description: "Use on demand after verified gaps require an incremental task-map amendment; preserves completed work and delegates event/schema mechanics to the gap producer and runtime."
stages: [verify, discovery, replan]
tags: [contract, goal-closure, replan]
dependencies: [zf-verify-gap-producer-contract]
auto_inject: false
load_on_demand: true
---

# ZaoFu Goal Closure Replan Contract

Use this reference only after admitted verification/discovery evidence proves
that the current goal remains open and new implementation work is required.
Normal pass/fail reporting does not need it.

## Inputs

Start from the immutable verifier/discovery result and its evidence refs. The
gap must identify:

- the still-open goal/acceptance/parity claim;
- the failed or missing behavior and reproducible evidence;
- the affected task, assembly, candidate, or goal scope;
- why existing work cannot close the gap without a new delta.

Do not re-derive a gap from chat or a one-line reason when a typed result exists.

## Incremental Planning Method

1. Preserve accepted completed tasks and their evidence.
2. Use `zf-gap-task-synth` to create the smallest additional task set that can
   close the verified gap.
3. Give every task explicit ownership, allowed paths, source anchors,
   acceptance criteria, verification owner/tier, and dependencies.
4. Reuse stable task identity where the contract is unchanged; mint a revised
   contract/task only when the required behavior or ownership changed.
5. Record what changed from the prior plan and which old attempt/task is
   superseded.
6. Declare the semantic `required_delta`: changed AC/evidence fields,
   ownership paths, whether a Task Map revision is required, and the expected
   postcondition. Preserve unchanged logical ports by explicit inheritance
   from the current Package rather than rediscovering files.
7. Persist the gap plan at the path supplied by the briefing, then use
   `zf-verify-gap-producer-contract` for the exact canonical submission.

The current runtime output profile is authoritative for artifact/event shape.
Do not hand-build the amended full task map, emit task-map truth directly, or
start a new run merely because a gap exists.

## Convergence

If runtime suppresses an equivalent pending plan, wait for its decision or
change the semantic task set. If re-verification is suppressed because the
target has no new delta, produce a new implementation commit or escalate the
real blocker. Re-emitting an identical plan is not progress.

## Boundary

Planner/Synth decides task meaning, slicing, ownership, and acceptance.
Runtime owns plan fingerprinting, task-map amendment/admission, generation,
attempt/cap, stale/replay, Package currentness, Recovery Proposal admission,
affinity dispatch, and truth transitions. Thin Judge
does not load this Skill; a rejected Judge result is routed to the appropriate
planner/synth owner first.
