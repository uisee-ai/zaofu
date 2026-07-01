---
name: zf-harness-backlog-synthesis
description: "Use after design.critique.done verdict=approve to accept/merge arch candidate artifacts and synthesize the final task contract or plan/backlog artifacts (with 6 required_backlog_refs). Finalization is the orchestrator's exclusive job at stage ④ backlog."
---

# ZaoFu Backlog Synthesis

## When to use

You are the **orchestrator**. You just observed a `design.critique.done`
event with `verdict=approve` on a task currently assigned to the design
stage. The DAG specifies (workflow.dag.stage_order):

```
intake → design → design_critique → backlog → implement → ...
                                    ^^^^^^^
                                    YOU ARE HERE
```

`workflow.dag.design_to_backlog_owner: orchestrator` says **you own this
stage**. Arch may already have written draft/proposed full-stage plan,
backlog, and task-map artifacts; treat them as reviewed inputs, not final
runtime truth. Do not let any downstream worker start until you finish
synthesis or explicitly finish the plan-only artifact handoff.

## What NOT to do

- **Never** write task contract at intake (when handling `user.message`).
  The contract reflects arch + critic outcomes; at intake those events
  don't exist yet.
- **Never** assume kernel will auto-fill contract from arch.proposal.done.
  As of P0/K1 (docs/impl/22), the kernel intentionally does NOT auto-apply
  arch's proposal into kanban. Synthesis is your job.
- **Never** reject arch solely for producing a complete candidate
  plan/backlog artifact. That is allowed. What arch must not do is write the
  final implementation task contract or claim accepted runtime truth.
- **Never** dispatch an implementation writer role without the 6
  required_backlog_refs filled in. Kernel preflight (P2/K4) will reject the
  dispatch with task.contract.invalid.
- **Never** invent a `dev` stage for plan-only zf.yaml. If no implementation
  writer role exists, validate and publish the plan/backlog artifact instead.

## Task-map hard contract (multi-task splits — R25 lessons)

When your output is a multi-task `task_map.json` (refactor/greenfield
splits), four rules are admission/review killers. All four were hit in
real runs (hermes R24/R25 — zaofu
`docs/records/runs/2026-06-12-r25-issues.md` ISSUE-001/002 and the
round-1/2 review rejections):

1. **Exactly one assembly/integration task.** Its verification MUST be
   root-level build green PLUS one end-to-end boot. Per-package green is
   NOT root green — a workspace whose packages each pass their own tests
   can still fail `tsc -b` or fail to boot at the root.
2. **Root files are owned exclusively by the assembly task.** Root
   `package.json`, workspace manifest (`pnpm-workspace.yaml`), root
   `tsconfig*`, lockfiles: no other task may list ANY root-level path in
   `allowed_paths`. `allowed_paths` is write-scope — reading needs no
   declaration. (R25 ISSUE-002: 'package.json' double-held → writer
   fanout cancelled by the W1 exclusivity gate.)
3. **The assembly task MUST register EVERY new package in the workspace
   manifest** so per-package filter commands actually select them.
   Self-check: run one filter command and confirm the package is matched
   — "No projects matched" means broken. (R25 round-1 review: 4 of 5
   slices had no-op verification because the workspace listed only
   `packages/assembly`.)
4. **Backlog-style field completeness per task** — `task_id`,
   `affinity_tag`, `wave`, `allowed_paths`, `verification` (executable),
   `source_refs` (anchor back to the plan/scan section this task came
   from), `root_owner_class` (`slice|assembly|scaffold|none`). The
   backlog-style structure constrains the JSON fields; the deliverable
   is ALWAYS `task_map.json` — markdown is at most your drafting form,
   never the artifact the kernel consumes.

Pre-flight self-check before emitting `task_map.ready`:

```text
[ ] exactly one task with root_owner_class=assembly
[ ] no non-assembly task lists a root-level path in allowed_paths
[ ] workspace manifest registers every packages/* this map creates
[ ] every task: verification is an executable command, not prose
[ ] every task: source_refs anchors back to plan/scan (no orphan tasks,
    no invented tasks beyond the plan)
```

