---
name: zf-harness-gate-evaluator
description: "Use when evaluating ZaoFu review, test, critic, quality, or judge gates with structured pass/fail evidence, or when a candidate proposal from channel, research, plan, or backlog synthesis needs a lightweight candidate-level gate before task-map or workflow handoff."
---

# ZaoFu Harness Gate Evaluator

Absorbs zf-candidate-scoped-gate.

This skill standardizes gate reports. It does not make the deterministic gate
decision by itself.

## Gate Report

Every gate report must include:

- gate name
- task id or feature id
- input artifact references
- checks performed
- pass/fail verdict
- blocker severity
- evidence paths or command outputs
- rework target when failed
- minimum condition required for pass

### Verify / review reader-gate reports

For verify and review reader gates, generic "checks performed / evidence
paths" prose is not enough. The report payload must follow the kernel
verify-report contract (FIX-14; enforced through the `non_empty` schema tier
in `src/zf/core/verification/event_schema.py` when the project's event schema
config declares it):

- `requirement_coverage_matrix`: at least one row; each row's
  `requirement_id` must come from the task contract, with `source_ref`,
  `status`, and row-level `evidence_refs`
- `gap_findings`: file-level gap locations for anything not covered
- `replan_recommendation`: explicit continue/replan recommendation
- `evidence_refs`: non-empty top-level evidence paths

The kernel educates missing fields with placeholders
(`_SCHEMA_EDU_PLACEHOLDERS` in `src/zf/runtime/orchestrator_fanout.py`); a
zero-row coverage matrix is exactly the failure mode FIX-14 exists to block.

## Evaluation Rules

- Prefer false rejection over unsupported approval.
- Do not accept hedged claims such as "should work" without evidence.
- A prior approval is input evidence, not a shield. Re-review is bound to the
  `target_commit` pinned on the `fanout.child.dispatched` event: if the same
  commit already has a rejection on record and there is no new delta, the
  kernel suppresses the retrigger (`fanout.retrigger.suppressed`, via
  `_delta_gate_allows` in `src/zf/runtime/orchestrator_fanout.py`). Do not
  re-litigate a pinned commit; demand new evidence or a new commit.
- If the weakest required dimension fails, the gate fails.
- Route failures to the earliest role that can fix the root cause.

## Verdicts

The kernel consumes the machine `verdict` field, not prose labels:

- Approval: set `verdict` to `pass` or `approve`. The kernel accepts only
  `pass` / `passed` / `approve` / `approved` (case-insensitive); any other
  value is treated as not approved and blocks downstream approval checks
  such as product delivery.
- Escalation: set `verdict` to `SUSPEND` on a `gate.failed` event to escalate
  to the operator without burning rework cap.
- Failure routing keys off the event type (`gate.failed`, `review.rejected`,
  `verify.failed`, `test.failed`, `judge.failed`), not the verdict string.

Labels such as `PASS_WITH_RISK`, `FAIL_REWORK_DEV`, `FAIL_REWORK_ARCH`,
`FAIL_NEEDS_OPERATOR` may appear in the report body as human-facing
classification, but they are report prose only. The machine `verdict` field
must independently carry a kernel-consumed value as above — `PASS_WITH_RISK`
in the `verdict` field reads as not-approved.

## Candidate-Scoped Gate

Use this mode when channel, research, plan, or backlog synthesis produces
candidate proposals that need a lightweight per-candidate gate before
task-map input, workflow handoff, or backlog approval. It narrows the check
to one candidate's own claims, evidence, and risks; it does not replace
review/test/judge gates.

Naming note: "candidate" in this section means a proposal candidate — a
synthesized idea awaiting a gate. It is not the kernel's `candidate.*`
integration semantics: `src/zf/runtime/candidates.py` manages integration
candidate branches (including FIX-10 patch-id idempotent exclusion via
`git rev-list --cherry-pick`), the kernel emits `candidate.started` /
`candidate.ready` / `candidate.quality.*` events for those branches, and
`delivery_trace.py` maps `candidate.*` events to a `candidate_gate` trace
node. Gating "the current candidate" here never means reviewing an
integration candidate branch.

### Hard rules

- Do not write `events.jsonl`, `kanban.json`, `session.yaml`,
  `feature_list.json`, or `role_sessions.yaml`.
- Do not mutate `zf.yaml` or task state.
- Do not treat scoped support topics as long-term memory.
- Fail closed: a candidate with material claims but no evidence cannot be
  `acceptable`.
- Gate only the current candidate; do not evaluate the whole project unless
  the candidate scope requires it.

### Candidate verdicts

Use one of (skill-owned vocabulary, no kernel validation; deliberately not
event-styled to avoid colliding with kernel `candidate.*` event names):

- `acceptable`: all material claims pass and risks are bounded.
- `needs_evidence`: the proposal may be good, but evidence is missing or
  stale.
- `rework`: directionally useful but must be revised before dispatch.
- `reject`: conflicts with project constraints, runtime truth boundaries, or
  known decisions.

When in doubt between `acceptable` and `needs_evidence`, choose
`needs_evidence`.

### Handoff to the kernel proposal channel

An `acceptable` candidate that should become a task goes through the
owner-gated proposal path, never direct truth-file writes: kanban proposals
(`src/zf/runtime/kanban_proposals.py`, actions `create-task` /
`idea-to-product`) and the `channel.artifact.proposed` /
`channel.artifact.rejected` events. The candidate gate artifact is input
evidence for that path.

### Candidate gate artifact

Record the result as a machine-readable artifact, e.g.
`docs/plans/<work>/candidate-gate-<candidate_id>.json` (skill-owned
convention, no kernel validation), carrying: candidate id and kind
(channel/research/plan/backlog/workflow), source artifact refs, scoped
support topics with reasons and source refs, per-claim verdicts with required
evidence and evidence refs, an overall verdict from the list above, and an
owner hint. Then return a short human summary: candidate id and kind,
verdict, failed or unknown claims, support topics used, owner hint and next
action.
