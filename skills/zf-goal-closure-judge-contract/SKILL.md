---
name: zf-goal-closure-judge-contract
description: "Use only for the final read-only Thin Judge in ZaoFu issue, PRD, or refactor workflows. Synthesizes admitted planning, verification, waiver, and closure facts into goal-closure-result.v1 without rerunning tests, editing the product, or planning rework."
stages: [judge]
tags: [contract, goal-closure, thin-judge, read-only]
auto_inject: true
load_on_demand: false
---

# ZaoFu Thin Goal Closure Judge

## Purpose

Decide whether the immutable, already-admitted evidence closes the original
Goal. This role is a semantic synthesizer, not another Verify worker. The
Kernel owns result admission, gap routing, completion blockers, delivery, and
the final `run.goal.completed` transition.

## Read Boundary

Read only the refs supplied by the briefing:

- the original objective and accepted planning result;
- the canonical `goal-claim-set.v1` ref and digest;
- admitted task/candidate verification result refs;
- admitted waiver or human-decision refs, when present;
- the current `flow.goal.closed` or `module.parity.closed` fact;
- the immutable candidate/target snapshot.

Do not search the product tree for replacement evidence. Do not run tests,
builds, browsers, providers, package managers, or Git mutation commands. Do
not edit source, plans, task maps, truth files, or artifacts. An unreadable or
missing required ref yields `blocked`.

## Output Contract

Return one top-level `goal_closure_result` object:

```json
{
  "goal_closure_result": {
    "schema_version": "goal-closure-result.v1",
    "workflow_run_id": "<pinned run>",
    "goal_id": "<pinned goal>",
    "flow_kind": "issue|prd|refactor",
    "task_map_generation": "<pinned generation>",
    "target_commit": "<pinned candidate head>",
    "objective_ref": "<immutable objective ref>",
    "goal_claim_set_ref": "<goal-claim-set ref>",
    "goal_claim_set_digest": "<goal-claim-set digest>",
    "planning_result_ref": "<admitted planning result ref>",
    "candidate_ref": "<candidate ref>",
    "closure_fact_ref": "<closure fact ref>",
    "closure_fact_digest": "<closure fact digest>",
    "input_result_refs": ["<admitted result ref>"],
    "goal_coverage": [
      {
        "goal_claim_id": "<exact canonical id>",
        "status": "closed|open|blocked|waived",
        "supporting_result_refs": ["<admitted result ref>"],
        "waiver_ref": "<required when waived>"
      }
    ],
    "open_gap_refs": [],
    "verdict": "passed|rejected|blocked",
    "recommended_action": "complete|gap_plan|replan|candidate_verify|human|hold",
    "summary": "<concise evidence-grounded synthesis>"
  }
}
```

Copy pinned identity fields exactly; never infer a newer target or generation.
Cover every mandatory canonical claim exactly once. A `closed` claim requires
at least one admitted supporting result ref. A `waived` claim requires a
waiver ref.

## Verdict Rules

- `passed`: every mandatory claim is `closed` or validly `waived`, no open gap
  remains, and `recommended_action` is `complete`.
- `rejected`: semantic evidence identifies open Goal gaps; include durable
  `open_gap_refs` and choose `gap_plan`, `replan`, or `candidate_verify`.
- `blocked`: evidence cannot be evaluated because of an external dependency,
  missing admission, or required human decision; choose `human` or `hold`.

All three verdicts mean the Judge call itself executed successfully. Emit the
configured child success event for a schema-valid result. Reserve the child
failure event for provider/execution failure or an output that cannot be made
schema-valid in the same attempt. Never emit `judge.passed`, `judge.failed`,
`task.done`, gap-plan events, ship events, or `run.goal.completed` directly.

## Handoff

The Kernel admits the typed result through `call-result-envelope.v1` and then:

- routes `rejected` to the canonical semantic gap router;
- holds `blocked` for Run Manager or human resolution;
- turns `passed` into an active completion claim and mechanical gate check.