## Inputs you have

| Source | Where to read |
|---|---|
| User intent | The original `user.message` event in events.jsonl |
| Plan candidates | `artifact.manifest.published` / `task.artifact_refs.updated` refs from arch, especially `spec`, `sdd`, `plan`, `implementation_plan`, `process_plan`, `backlog_plan`, `backlog`, `task_map`, `tdd`, `test_plan` |
| Arch summary | `arch.proposal.done` event payload (.file_plan / .test_plan / .summary / .artifact_refs / .evidence_refs) |
| Audit | `design.critique.done` event payload (.verdict / .risks / .fix_items / .evidence_refs) |
| Current state | kanban.json + git refs |

## The 6 required_backlog_refs

Every implementation task contract MUST contain these 6 fields:

| Field | Source | Format |
|---|---|---|
| `spec_ref` | reviewed spec/sdd artifact, or user.message.payload.spec_refs | path string, e.g., `"docs/specs/phase-1/runtime-foundation.md"` |
| `plan_ref` | orchestrator-final plan/process artifact, often accepted or merged from arch draft/proposed refs | path string, e.g., `"docs/plans/run-state-machine-plan.md"` |
| `tdd_ref` | reviewed tdd/test_plan artifact or arch.proposal.done.payload.test_plan | path string or compact dict, e.g., `"docs/plans/run-state-machine-tdd.md"` |
| `critic_event_id` | design.critique.done.id | string, e.g., `"evt-5f58a46b90c3"` |
| `critic_gate_ref` | verdict + key fix_items summary | string, e.g., `"approve: import type + deep freeze applied"` |
| `evidence_contract` | arch test_plan + critic verification recommendations | dict, e.g., `{"static": "pnpm tsc -b --noEmit", "runtime": "pnpm vitest run test/..."}` |

## Synthesis procedure (4 steps)

### Step 1: Read arch + critic events and candidate artifacts

```bash
# Find arch.proposal.done for this task
arch_event=$(zf events --task "$task_id" --type arch.proposal.done | tail -1)
critic_event=$(zf events --task "$task_id" --type design.critique.done | tail -1)
```

Parse out the relevant fields and artifact refs. The events are JSONL — use
Python stdlib if `jq` is unavailable. Prefer manifest/index refs over chat
transcript. Valid arch candidate artifact statuses are `draft`, `proposed`, and
`accepted`; `rejected` / `superseded` refs are not eligible for final contract
refs.

If critic approved the candidate package and the artifacts are already good
enough, use their paths as final refs. If they are fragmented, merge them into
orchestrator-final artifacts first, publish a new manifest, then use those
paths in the contract.

### Step 2: Build the contract.json

Always use Python heredoc (jq may not be present, Node may not be present).

```bash
mkdir -p .zf/tmp
python3 - <<'PY'
import json
contract = {
    "contract": {
        # Behavior (from arch summary)
        "behavior": "<arch.summary>",
        # Scope = arch.file_plan, raw relative paths only
        "scope": [
            "packages/state/src/run-state-machine.ts",
            "test/unit/state/run-state-machine.test.ts",
            "packages/state/package.json",
        ],
        # Verification command (executable preferred)
        "verification": "PATH=... pnpm install --frozen-lockfile && pnpm exec vitest run test/unit/state/run-state-machine.test.ts && pnpm tsc -b --noEmit && pnpm biome ci",
        "verification_tiers": ["static", "runtime"],
        # Acceptance = arch acceptance + critic.fix_items converted to clauses
        "acceptance": "1) ... 2) ... (critic fix #1: ...) (critic fix #2: ...)",
        # Exclusions (from critic.risks where applicable)
        "exclusions": [
            "do not modify pnpm-lock.yaml",
            "do not modify root package.json",
        ],
        "owner_role": "<implementation-role-from-zf.yaml>",
        "handoff_artifacts": [
            "packages/state/src/run-state-machine.ts",
            "test/unit/state/run-state-machine.test.ts",
        ],
        # 6 required_backlog_refs (THIS IS THE KEY PART):
        "spec_ref": "docs/specs/phase-1/runtime-foundation.md",
        "plan_ref": "docs/plans/run-state-machine-plan.md",
        "tdd_ref": "docs/plans/run-state-machine-tdd.md",
        "critic_event_id": "evt-5f58a46b90c3",
        "critic_gate_ref": "approve: 4 fixes applied (import type / phase_gate / deep freeze / preserve export)",
        "evidence_contract": {
            "static": "pnpm tsc -b --noEmit; pnpm biome ci",
            "runtime": "pnpm vitest run test/unit/state/run-state-machine.test.ts"
        }
    }
}
open('.zf/tmp/contract.json', 'w', encoding='utf-8').write(json.dumps(contract, ensure_ascii=False))
PY
```

