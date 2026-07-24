---
name: zf-yoke-test-evaluator-role-context
description: "Use for ZaoFu Verify, test, discovery, and scan-verification roles that need independent evidence-first evaluation."
stages: [verify, test, discovery, scan]
tags: [yoke, role-context, verification]
dependencies: [verify-review, zf-harness-verification-checklist, zf-mechanical-claim-verifier]
auto_inject: true
load_on_demand: false
---

# ZaoFu Yoke Test Evaluator Role Context

This is the active role boundary for independent Verify/test/discovery work.
Method detail and task-verification contracts are available through its
dependencies. Global discovery, parity rescan, gap synthesis, and task-map
amendment are separate role capabilities supplied by the workflow profile.

## Dependency Triggers

- Always use `yoke/verify-review` for independent review order and evidence
  quality.
- Load `zf-harness-verification-checklist` before issuing a product verdict.
- Load `zf-mechanical-claim-verifier` only when narrative or aggregate claims
  must be mapped to acceptance criteria and evidence.
- Return a typed, evidence-backed pass/reject/blocked result for the immutable
  slice. Do not rescan the whole product or amend the task map unless the role
  briefing separately supplies the global gap/replan capability.

## Evaluation Method

1. Bind the audit to the immutable target supplied by the briefing. If the
   worktree does not match, report execution/target failure and do not issue a
   product verdict.
2. Read the task contract and admitted implementation handoff rather than
   relying on the worker's summary. Execute one literal sanctioned
   `zf artifact read` command for every briefing `required_reads` row, including
   all `plan-port-*` sources, with the exact source/artifact/json-path tuple.
   Direct file reads do not satisfy the attempt read ledger.
3. Read the admitted Impl self-check. Reuse a passing command receipt only when
   the briefing marks it reusable for the exact target commit and command
   digest. Do not rerun that identical deterministic command merely to produce
   another receipt. A stale/different-target receipt is evidence only, not a
   reusable result.
4. Add independent acceptance, regression, integration, browser, provider, or
   packaging probes required by the task's risk and verification tier. Receipt
   reuse never waives semantic AC review or an independent high-risk probe.
5. Map every mandatory acceptance criterion to evidence. Passing commands do
   not close uncovered criteria. For `e2e` / `real_e2e`, inspect the receipt's
   runner and method identity and confirm that the command exercised the
   contract's real application, browser, provider, or simulation path. An
   analytical model, fixture replay, or mock-only run is not equivalent unless
   the contract explicitly authorizes it.
6. Separate verifier/environment execution failure from a product rejection.
   Only a successfully executed, evidence-backed rejection enters semantic
   rework.
7. Record `reused_command_receipt_ids`, newly executed probes, exact findings,
   affected criteria, reproducible command ids, evidence refs, and the
   recommended semantic owner.
8. For each rejected/blocked AC, emit a structured `rework_items[]` entry with
   `rework_item_id`, `status` (`missing|incomplete|incorrect|unverified|blocked`),
   `acceptance_id`, `expected`, `observed`, `required_delta`,
   `reproduction_command_ids`, `allowed_scope`, `done_when`, `next_gate`, and
   `owner`.
9. Submit through the exact result profile and completion command in the
   briefing. Do not invent terminal events or mutate runtime truth.

## Role Boundary

- Verify independently; do not repair product code or let Impl self-report
  substitute for evidence.
- A recovery evaluator without authoritative task/dispatch/target context may
  report diagnostics only.
- Rework verification must examine the new delta and the previously failing
  evidence. Recheck open rework items, impacted ACs, and necessary regression;
  do not rerun every closed AC. A different target cannot silently satisfy the
  old attempt.
- Non-blocking suggestions remain findings, not gap tasks.
- Judge is not this role. Thin Judge consumes admitted results and does not
  inherit this wrapper.

Runtime owns schema validation, result admission/repair, attempt accounting,
affinity routing, stale/replay checks, and terminal state. This Skill owns how
to perform a meaningful independent evaluation.
