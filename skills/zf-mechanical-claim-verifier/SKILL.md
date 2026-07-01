---
name: zf-mechanical-claim-verifier
description: "Use when ZaoFu needs to convert agent completion statements, design assertions, review approvals, channel synthesis, or gate reports into a structured claim set with evidence refs, deterministic verdicts, passRate, failedClaims, unknownClaims, and regenPlan."
---

# ZaoFu Mechanical Claim Verifier

## Purpose

Turn narrative statements such as "done", "tested", "safe", "covered", or
"ready" into explicit claims that can be checked against evidence. This skill
extends `zf-harness-evidence-collection` and
`zf-harness-verification-checklist`; it does not decide runtime truth by itself.

## Hard Rules

- Prefer command/file/event evidence over narrative evidence.
- Do not count an unsupported claim as pass.
- Do not use an LLM score as `passRate`; derive it from claim verdicts.
- Do not emit terminal completion events or edit runtime truth.
- If a referenced artifact path is unavailable, mark the related claim
  `unknown` or `fail`.

## Claim Set Artifact

Write a machine-readable artifact such as
`docs/impl/<work>/claim-set.json`:

```json
{
  "schema_version": "zf.claim_set.v1",
  "subject": {
    "kind": "task|candidate|plan|review|test|channel|release",
    "id": "TASK-001",
    "source_refs": ["docs/plans/example.md"]
  },
  "claims": [
    {
      "claim_id": "CLM-001",
      "claim_text": "The implementation is covered by a focused unit test.",
      "claim_type": "implementation|test|design|risk|scope|artifact",
      "required_evidence": ["test command", "test output", "changed files"],
      "evidence_refs": ["reports/test-output.txt"],
      "verdict": "pass|fail|unknown|not_applicable",
      "reason": "The referenced test output shows the focused test passed."
    }
  ]
}
```

## Verifier Output

Write or include verifier output:

```json
{
  "schema_version": "zf.claim_verifier_result.v1",
  "claim_set_ref": "docs/impl/example/claim-set.json",
  "passRate": 0.67,
  "failedClaims": ["CLM-002"],
  "unknownClaims": ["CLM-003"],
  "regenPlan": [
    {
      "owner_hint": "dev|arch|research|review|test|operator",
      "action": "Produce focused test evidence for CLM-003",
      "required_evidence": ["pytest output", "artifact path"]
    }
  ],
  "evidence_refs": ["reports/test-output.txt"],
  "verdict": "pass|fail|needs_regen"
}
```

Compute `passRate` as:

```text
passing claims / claims with verdict pass|fail|unknown
```

Ignore `not_applicable` claims in the denominator.

## Failure Policy

- Missing required evidence -> `unknown` unless the source explicitly
  contradicts the claim, then `fail`.
- Missing artifact path for an artifact claim -> `fail`.
- Test, scope, or risk claims with no command/file evidence -> `unknown`.
- Any failed or unknown required claim -> output a concrete `regenPlan`.

## Output Summary

Return:

- claim count, pass count, fail count, unknown count
- `passRate`
- top failed or unknown claims
- regen plan owner hints
- evidence refs used