### Step 3: Emit task.contract.update

```bash
zf emit task.contract.update --task "$task_id" --payload-file .zf/tmp/contract.json
```

This updates kanban.json with the synthesized contract. Kernel's
`apply_sprint_contract_event` handles this (legitimate housekeeping — it's
responding to a Layer-2 directive, not auto-projecting).

### Step 4: Assign to the configured implementation role

```bash
zf kanban assign "$task_id" <implementation-role-from-zf.yaml>
```

(Or if you decided to fanout, repeat Step 2-4 for each sub-task with
disjoint scope; see Fanout below.)

### Plan-only topology

If zf.yaml has no implementation writer role after `design_critique`, stage ④
ends with deterministic artifact handoff instead of worker dispatch. The
orchestrator publishes a final manifest; Layer 1 promotes, validates, and closes
the task. Do not manually emit `discriminator.passed`, `task.done.evidence`, or
move the task to `done`.

```bash
# 1. Produce or accept final repo-relative artifacts under docs/** / tasks/**.
#    If you write them in the role workdir, keep the manifest path repo-relative
#    and include workdir_path when needed so the kernel can promote to project root.
plan_path="docs/plans/<final-plan-artifact>.md"
backlog_path="docs/plans/<final-backlog-artifact>.md"

# 2. Validate the final backlog artifact before publishing the manifest.
zf spec validate --strict "$backlog_path"
zf spec ingest --dry-run "$backlog_path"

# 3. Prefer the deterministic manifest helper instead of hand-writing sha256.
zf artifact manifest create \
  --task "$task_id" \
  --role orchestrator \
  --status accepted \
  --kind implementation_plan="$plan_path" \
  --kind backlog_plan="$backlog_path" \
  --output .zf/tmp/final-manifest.json

# 4. Publish artifact.manifest.published(actor=orchestrator) with:
#    artifact_refs: [{kind,path,sha256,summary,status:"accepted",...}]
#    handoff_contract.backlog_ref = "$backlog_path"
zf emit artifact.manifest.published --task "$task_id" --payload-file .zf/tmp/final-manifest.json
```

Do not assign `dev`, `review`, `test`, or `judge` in a plan-only topology.
After the final manifest event, expect the kernel to emit
`artifact.promote.completed`, `discriminator.passed`, `task.done.evidence`, and
`task.status_changed(to=done)` if validation passes. If promotion or validation
fails, the kernel emits `artifact.promote.blocked` / `task.done.blocked` and the
task stays non-terminal for bounded rework.

## Fanout decision tree

When does a single arch proposal warrant N parallel implementation tasks?

**Fanout if ALL of these**:
- arch.file_plan touches ≥ 2 packages
- packages are independent (no shared file like package.json edits)
- expected implementation work per slice >= 30 min

**Don't fanout if ANY**:
- file_plan touches ≤ 3 files in same package
- shared file edits unavoidable (root package.json, pnpm-lock.yaml)
- one slice depends on another (e.g., F-tools-builtin depends on F-tools-loop)

