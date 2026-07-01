---
name: zf-decision-map-replan
description: "Use when ZaoFu plan, replan, channel synthesis, research synthesis, or orchestrator handoff needs a decision-map artifact that records knowns, unknowns, decisions, rejected options, blockers, probes, replan triggers, and source refs without replacing task-map."
---

# ZaoFu Decision Map Replan

## Purpose

Capture fog-of-war during plan and replan. A decision map explains why the
current plan is shaped the way it is, what remains unknown, and which probes or
replan triggers matter. It is evidence/context for the orchestrator; it is not
the dispatch contract.

`task_map.json` remains the canonical dispatch contract defined by
`zf-plan-task-map-contract`.

## Hard Rules

- Do not create a second task schema.
- Do not set kanban status from a decision map.
- Do not treat provider transcript as decision truth.
- Do not erase rejected options without source refs; repeated debate is a
  long-horizon cost.
- If a blocker changes task dependencies, let the orchestrator update the
  task-map rather than encoding dependencies only in this artifact.

## Decision Map Artifact

Write a markdown artifact for human review and, when possible, a JSON sidecar:

```json
{
  "schema_version": "zf.decision_map.v1",
  "work_id": "cangjie-agent-plan",
  "source_refs": ["docs/research/openclaw.md", "docs/research/hermes.md"],
  "knowns": [
    {
      "id": "K-001",
      "statement": "The product requires a GA core plus vertical data-agent capabilities.",
      "source_refs": ["docs/research/product-baseline.md"]
    }
  ],
  "unknowns": [
    {
      "id": "U-001",
      "question": "Which plugin boundary is stable enough for phase 1?",
      "owner_hint": "arch|research|operator",
      "resolution_path": "research|prototype|user_decision"
    }
  ],
  "decisions": [
    {
      "id": "D-001",
      "decision": "Use a thin kernel and skill-driven artifact contracts first.",
      "rationale": "Keeps runtime deterministic while improving agent output quality.",
      "source_refs": ["docs/design/62-plan-artifact-adapter-task-map-synthesis.md"]
    }
  ],
  "rejected_options": [
    {
      "id": "R-001",
      "option": "Put all claim reasoning into kernel enforcement immediately.",
      "reason": "Too thick for the current kernel boundary.",
      "source_refs": ["docs/design/23-kanban-runtime-projection-boundary.md"]
    }
  ],
  "blockers": [
    {
      "id": "B-001",
      "description": "Need source freshness before finalizing competitive assumptions.",
      "owner_hint": "research",
      "severity": "low|medium|high|blocking"
    }
  ],
  "probes": [
    {
      "id": "P-001",
      "question": "Can the current task-map be split into independent vertical slices?",
      "expected_evidence": ["task_map.json", "source_index.json"]
    }
  ],
  "replan_triggers": [
    {
      "trigger": "critical unknown resolved differently than assumed",
      "expected_action": "orchestrator revises task-map"
    }
  ]
}
```

## Use With Task Map

- Use the decision map before task-map synthesis to document decisions and
  unknowns.
- Use it during replan to explain what changed.
- Include `decision_map_ref` in plan/replan summaries when available.
- Keep dependencies and dispatch constraints in `task_map.json`.

## Output Summary

Return:

- decision map path
- unresolved blockers and unknowns
- decisions made
- rejected options
- replan triggers
- whether task-map synthesis can proceed
