---
name: zf-harness-design-handoff
description: "Use when you are arch about to emit arch.proposal.done OR critic about to emit design.critique.done. Defines the shared schema both sides agree on so orchestrator can synthesize the backlog (6 refs) at stage ④ without ambiguity."
---

# ZaoFu Design Handoff (arch ↔ critic ↔ orchestrator)

## Why this skill exists

Stage ② design (arch) and stage ③ design_critique (critic) feed stage ④
backlog (orchestrator). Arch may create rich **candidate** plan/backlog
artifacts, but only orchestrator accepts/merges them into final task
contracts. If arch and critic don't agree on the event payload shape,
orchestrator can't deterministically synthesize the 6
`required_backlog_refs` (`docs/impl/22-zaofu-canonical-dag.md` §4.2.1)
and `task.contract.invalid` blocks dev dispatch.

This skill is the contract between three roles.

## Ref channels (do not mix)

ZaoFu accepts planning output from any content skill pack, but lifecycle event
payloads use a stable adapter contract:

- `artifact.manifest.published.artifact_refs` is the **structured manifest**
  channel: object refs with `kind/path/sha256/summary/status`.
- `arch.proposal.done` / `design.critique.done` / terminal gate events use
  `artifact_refs` as a **string path list** only. If you need structured
  metadata, publish a manifest first and reference its event id.
- `artifact_manifest_event_id` links the lifecycle event back to the structured
  manifest. This keeps ZaoFu independent from whether the content came from
  `agent-skills`, yoke, a project-local skill pack, or a different provider.

## Schema arch MUST emit (arch.proposal.done)

```json
{
  "summary": "<one-sentence behavior statement; goes into contract.behavior verbatim>",
  "file_plan": [
    "packages/foo/src/bar.ts",
    "test/unit/foo/bar.test.ts",
    "packages/foo/package.json"
  ],
  "test_plan": {
    "framework": "vitest" or "pytest" or "...",
    "location": "test/unit/foo/bar.test.ts",
    "cases": [
      "happy_path",
      "boundary_zero",
      "boundary_max",
      "invalid_input_throws"
    ],
    "min_required": 4
  },
  "verification_command": "PATH=... pnpm install --frozen-lockfile && pnpm exec vitest run test/unit/foo/bar.test.ts",
  "scope_compliance": {
    "respects_exclusions": true,
    "touches_only_listed_files": true
  },
  "risks_and_clarifications": [
    "RISK-1: <thing reviewer should check>",
    "..."
  ],
  "evidence_refs": [
    "packages/foo/package.json",
    "docs/specs/phase-1/...",
    "CLAUDE.md §6"
  ],
  "artifact_manifest_event_id": "evt-<artifact.manifest.published id>",
  "artifact_refs": [
    "docs/plans/foo-implementation-plan.md",
    "docs/plans/foo-backlog.md"
  ],
  "dispatch_id": "<from briefing>"
}
```

**Required fields**: `summary`, `file_plan`, `test_plan` (or `verification_command`), `dispatch_id`.

**Optional but strongly recommended**: `risks_and_clarifications`, `scope_compliance`,
`evidence_refs`, `artifact_manifest_event_id`, `artifact_refs`. These reduce
critic's auditing effort and let orchestrator pre-populate
`contract.exclusions` and `contract.evidence_contract`.

If arch created rich artifact metadata, it MUST be published in
`artifact.manifest.published` before `arch.proposal.done`. Do not paste object
refs into `arch.proposal.done.artifact_refs`; that field is a replayable string
path list for evidence gates.

Arch-authored planning artifacts are normally `status=draft` or
`status=proposed`. That status means "candidate input for critic and
orchestrator", not accepted runtime truth. Orchestrator may accept the candidate
as-is, merge several candidate artifacts into a new final artifact, or reject
the package and re-dispatch arch.

## Schema critic MUST emit (design.critique.done OR gate.failed)

### Success path (critic approves arch's plan)

```json
{
  "verdict": "approve",
  "summary": "<one-line: what was reviewed and what conclusion>",
  "risks": [],   // empty or low-severity informational risks
  "fix_items": [],   // empty — nothing to fix
  "evidence_refs": [
    "arch event evt-...",
    "packages/foo/package.json",
    "docs/specs/..."
  ],
  "dispatch_id": "<from briefing>"
}
```

