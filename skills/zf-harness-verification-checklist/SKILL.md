---
name: zf-harness-verification-checklist
description: "Use before a ZaoFu Verify/test role issues a product verdict; checks target binding, acceptance coverage, reproducibility, and failure classification without owning runtime state."
stages: [verify, test, discovery, scan, refactor_scan]
tags: [verification, evidence, checklist]
auto_inject: false
load_on_demand: true
---

# ZaoFu Verification Checklist

Use this reference at verdict time under
`zf-yoke-test-evaluator-role-context`. It defines verification quality, not the
event schema or recovery state machine.

## Checklist

- The checked worktree/ref is the immutable target named by the briefing.
- The task contract and every mandatory acceptance criterion were read.
- Each criterion has a behavior-specific check; a green generic command alone
  is not coverage.
- Required verification tiers have reproducible command or artifact evidence.
- Changed files/artifacts and their evidence belong to the assigned scope.
- Declared implementation checks were independently rerun where applicable.
- Required regression, integration, browser, provider, packaging, or live
  checks were added when the contract calls for them.
- Skipped checks name the reason, risk, and next owner.
- Verifier/environment execution failure is distinct from product failure.
- Rejected and blocked criteria have evidence of the same quality expected for
  passed criteria.

## Verdict Method

- `passed`: all mandatory criteria are covered; no blocking finding remains.
- `rejected`: verification executed successfully and evidence proves one or
  more product criteria failed.
- `blocked`: evidence proves an external prerequisite or decision prevents a
  product verdict.
- verifier execution failure: do not manufacture a product verdict; report the
  failed execution/target condition through the configured result profile.

The current runtime-provided `verification-result`/child report profile is the
authoritative output shape. Fill it exactly and cite durable evidence refs.
Do not copy field lists from old examples, emit a different terminal event, or
move task truth directly.

## Gap Handoff

Only blocking product/parity gaps become gap work. Load
`zf-verify-gap-producer-contract` to produce the canonical gap handoff; load
`zf-goal-closure-replan-contract` only when that handoff requires task-map
amendment. Suggestions and nits stay in the verification report.

## Boundary

Runtime validates schema, identity, evidence presence, result admission,
attempt/cap, affinity, stale/replay, and terminal state. This checklist decides
whether the selected checks and evidence meaningfully prove the project
contract.
