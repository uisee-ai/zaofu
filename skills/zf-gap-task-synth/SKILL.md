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
- `verification_read_paths` when focused verification reads stable tests,
  scripts, fixtures, or manifests outside the task's writable claim;
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

Treat `allowed_paths` as write authority. Do not widen it merely to admit a
verification command. Before emit, extract every repository path referenced by
the final commands and prove that each is covered by the task write scope, a
live sibling task's scope, or explicit `verification_read_paths`. The read-only
field does not grant the worker permission to modify those paths.

Classify command paths before emitting the plan:

- an absolute command executable and the value of `--state-dir` are host/runtime
  context, not repository read claims;
- every repository test, script, fixture, manifest, or imported helper named by
  a command still belongs in write scope, a live sibling scope, or
  `verification_read_paths`;
- mechanically compute
  `repo_paths(verify_commands) - writable_scope - live_sibling_scope` and copy
  the complete remainder into `verification_read_paths` before emitting
  `flow.gap_plan.ready`;
- after an admission rejection, preserve the original semantic gap and correct
  only the rejected task-map shape. Do not rerun broad discovery merely to add
  a missing read claim.

## 恢复提案就绪检查

对新增的每个任务（包括不声明 `supersedes_task_ids` 的前置任务），在提交前
同时对账执行输入、写入权限和验证责任：

- 当前输入包使用按任务选择时，显式声明
  `evidence_contract.plan_input_selection` 的 Impl/Verify 消费范围；按
  `zf-verify-rescan-replan` 核对前驱需求及新增义务是否完整覆盖。新任务 ID
  不等于新需求正文，使用受控前驱关系继承分区，不能省略选择或复制全量输入。
- 逐项核对 `evidence_contract.required_artifacts` 与 `allowed_paths`。
  本任务需生成的证据必须有精确写入范围；只读来源不扩为可写。若验收要求
  发布控制面 Plan/input，由 Planner 经受控产物链路完成后再派发消费者，
  不把控制面状态目录加入业务 worker 的写入范围。
- 从已绑定合同继承完整 `verification_commands` 对象。新增命令显式声明
  command ID、AC 对应、owner、tier 及确定性/可复用/超时属性；当当前合同要求
  rolling smoke 时，选择实际能在 worker 和集成 Candidate 重跑的本地命令。
  不能给需要外部环境的验收虚标 `rolling_smoke`，也不能删除原最终集成门。
- 全图中共享 command ID 只表示同一验证义务；不同任务的 AC/owner 不同时，
  选择唯一 ID，并同步验收映射及矩阵。不得用改名伪造已存在的执行收据。

准入拒绝后，沿用原缺口证据，针对返回的具体字段修订当前草稿并复查以上
关联面。若缺少上游输入或权限，报告具体责任者与所需产物；重复提交相同
不完整提案不能构成恢复进展。

## Split-quality Preflight

Before emitting a gap plan, read the effective workflow work-unit contract from
the current briefing/effective config. When
`workflow.work_units.split_quality.max_acceptance_criteria` is non-zero, every
generated task must stay at or below that limit.

Treat this as a shape constraint, not permission to drop requirements:

- merge criteria that prove the same acceptance domain into one explicit
  clause while retaining all commands/evidence anchors;
- if independent acceptance domains still exceed the limit, split them into
  non-overlapping tasks with explicit ownership and dependency closure;
- never truncate criteria, hide requirements in prose, or weaken coverage just
  to pass admission;
- count the final `acceptance_criteria` array mechanically before emit and
  revise the artifact while it is still local.

The same preflight applies to replacement tasks synthesized after verify,
semantic replan, or admission feedback. Include the admission reason in the
next attempt's source anchors so a repeated shape rejection is auditable.

## Ownership

Keep gaps small and lane-friendly:

- reuse the original lane affinity when the same module owns the gap;
- use a new affinity only when the gap belongs to a different module;
- avoid two concurrent gap tasks owning the same exclusive root file;
- put root assembly/package files under an assembly/root task when needed;
- 以 `diagnosis.completed` 触发时,lane / affinity 归属优先采纳诊断报告的
  `target_lane`,而非默认沿用原 lane。

## Gap-only Dependency Closure

Treat the pinned candidate/target as the dependency baseline. A prior task whose
accepted implementation is already present in that immutable target is completed
input, not an outstanding dependency:

- cite it through `source_refs`, candidate refs, or accepted task-ref evidence;
- do **not** copy it into `blocked_by` / `dependencies` merely because it preceded
  the failed task in the original task map;
