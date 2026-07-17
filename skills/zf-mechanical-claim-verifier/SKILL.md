---
name: zf-mechanical-claim-verifier
description: "Use on demand to turn narrative completion or parity claims into acceptance-linked evidence judgments before writing the runtime-provided verification result."
stages: [verify, test, discovery, scan]
tags: [verification, claims, evidence]
auto_inject: false
load_on_demand: true
---

# ZaoFu Mechanical Claim Verifier

Use this method when the input contains broad statements such as "done",
"covered", "compatible", or "ready" that must be decomposed before a Verify
result can be trusted. It is an internal reasoning aid, not a second result
schema or terminal authority.

## Method

1. Take the acceptance/goal claim IDs from the current contract or claim-set;
   do not invent replacement IDs.
2. Split each broad statement into the smallest independently checkable claim.
3. For each claim, name the required evidence and inspect concrete command,
   file, artifact, event, screenshot, transcript, or Git refs.
4. Bind evidence to the immutable target supplied by the briefing.
5. Mark the claim `pass`, `fail`, `unknown`, or `not_applicable` with a short
   reason. Missing evidence is never a pass.
6. Map the judgments into the current runtime-provided verification result:
   passed claims become covered requirement rows; failed/blocked claims become
   evidence-backed findings. Do not emit an internal claim table as the final
   product.

## Evidence Rules

- Prefer reproducible evidence over narrative summaries or LLM scores.
- Derive any diagnostic pass ratio from claim verdicts; it has no runtime
  authority.
- A referenced artifact that cannot be resolved makes the dependent claim
  `unknown` or `fail`.
- A changed target invalidates the old claim check; rebind and rerun instead of
  carrying the verdict forward.
- Unknown required claims need a concrete evidence-generation action and
  suggested semantic owner.

## Boundary

Do not:

- define a new `claim-set` or verifier-result truth schema;
- emit terminal completion events;
- choose affinity/rework routes or increment attempts;
- mutate task, candidate, or run truth;
- convert verifier/protocol failure into product rejection.

Runtime owns result schema/admission, identity/digest checks, evidence
presence, stale/replay, recovery routing, and terminal state. The independent
evaluator owns whether the claims and evidence actually support the project
semantics.
