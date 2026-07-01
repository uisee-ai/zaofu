---
name: zf-harness-evaluator-scoring
description: "Use when scoring ZaoFu test or judge results across correctness, completeness, regression risk, and evidence quality."
---

# ZaoFu Harness Evaluator Scoring

This skill adapts yoke evaluator-scoring discipline to ZaoFu gates. Scoring is
advisory evidence; runtime gates remain deterministic.

## Dimensions

Score each dimension as `pass`, `risk`, or `fail`:

- correctness
- completeness
- regression risk
- test coverage
- evidence quality
- scope fidelity
- operator safety

## Aggregation Rule

The final verdict follows the weakest required dimension:

- any required `fail` -> fail
- no fail but any required `risk` -> pass with risk
- all required pass -> pass

Do not average away a hard failure.

## Output Shape

Include:

- task id
- per-dimension score under `scores`
- weakest dimension
- final verdict
- evidence references
- required fix or rework target

For strict ZaoFu `judge.passed` evidence, the runtime expects at least these
score keys:

- `correctness`
- `completeness`
- `regression_risk`
- `evidence_quality`

Do not rename these keys in the completion payload.