**Fanout procedure**:

```bash
# create sub-tasks for each slice
sub_task_1=$(zf kanban add "$feature_id" "Subtask: package-A stub" --id-only)
sub_task_2=$(zf kanban add "$feature_id" "Subtask: package-B stub" --id-only)
# Each gets its own contract with disjoint scope but SAME 6 backlog refs:
#   spec_ref / plan_ref / tdd_ref / critic_event_id / critic_gate_ref / evidence_contract
# Then assign each:
zf kanban assign "$sub_task_1" <implementation-role-from-zf.yaml>
zf kanban assign "$sub_task_2" <implementation-role-from-zf.yaml>
```

Kernel will dispatch them in parallel to the configured writer role pool.

## Affinity lane task-map requirements

When the selected `zf.yaml` uses `fanout_writer_scoped` with
`fanout.assignment.strategy: affinity_stage_slots`, the orchestrator-final
task-map MUST include enough metadata for deterministic lane assignment:

| Field | Required | Purpose |
|---|---:|---|
| `task_id` | yes | stable lane child / task identity |
| `affinity_tag` | yes | maps module/domain to a lane profile key |
| `wave` | yes when product-delivery waves are used | controls `product_delivery.wave.ready` batching |
| `allowed_paths` | yes | worker scope and protected write gate |
| `exclusive_files` | strongly recommended | parallel independence proof |
| `shared_files` | optional | read-only shared context; not a write grant |
| `blocked_by` | yes when dependent | prevents false parallelism |
| `verification` or `evidence_contract` | yes | lane completion evidence |
| `owner_role` / `owner_instance` | recommended | audit and fallback routing |

Before dispatch, check:

- every parallel writer task has disjoint write scope;
- tasks that share mutable files are serialized or split differently;
- every task with `affinity_stage_slots` has the configured affinity key
  (usually `affinity_tag`);
- lane workers will receive `zf-harness-lane-goal-continuation` in their
  briefing or role skill roster.

If any required lane metadata is missing, do not guess. Re-dispatch arch or
critic for a corrected task-map, or emit a blocking contract update.

## Worked example (cangjie F-952f2065 RunStateMachine round)

1. user.message says "build RunStateMachine"
2. arch emits artifact.manifest.published + arch.proposal.done with candidate
   plan/test refs and file_plan=[3 files in packages/state]
3. critic emits design.critique.done verdict=approve (after v2)
4. orchestrator synthesizes contract.json with:
   - spec_ref = "docs/specs/phase-1/runtime-foundation.md"
   - plan_ref = "docs/plans/run-state-machine-plan.md"
   - tdd_ref = "docs/plans/run-state-machine-tdd.md"
   - critic_event_id = "evt-5f58a46b90c3"
   - critic_gate_ref = "approve: 4 fixes applied"
   - evidence_contract = {static: tsc + biome, runtime: vitest}
5. zf emit task.contract.update + zf kanban assign `<implementation-role-from-zf.yaml>`
6. Single implementation task (scope small enough; no fanout)

The configured worker gets a briefing with all 6 refs, builds, and hands off
to the next zf.yaml stage.

## Verification

After your synthesis, the worker briefing must contain references to all 6
fields. If kernel returns `task.contract.invalid`, you missed a field —
read the event payload's `errors` list and re-emit task.contract.update
with the missing fields.

## Anti-patterns

- ❌ Synthesizing contract before critic.approve
- ❌ Dispatching any downstream worker before emitting task.contract.update
- ❌ Inventing a `dev` stage when zf.yaml is plan-only or uses another role name
- ❌ Treating arch draft/proposed artifact status as automatic runtime truth
- ❌ Empty / placeholder strings for any of the 6 refs
- ❌ Fanout with overlapping scope between sub-tasks (causes candidate.conflict)
- ❌ Ignoring critic.fix_items when building acceptance
