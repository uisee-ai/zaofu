---
name: zf-workflow-adaptation-boundary
description: "Use in ZaoFu issue, PRD, and refactor workflows when an agent must decide whether a project-specific adaptation belongs in skills/prompt/agent judgment or deterministic runtime/kernel/API/preflight code. Helps new-project intake, scan, plan, verify, and judge keep semantic adaptation out of runtime hardcode while still escalating missing invariants."
---

# ZaoFu Workflow Adaptation Boundary

## Rule

Prefer this split:

- **Skill / prompt / agent** owns project semantics, scan method, implementation
  strategy, module parity interpretation, task slicing, and domain-specific
  verification guidance.
- **Runtime / kernel / API / preflight** owns schemas, state transitions,
  event names, evidence existence, path safety, credential redaction, budget
  enforcement, run lifecycle, and external side effects.

If an adaptation is both semantic and safety-critical, emit an artifact with
the semantic decision, then require deterministic preflight/gate evidence before
the workflow proceeds.

## Intake Checklist

For every `issue`, `prd`, or `refactor` intake, write or preserve:

- `workflow-input-manifest.json` with `kind`, objective, source refs, target
  refs, backend, lanes, strictness, and required-field gaps.
- `skill-adapter-plan.json` with loaded skills, missing skills, diagnostics,
  proposed project adapter skills, and `roleSkillBundles`.
- A clear operator-facing proposal when required fields, environment, budget,
  or channel binding are not ready.

Do not let a long workflow start from chat-only context.

## Kind-Specific Boundaries

- **issue**: bug reproduction, root-cause hypothesis, and regression scope
  belong in `zf-issue-plan-synth` plus domain skills. Deterministic code should
  only require task anchors, allowed paths, and regression evidence refs.
- **prd**: product choices, UX/API shape, and implementation slicing belong in
  `zf-prd-plan-synth` plus domain skills. Deterministic code should require PRD
  artifacts, task map refs, and demo/test evidence.
- **refactor**: source inventory, module mapping, parity scope, and gap
  synthesis belong in refactor/project adapter skills. Deterministic code should
  enforce source/target path safety, immutable evidence refs, bounded rework,
  and final gap closure.

## Escalation

Create or update a skill when:

- the adaptation describes how to scan, plan, implement, verify, or judge a
  particular project/domain;
- the rule would otherwise hard-code product names, modules, UI details,
  provider choices, or parity expectations in runtime;
- the same judgment is needed by multiple roles.

Create runtime/API/preflight work when:

- the workflow can corrupt source code, runtime truth, credentials, budget, or
  external channels;
- a missing artifact/event/schema would make resume or audit ambiguous;
- a dashboard/API action needs deterministic response fields.

How to test: run `zf flow intake --kind issue|prd|refactor ...` and inspect the
generated `skill-adapter-plan.json`; this skill should appear in loaded skills
and relevant `roleSkillBundles` when the local ZaoFu skills source is available.
