---
name: zf-harness-lane-goal-continuation
description: "Use for ZaoFu fanout writer children, affinity lanes, product-delivery waves, or provider goal-mode runs that must complete a scoped lane without shrinking the feature objective or claiming global done."
---

# ZaoFu Lane Goal Continuation

This skill turns provider goal continuation into a ZaoFu lane-level worker
contract. It shapes the worker prompt and terminal claim; it does not replace
task-map admission, affinity scheduling, candidate integration, or deterministic
gates.

## When To Use

Use when a worker receives a fanout child, especially:

- `topology: fanout_writer_scoped`
- `fanout.assignment.strategy: affinity_stage_slots`
- `product_delivery.wave.ready`
- a task-map slice with `affinity_tag`, `lane_id`, `stage_slot`,
  `allowed_paths`, `exclusive_files`, or `blocked_by`
- provider-native goal mode continues one scoped task across turns

## Core Rule

The lane goal is a scoped objective, not product truth.

- Keep the full feature / wave objective visible.
- Complete only the assigned lane task.
- Do not redefine success around an easier subset.
- Do not claim feature, candidate, release, or product done.
- Emit only the configured role result event with evidence; ZaoFu kernel gates
  decide whether the lane, fanout, candidate, and task are accepted.

## Lane Goal Envelope

Every lane briefing should carry this shape, either rendered directly or through
equivalent fields:

```text
## Lane Goal Continuation

Feature objective:
  <full feature / PDD / wave objective>

Lane binding:
  fanout_id: <fanout_id>
  child_id: <child_id>
  run_id: <run_id>
  task_id: <task_id>
  lane_id: <lane_id, if any>
  stage_slot: <impl|review|verify, if any>
  affinity_tag: <module/domain tag>
  task_map_ref: <accepted task-map ref>
  source_index_ref: <source index ref, if any>

Scope:
  allowed_paths: [...]
  exclusive_files: [...]
  shared_files: [...]
  blocked_by: [...]
  verification: <command or evidence contract>

Completion evidence:
  source_commit == current branch HEAD
  files_touched stays inside allowed/exclusive scope
  required verification commands or artifacts are present
  evidence_refs and artifact_refs are replayable
```

## Continuation Behavior

Before ending a turn, audit the lane:

1. Derive concrete lane requirements from `task_map_ref`, task capsule,
   briefing, acceptance, verification, and referenced artifacts.
2. Inspect current state: workdir, git diff/status, produced files, command
   outputs, and event/task refs.
3. If any lane requirement lacks evidence, keep working or emit a blocking
   event; do not emit the success event.
4. If the same true blocker repeats across repeated continuation turns, emit
   the configured blocked/suspend event with concrete blocker evidence.
5. If lane evidence is complete, emit the role success event only for this lane.

Provider-native goal status is advisory. If a provider goal says complete but
the ZaoFu lane evidence is missing, treat it as incomplete and continue.

## Terminal Claim Payload

Lane completion claims should include these fields when available:

```json
{
  "fanout_id": "<fanout_id>",
  "stage_id": "<stage_id>",
  "child_id": "<child_id>",
  "run_id": "<run_id>",
  "task_id": "<task_id>",
  "lane_id": "<lane_id>",
  "stage_slot": "<stage_slot>",
  "affinity_tag": "<affinity_tag>",
  "task_map_ref": "<task_map_ref>",
  "source_index_ref": "<source_index_ref>",
  "workdir": "<assigned workdir>",
  "source_branch": "<worker branch>",
  "source_commit": "<HEAD commit>",
  "files_touched": ["<repo-relative path>"],
  "commands": [{"cmd": "<verification command>", "exit_code": 0}],
  "artifact_refs": ["<path or ref>"],
  "evidence_refs": ["git:<sha>", "event:<id>", "<path>"],
  "risks": []
}
```

If a field is unavailable, report why. Do not silently drop `fanout_id`,
`child_id`, `run_id`, `task_id`, `task_map_ref`, or `source_commit` from writer
success claims.

## Boundaries

- Skills may generate the lane goal text and report shape.
- `zf.yaml` declares topology, roles, lane profiles, and stage slots.
- The kernel assigns slots, queues overflow children, releases lanes, validates
  current task-map admission, and performs candidate integration.
- Gates decide completion from events and evidence, not from narrative claims.

## Related Skills

- `zf-harness-goal-loop-contract` — feature / long-horizon goal condition.
- `zf-harness-backlog-synthesis` — task-map and lane metadata synthesis.
- `zf-harness-done-contract` — done evidence shape.
- `zf-harness-state-sync` — event/truth boundary.
- `zf-yoke-dev-worker-role-context` — worker scope and evidence discipline.