### Reject path — TWO event-type options (same semantic after P1/K2):

**Option A — `design.critique.done verdict=reject`** (preferred default):

```json
{
  "verdict": "reject",
  "summary": "<reason in one sentence>",
  "risks": [
    "BLOCKER-1: <hard issue arch must fix>",
    "RISK-2: <soft issue worth flagging>"
  ],
  "fix_items": [
    "concrete change arch must apply in v2",
    "..."
  ],
  "evidence_refs": ["..."],
  "next_action": "arch 重新发布 arch.proposal.done with fixes (a)..(b)..",
  "dispatch_id": "<from briefing>"
}
```

**Option B — `gate.failed`** (use for hard BLOCKERs that fundamentally
invalidate the design):

```json
{
  "verdict": "REJECT",   // or "SUSPEND"
  "summary": "<reason>",
  "risks": ["..."],
  "required_action": "<what arch must do>",
  "evidence_refs": ["..."],
  "dispatch_id": "<from briefing>"
}
```

After P1/K2, both routes go to arch via `workflow.rework_routing`. Use
Option B for `SUSPEND` (block + escalate to human) — that semantic doesn't
fit into `design.critique.done`'s verdict enum.

## yoke envelope compatibility

When using yoke's `plan-option-scoring` skill, structure the payload using
the `zaofu_gate` envelope (`yoke/role-skills/critic/plan-option-scoring/SKILL.md:319`):

```yaml
zaofu_gate:
  stage: design_critique
  role: critic
  verdict: APPROVE | CONDITIONAL | REJECT | SUSPEND
  success_event: design.critique.done
  failure_event: gate.failed
  selected_option: A | B | C | none
  payload:
    scoring_dimensions:
      - code_landing_feasibility
      - dependency_ordering
      - time_estimation_realism
      - discipline_depth
      - risk_identification
    weakest_dimension: "<dimension name>"
    required_action: "<arch fix specifics>"
```

The 6-ref synthesis (orchestrator's stage ④) reads BOTH the envelope and
the arch event. zaofu-skills do NOT invent alternate verdict labels — yoke
is the source of truth for `verdict`.

## What orchestrator extracts at stage ④ (for cross-reference)

| Backlog ref | Source event | Path |
|---|---|---|
| `spec_ref` | artifact manifest / user.message | reviewed `spec` / `sdd` artifact path, or `payload.spec_refs` |
| `plan_ref` | orchestrator final synthesis | accepted plan/process artifact path, often merged from arch `draft/proposed` refs |
| `tdd_ref` | artifact manifest / arch.proposal.done | reviewed `tdd` / `test_plan` path, or compact `payload.test_plan` |
| `critic_event_id` | design.critique.done | `event.id` |
| `critic_gate_ref` | design.critique.done | `payload.verdict + fix_items summary` |
| `evidence_contract` | arch.proposal.done + design.critique.done | merge of `arch.verification_command` + critic's recommended runtime checks |

If any of these can't be filled because arch / critic omitted a required
field, orchestrator MUST re-dispatch the prior role (arch for missing
file_plan/test_plan; critic for missing verdict/fix_items) instead of
fabricating values.

## Common failure modes

- **arch emits empty file_plan and no candidate plan artifact** — orchestrator can't fill `plan_ref` with concrete files. Re-dispatch arch with a more explicit briefing.
- **critic emits gate.failed with no fix_items** — arch v2 has nothing concrete to address. Re-dispatch critic asking for specific fixes.
- **arch v2 doesn't reference `evt-<critic_event_id>` in its proposal** — orchestrator can't trace which critique was addressed. Re-dispatch arch with explicit `previous_critique_event_id` in the briefing.
- **candidate artifact status is misunderstood as final truth** — keep the artifact indexed, but do not dispatch implementation until orchestrator writes `task.contract.update` with final 6 refs.

## Related skills

- `zf-harness-backlog-synthesis` — orchestrator's stage ④ procedure (consumes this contract)
- `zf-yoke-critic-role-context` — critic verdict discipline + Reject Event Type
- `zf-yoke-orchestrator-role-context` — Stage Routing table including ④ backlog
- `spec-driven-development` (agent-skills) — how arch builds the proposal from spec
- `plan-option-scoring` (yoke critic) — multi-option critic scoring shape
