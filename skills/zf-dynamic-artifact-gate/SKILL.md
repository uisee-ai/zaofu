---
name: zf-dynamic-artifact-gate
description: "Use in ZaoFu issue, PRD, or refactor workflows when plan, verify, judge, or replan stages must generate run-specific fact artifacts and artifact/matrix gate configs instead of hard-coding project facts in runtime or static JSON. Triggers on gate_config_ref, artifact_matrix_gate, scan inventory coverage, acceptance/test matrices, goal gap reports, or verify-rescan gap loops."
---

# ZaoFu Dynamic Artifact Gate

## Purpose

Keep ZaoFu runtime generic. Runtime may validate schemas, matrix rows, required
fields, statuses, and refs; project facts must come from generated artifacts
and project skills.

Use this skill to generate the artifacts that bridge agent reasoning and
deterministic gates.

## Layer Split

Static control-plane contract:

- event names, required top-level refs, canonical task-map shape;
- gate schema fields such as `required_artifacts`, `matrix_paths`,
  `required_row_fields`, `required_row_field_groups`, `allowed_statuses`;
- generic closure fields such as `goal_gap_report_paths` and
  `goal_gap_task_map_paths`.

Dynamic run facts:

- source inventory and capability rows;
- acceptance, test parity, dashboard/API/CLI matrices;
- task map, gap task map, replan history;
- forbidden dependency/text checks derived from the target project;
- generated gate config referenced by `gate_config_ref`.

Do not encode project module names, product brands, provider lists, UI routes,
or source paths in runtime code. Keep those in project skills, prompt, profile,
or generated artifacts.

## Required Plan Outputs

When a workflow has verify or judge gates, plan synthesis must write and emit:

- `plan_artifact_ref`;
- `task_map_ref`;
- `source_index_ref`;
- `gate_config_ref`;
- matrix refs relevant to the goal, for example `acceptance_matrix_ref`,
  `test_matrix_ref`, `dashboard_matrix_ref`;
- `artifact_manifest_ref`;
- `skills_provenance_ref`;
- `replan_history_ref` when the goal can loop.

Include these refs at the top level of the success payload and in
`artifact_refs` or `evidence_refs`.

## Gate Generation Procedure

1. Read the current goal, scan inventory, PRD/issue/refactor evidence, and
   existing plan artifacts.
2. Build or update source inventory rows with stable ids and priorities.
3. Build acceptance/test/runtime matrices that map P0/P1 rows back to source
   inventory ids and source refs.
4. Build task-map tasks that close the inventory rows and include verification.
5. Generate the artifact/matrix gate config from those artifacts.
6. Emit `gate_config_ref` pointing to the generated config, not a stale sample.

The generated gate should include:

```json
{
  "schema_version": "zf.artifact_matrix_gate.v1",
  "generated_from": "docs/plans/artifact-gate.template.json",
  "generation_inputs": [
    "<source-index-ref>",
    "<acceptance-matrix-ref>",
    "<test-matrix-ref>",
    "<task-map-ref>"
  ],
  "source_inventory_refs": ["<source-inventory-ref>"],
  "required_artifacts": ["<expected output paths>"],
  "matrix_paths": ["<matrix paths>"],
  "blocking_priorities": ["P0", "P1"],
  "allowed_statuses": ["done", "implemented", "passing", "passed", "covered", "ok", "closed"],
  "required_row_fields": ["id", "capability", "priority", "status", "source_refs", "evidence_refs"],
  "required_row_field_groups": [
    ["verification", "verify_commands", "verification_commands", "test_refs", "runtime_evidence_refs"]
  ],
  "goal_gap_report_paths": ["<goal-gap-report-ref>"],
  "goal_gap_task_map_paths": ["<gap-task-map-ref>"]
}
```

Static templates may keep this shape, but the run-specific config must be
regenerated from current evidence.

## Replan And Rescan

When scan, verify, judge, supervisor, or run manager discovers a missing P0/P1
surface:

- update the source inventory or gap report;
- update acceptance/test/runtime matrices;
- synthesize bounded gap tasks through the canonical task-map contract;
- regenerate `gate_config_ref` in the same amendment;
- append replan history with `reason`, `source_refs`, `affected_tasks`,
  `new_contract_refs`, and `gate_changes`.

Do not only write feedback prose. A gap is actionable only when it appears in
the generated matrices and task-map/gap-task artifacts.

## Validation Checklist

Before emitting pass:

- the `gate_config_ref` file exists and is valid JSON;
- every generated matrix path exists or has a blocking finding;
- every P0/P1 row has source refs and at least one verification evidence field;
- every blocking inventory row is owned by a task or a structured open gap;
- final judge sees zero open P0/P1 gaps or emits failure;
- no runtime code change was needed for project-specific facts.
