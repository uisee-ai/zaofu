---
name: zf-verify-gap-producer-contract
description: "Use on demand when admitted Verify/discovery evidence must become a canonical product/parity gap handoff; runtime owns task-map amendment and loop idempotency."
stages: [verify, discovery, replan]
tags: [contract, gap-plan, evidence]
dependencies: [zf-gap-task-synth]
auto_inject: false
load_on_demand: true
---

# ZaoFu Verify Gap Producer Contract

Use this reference only when independent verification or discovery has found a
blocking product, regression, acceptance, or parity gap. It translates a
semantic finding into the canonical gap channel; it does not decide runtime
state or rebuild the plan itself.

## Source Requirements

- Start from an admitted verification/discovery result bound to the current
  contract and target.
- Cite the failed/open acceptance or goal claim and reproducible evidence.
- Keep verifier/environment execution failures out of product gap work.
- Only must-fix findings become gap tasks; suggestions remain report findings.
- Reuse the typed result and immutable feedback refs instead of copying
  transcript prose.

## Producer Method

1. Decide whether the finding is task-local, candidate/assembly-wide, or a
   goal-level gap and recommend the corresponding semantic owner.
2. For task-local rejection, return the complete finding/evidence to the
   implementation owner; do not mint a new plan by default.
3. When new or revised task-map work is required, load
   `zf-goal-closure-replan-contract` and `zf-gap-task-synth` to produce bounded
   gap tasks that preserve completed work.
4. Persist the gap report/plan at the exact path named by the briefing.
5. Submit once through the current runtime-provided gap output profile or
   bridge command. Do not infer payload fields from this Skill or emit task-map
   truth directly.

## No-Gap Closure

When no blocking gap remains, report the sources checked, mandatory claim
coverage, required real-E2E/release evidence, and explicit defer/waiver refs.
Emit only the closure result requested by the current role briefing. Thin
Judge and Completion Gate own final goal synthesis and completion.

## Expected Runtime Outcomes

Runtime may accept the gap, suppress an equivalent pending plan, reject stale
identity/evidence, or require a new target delta before re-verification. Treat
those outcomes as control facts:

- equivalent pending plan -> wait or materially revise the task set;
- no target delta -> implement a real delta before requesting verification;
- stale/identity mismatch -> repair the input/result binding, not product code;
- accepted gap -> let the deterministic bridge amend/adopt the task map and
  dispatch only the new/revised work.

## Boundary

Agents/adapter Skills determine project semantics, severity, gap scope, and
task meaning. Runtime validates schema/identity/evidence presence, deduplicates
and applies task-map amendments, preserves affinity, counts attempts, and owns
terminal state. This Skill never runs in Thin Judge.