- use `blocked_by` only for a task in the same amendment that will actually be
  dispatched before this task, or for a task the current briefing/runtime
  explicitly lists in `completed_task_ids` / active runnable scope;
- when replacing a failed task, put the replaced id in `supersedes_task_ids`, not
  in `blocked_by`.

Bind replacement identity to the **current** `task_map_ref`, not to an older
amendment or failure report:

- load the task ids from the current task map before choosing a replacement;
- every `supersedes_task_ids` value must exist in that current task map;
- the replacement `task_id` must be new and must differ from every superseded
  id;
- record older task generations only in `source_refs` / `replan_history_ref`;
  do not carry their ids forward as current supersede targets.

Every replacement task with non-empty `supersedes_task_ids` must also declare
an explicit immutable `base_commit` at task top level. Select the commit that
already contains all accepted work the successor must preserve, and bind that
exact full commit id through a `git:<base_commit>` entry in task `source_refs`.
Do not leave the baseline only in acceptance prose, infer it from source-ref
ordering, or fall back to the original run target. Additive gap tasks without
`supersedes_task_ids` do not use this successor rule.

Do not confuse the continuation checkout with an older comparison baseline.
When the current canonical task has a `dev.build.done` or self-check checkpoint
whose required receipts passed, and a later blocker only concerns task-map
identity, scope/base metadata, admission, or handoff, use that verified
`source_commit` as the successor `base_commit`. Keep an older commit used for
unchanged-tree, compatibility, or ancestry comparison inside the relevant
verification command. Reusing the older comparison baseline as `base_commit`
would discard verified work and force a needless rebuild. If the blocker
implicates product behavior or the receipts are incomplete, do not promote the
failed delivery checkpoint; retain the last independently accepted commit.

When one failed task is replaced by multiple gap tasks, model a graph-node
replacement rather than copying `supersedes_task_ids` onto every new task:

- declare `blocked_by` between every predecessor and successor; list order is
  not a dependency contract;
- exactly one terminal task in that replacement subgraph carries
  `supersedes_task_ids`, `base_commit`, and `git:<base_commit>`;
- additive/predecessor tasks do not supersede the old task;
- the kernel mechanically inherits the old task's incoming dependencies onto
  the replacement root and rewires old downstream dependents to the terminal
  successor;
- if the subgraph has multiple terminals, revise the task graph before emit.

For example, `API gap -> Web rebuild` replacing an old Web task means the Web
rebuild blocks on the API gap and is the sole task that supersedes the old Web
task. Do not emit two unrelated replacements and rely on array order.

A continuation or retry that deliberately reuses the same `task_id` must use
the same top-level `base_commit` + `git:<base_commit>` binding whenever it
starts from an accepted checkpoint. Do not invent aliases such as
`implementation_base_commit`; downstream writer snapshots consume only the
canonical `base_commit` field.

Before emit, assert both set checks mechanically:

```text
set(supersedes_task_ids) <= set(current_task_map_task_ids)
replacement_task_id not in set(current_task_map_task_ids)
base_commit in full_git_commit_ids
"git:" + base_commit in source_refs
```

If either check fails, revise the gap task identity before submission. A mixed
current-plus-historical supersede list is invalid even when the semantic gap is
otherwise correct.

Before emitting, simulate `resume_scope=gap_tasks_only`: every dependency must be
either part of the emitted gap task set or explicitly proven complete by the
current runtime contract. If a dependency would only remain in the historical
full task map, revise the gap plan before submission; otherwise the child can be
admitted but remain permanently queued.

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

## Candidate-safe Git Evidence

Generated acceptance and verification commands must remain valid after the
kernel assembles task commits into a candidate. Candidate integration uses
patch-equivalent cherry-picks, so worker/source commit hashes are provenance
identities, not candidate commit identities:

- never require a worker `source_commit` (or one of its parents) to equal the
  candidate `HEAD` / `HEAD^`;
- never place a not-yet-created candidate commit in a worker-owned receipt;
- bind worker provenance through the task ref, source commit, contract
  snapshot, patch/tree or owned-path digest, and command receipts;
- obtain the candidate ref/head only from the kernel candidate event or
  manifest, then verify that the candidate contains the required patch/tree
  and evidence;
- when one command is declared as a task/candidate quality gate, preflight it
  conceptually against both the worker checkout and a cherry-picked candidate
  checkout. A command that depends on unchanged commit hashes is invalid.

Use `git rev-list --cherry-pick` / patch equivalence for integration identity,
or compare the declared owned paths and their digests. Exact commit equality is
appropriate only inside the same immutable ref namespace. This is a task
contract rule, not permission for an agent to write candidate refs.

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
