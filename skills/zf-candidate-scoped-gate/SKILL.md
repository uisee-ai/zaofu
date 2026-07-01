---
name: zf-candidate-scoped-gate
description: "Use when ZaoFu channel, research, plan, or backlog synthesis produces one or more candidate proposals that need a lightweight candidate-level gate before task-map or workflow handoff. Generates candidate gate artifacts with claims, scoped support topics, evidence refs, owner hints, and verdicts without writing runtime truth."
---

# ZaoFu Candidate Scoped Gate

## Purpose

Evaluate one candidate proposal at a time before it becomes task-map input,
workflow handoff, or an approved backlog. This skill narrows the check to the
candidate's own claims, evidence, and risks. It complements
`zf-harness-gate-evaluator`; it does not replace review/test/judge gates.

## Hard Rules

- Do not write `events.jsonl`, `kanban.json`, `session.yaml`,
  `feature_list.json`, or `role_sessions.yaml`.
- Do not mutate `zf.yaml` or task state.
- Do not treat support topics as long-term memory.
- Fail closed when a candidate has material claims but no evidence.
- Gate only the current candidate; do not load unrelated global skills or
  evaluate the whole project unless the candidate scope requires it.

## Candidate Gate Artifact

Write a machine-readable artifact such as
`docs/plans/<work>/candidate-gate-<candidate_id>.json`:

```json
{
  "schema_version": "zf.candidate_gate.v1",
  "candidate_id": "candidate-001",
  "candidate_kind": "channel|research|plan|backlog|workflow",
  "source_artifact_refs": ["docs/plans/example.md#candidate-001"],
  "support_topics": [
    {
      "topic": "API boundary",
      "reason": "Required to verify the candidate scope",
      "source_refs": ["docs/design/23-kanban-runtime-projection-boundary.md"]
    }
  ],
  "claims": [
    {
      "claim_id": "C-001",
      "claim_text": "This candidate can be implemented without changing runtime truth files.",
      "claim_type": "scope",
      "required_evidence": ["allowed path list", "runtime truth boundary"],
      "evidence_refs": ["docs/design/23-kanban-runtime-projection-boundary.md"],
      "verdict": "pass|fail|unknown|not_applicable"
    }
  ],
  "checks": [
    {
      "check_id": "CHK-001",
      "description": "Evidence refs exist for all material claims",
      "verdict": "pass|fail|unknown",
      "evidence_refs": ["docs/plans/example.md"]
    }
  ],
  "verdict": "candidate.acceptable|candidate.needs_evidence|candidate.rework|candidate.reject",
  "owner_hint": "arch|critic|research|orchestrator|review|operator",
  "evidence_refs": ["docs/plans/example.md"]
}
```

## Verdict Rules

- `candidate.acceptable`: all material claims pass and risks are bounded.
- `candidate.needs_evidence`: the proposal may be good, but evidence is
  missing or stale.
- `candidate.rework`: the candidate is directionally useful but must be
  revised before dispatch.
- `candidate.reject`: the candidate conflicts with project constraints,
  runtime truth boundaries, or known decisions.

When in doubt between acceptable and needs evidence, choose
`candidate.needs_evidence`.

## Output Summary

Return a short human summary:

- candidate id and kind
- verdict
- failed or unknown claims
- scoped support topics used
- owner hint and next action
