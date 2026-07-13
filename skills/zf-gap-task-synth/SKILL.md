---
name: zf-gap-task-synth
description: "Use to synthesize bounded gap tasks from a failed verify/rescan result without duplicating the original task-map schema or reopening unrelated completed work."
---

# ZaoFu Gap Task Synthesis

## 输入源(触发)

gap task synth 的合法触发源不止失败的 `verify` / rescan 结果:Tier-2 的
`diagnosis.completed`(`next_action: route_to_lane`)也是合法触发源——其结论经
`candidate_rework` 的 feedback 管线回流 replan(`known_types.py`
`KNOWN_EVENT_TYPES` 注册、`runtime/diagnosis.py`)。以诊断报告触发时,lane 归属优先采纳报告的 `target_lane`。

## Task Shape

Each generated gap task must be a normal task-map task with:

- stable `task_id`;
- `owner_role` and `affinity_tag`;
- `parent_task_id` when it patches a previous task;
- `claim_paths` / `allowed_paths`;
- explicit acceptance criteria;
- focused verification commands;
- `source_refs` 优先锚定 verify report 的 `gap_findings` 条目(`finding_id` /
  `severity`)与 `requirement_coverage_matrix` 的 uncovered / partial 行
  (`requirement_id`);失败报告路径、source goal、runtime evidence 作补充锚点。
  verify report 由 event schema 强制 `requirement_coverage_matrix`(non_empty
  档位)与 `gap_findings` / `replan_recommendation`(`core/verification/event_schema.py`、
  `runtime/orchestrator_fanout.py`),canonical 锚点是这两处结构化条目,不要只泛写
  "the failing report";
- `goal_kind`, `gap_category`, and `gap_kind`(与下方 amend 封套 `goal-gap-plan.v1`
  的必填字段一致,内核校验,勿自由发挥)。

Do not synthesize vague tasks such as "finish web UI" without precise source
anchors and verification.

## Ownership

Keep gaps small and lane-friendly:

- reuse the original lane affinity when the same module owns the gap;
- use a new affinity only when the gap belongs to a different module;
- avoid two concurrent gap tasks owning the same exclusive root file;
- put root assembly/package files under an assembly/root task when needed;
- 以 `diagnosis.completed` 触发时,lane / affinity 归属优先采纳诊断报告的
  `target_lane`,而非默认沿用原 lane。

## Evidence Contract

Add an `evidence_contract` or source fields that preserve:

- `goal_id`, `goal_kind`, `gap_category`, `gap_kind`;
- `parent_task_id` and `affinity_tag`;
- `source_refs`;
- `repro_ref` and `acceptance_id` when available;
- `replan_history_ref`;
- `affected_tasks` and `gate_changes` when the replan changed expectations.
- `supersedes_task_ids` only when the semantic replan replaces failed tasks
  rather than appending missing work. Replacement task ids must be new; the
  kernel removes the superseded ids from the amended full task-map and records
  `task.superseded` during normal task-map adoption. Do not set this field for
  ordinary additive verify gaps.

The worker briefing must show this context before implementation.

## Emit Discipline

Gap task synthesis should produce artifacts first, then emit events. Do not
mark the goal done from the synth stage. Final closure belongs to verify/judge
after the amended tasks pass and the rescan report shows no open P0/P1 gaps.

Gap task 的完成必须产生新的 `target_commit` delta。FIX-15 后,同一审计对象
commit 的重开审会被 `fanout.retrigger.suppressed`(`reason:
no_delta_since_failure`)抑制——pin-commit 取失败时 `fanout.child.dispatched`
的 `target_commit`(`runtime/orchestrator_fanout.py`)。gap task 若不落新 commit,
后续 re-verify 直接判重抑制、成永久空转。因此验收命令必须能证明 delta 存在
(例如比对当前 HEAD 与失败时 pinned commit)。

Do not write directly to `events.jsonl`, `kanban.json`, `feature_list.json`,
`progress.md`, or `memory/`. Use artifacts plus the normal task-map amend event
path so Layer 1 remains the only runtime state writer。amend artifact 的封套
形状不由本技能自由发挥——遵循 `zf-goal-closure-replan-contract` 定义的
`goal-gap-plan.v1`(内核校验,见 `runtime/module_gap_plan.py` /
`runtime/goal_gap_plan.py`),含 `goal_kind` / `gap_category` / `gap_kind` 等必填字段。
