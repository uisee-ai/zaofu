---
name: zf-prd-plan-synth
description: "Use for ZaoFu PRD workflows. Defines scan, planning, configured review, and the dispatchable plan contract for product delivery."
dependencies: [zf-plan-execution-readiness]
---

# ZaoFu PRD Plan Synthesis

## Stage Contract

For the standard `PrdFlow` compiled by `workflow_profiles.py`, follow the
actual dispatch briefing and configured role names:

1. `prd-scan`: `product-scan` and `tech-scan` read in parallel; the Kernel
   waits for both and emits `prd.scan.completed`.
2. `prd-plan`: `planner` produces the complete Plan, task map and matrices.
3. By default, `plan-critic` independently reviews the admitted Planner
   candidate. Explicit `plan_review` selects none, targeted or full with a reason;
   targeted also names the review questions and does not imply whole-document approval.
   When `plan_review.mode: none` or `planCriticEnabled: false` is configured,
   Planner performs cross-artifact reconciliation and submits its own complete
   candidate; the Kernel admits the package before writer dispatch. This is
   Planner self-check, not independent review. Record missing evidence and do
   not claim an independent Critic verdict.
4. When enabled, human Plan approval precedes product writer dispatch.

Use the configured scope and the input policy; supplementary ports remain available
for controlled reads. Follow the dependency's review-scope method for first targeted
review, evidence-bound incremental review and finding-linked rework.

The default Plan stage has Planner and Critic; the explicitly configured
no-Critic route has Planner alone. The Kernel is deterministic orchestration,
not an additional Agent. When configured, Critic reviews the exact Planner
candidate and must not run as a parallel peer.

The author/PRD-review/task-map handoffs below also apply when an explicitly
configured workflow separates PRD authoring. They do not add `prd-author`,
`prd-critic`, or `task-map-synth` to the standard PrdFlow.

## PRD Author Output

编写或评审 PRD 时读取[产品阅读与证据方法](references/product-reading-evidence.md)，
将摘要、正文、设计和追溯组织为同版权威材料的阅读路径。存在设计问题时，同时读取
依赖技能中的[设计调研与交接](../zf-plan-execution-readiness/references/design-research-handoff.md)。
按适用性展开，不增加固定章节、角色或 Runtime 语义门禁。

Channel PRD 输入先消费同版 `implementation_companion` 的来源基线、评审覆盖、验收数据
计划和 handoff 索引，以及独立评估报告（如已提供）。复核报告的 PRD ref/digest；旧版
checklist 不证明新版通过。执行 `zf-requirement-reconciliation` 的五项质量闭环。
将已定 Requirement/AC/命令映射到 `prd-plan.md`、`task_map.json` 和 `source_index.json`，
记录尚未生成项的生产者、触发条件和范围；这些是 Spec-kit spec/plan/tasks 的阶段对应，
不另建第二份 Task Store。完整 plan/tasks 产生后由配置的 Critic 做跨工件对账；未配置
Critic 时由 Planner 对账并明确是自检，保留人工审批。首次 PRD 不以
未来文件、可选 assess 扩展或固定文件名为准入条件。TBD 仅阻塞其影响的当前动作，
产品验证未执行不得转写为通过，独立评估也不能代替 Owner 执行授权。

Write `prd.md` before emitting `prd.ready`. The PRD must be a durable artifact
under the project root, normally `docs/prds/` or `docs/plans/`. Do not emit a
memory-only PRD or mention the path only in prose.

Emit top-level:

```json
{
  "prd_ref": "docs/plans/example-prd.md",
  "artifact_refs": ["docs/plans/example-prd.md"],
  "evidence_refs": ["<source or research refs>"]
}
```

`artifact_refs` must include `prd_ref`. `evidence_refs` must be non-empty and
should point to channel messages, research fanout outputs, issue links, user
requirements, or source facts used to write the PRD. These fields may also be
duplicated inside `report`, but downstream runtime and critic stages consume the
top-level payload.

The PRD should define users, problem, non-goals, acceptance criteria, release
risks, and verification expectations.

## Critic Output

Review `prd_ref`, `artifact_refs`, and `evidence_refs` from the trigger event
and emit a concise verdict. If the PRD is not reviewable, emit the configured
failure event with the missing artifact reason. Do not approve memory-only PRD
prose or a PRD event whose `artifact_refs` does not include `prd_ref`.

## Task-Map Synth Output

After PRD approval, write:

- `prd-plan.md`: human implementation plan.
- `task_map.json`: dispatchable vertical slices.
- `source_index.json`: PRD and evidence index.

Plan 正文面向审批与执行交接，默认沿用确认需求的语言；代码标识、命令、路径和
固定机器键保持原样。小需求按适用性表达完整语义，不按模板补造页面、数据模型、
并行 wave 或业务指标。推荐阅读顺序：

- 简短审批摘要：本次交付、变化、范围、主要风险、待决条件及批准后的动作；
- Task Spec：本片行为与失败不变项、真实入口、依赖/写入责任、准备/恢复、验收与交接；
- 共享合同：仅在确有共用内容时集中定义一次，任务引用具体条款及本片例外；
- 工程附件：Task Map、来源/AC/测试矩阵的准确引用和未覆盖项，完整追溯不重复铺满正文。

PRD/SDD/TDD 是语义覆盖要求，不强制使用对应字面标题或重复章节。单任务可使用
一张责任表和少量段落；保持来源、命令、证据与决策可取得。身份、摘要、完整性和
授权仍由既有工件及准入规则约束，缩短正文不删除任何强制验收或更改机器合同。
审批前不执行实施和产品验收；通用运行协议引用当前 briefing/Skill，正文仅写任务特有义务。

The approval entry is a contract, not a formatting preference. For
`derived_plan_full`, the Planner candidate and any configured Critic handoff must bind these semantic
fields through existing plan ports or explicit evidence refs:

- **PRD**: users, problem/outcome, scope, non-goals, success measures, and
  authority anchors;
- **SDD**: system slices, entrypoints, input/output or data/event contracts,
  ownership, dependencies, and failure semantics;
- **TDD**: acceptance ids, expected result, command id, verification owner and
  tier, evidence path, pass/fail rule, and current evidence state;
- **Decision Register**: stable decision id, question, options or recommendation,
  affected tasks, blocker impact, and current disposition.

Do not claim `derived_plan_full` when a section is represented only by a task
title, a source excerpt, or a command name. Preserve the missing contract as a
blocking finding, including when Planner is the only plan role. The operator may render a read-only dossier from these
bound artifacts, but Runtime must not invent project semantics or silently
select an owner decision.

## Semantic Coverage Contract

`derived_plan_full` is a semantic coverage claim, not a heading or row-count
claim. Before emitting it, build a coverage ledger in the existing requirement,
acceptance, or review artifacts; do not create a second task schema. Classify
the confirmed authority into the applicable source units:

- product outcome, scope, and non-goals;
- domain, module, capability, and user journey;
- behavior rule, state transition, input/output or data/event contract;
- integration, permission, failure, recovery, concurrency, performance, and
  other non-functional constraints;
- acceptance, test, real-E2E, and provider-qualification obligations.

Each source unit must have exactly one current disposition:
`covered`, `explicit_non_goal`, `pending_decision`, or `out_of_scope`. A
`covered` row keeps a stable source section/heading anchor, a meaningful
statement, the Plan section, requirement/acceptance ids, owning task(s), test
or command ids, evidence refs, and the owner or next stage. A non-goal,
pending decision, or out-of-scope row keeps its reason and impact; it cannot be
silently omitted. If the authority does not define domains or modules, record
that fact and the chosen grouping rationale.

Chapter-level refs may index an authority bundle, but they are not sufficient
as the sole proof for every behavior row. Prefer stable section/heading anchors
with a short excerpt or digest. Do not infer module coverage from task names,
file counts, or a shared statement such as “待执行/待审批追踪项”. Any
mandatory source unit without a disposition, semantic Plan body, producer, or
verification path is a blocking finding and blocks `derived_plan_full`.

Return `plan_content_profile: "derived_plan_full"` only when those sections and
the exact confirmed authority have been reviewed. Use `summary_only` when the
output is intentionally abbreviated; the operator UI must label that result as
a summary and keep it out of the full Plan approval path. Also return
`authority_refs` and `requirement_spec_ref` when the source authority is
available so the owner can open the original PRD beside the derived Plan.

For a source PRD that defines formal task or acceptance mappings, preserve the
mapping as data instead of paraphrasing it away. Keep the authority's exact
task boundaries, acceptance ownership, and source anchor that proves each
association. Do not round-robin acceptance ids, infer a new owner from task
order, or replace a requirement statement with a shared placeholder such as
“待执行/待审批追踪项”. Every trace row must retain its source id, a meaningful
requirement statement, owning task(s), and evidence status. If the source
mapping cannot be recovered, mark the Plan as needing rework and do not claim
`derived_plan_full`.

When the authority includes UI/UX acceptance, carry the named screens and
interaction boundaries into the relevant journey and integration task. Keep
product-specific screen names, page counts, interaction rules, task numbering,
and acceptance categories in the confirmed authority or project adapter; a
generic Plan skill must not invent them. “Page integration” without the named
boundaries supplied by the authority is an incomplete task slice.

Emit top-level `plan_artifact_ref`, `task_map_ref`, `source_index_ref`,
`artifact_refs`, and `evidence_refs`.

The generated `task_map.json` must follow `zf-plan-task-map-contract`: every
task needs singular `source_key`, `source_ref`, and `source_excerpt` anchors.
Optional `source_keys` / `source_refs` add evidence but do not replace those
primary fields. If provenance lives in `source_index.json`, every `task_id`
under `tasks[]` or `task_sources[]` must carry the same singular anchors.

Read the exact confirmed requirement/PRD authority artifact before slicing.
When it explicitly names task boundaries, task ids, dependencies, AC ownership,
or a confirmed/recommended workflow, preserve that decision map exactly unless
the current owner request explicitly asks for a redesign. A smaller task count
or a cleaner-looking merge is not permission to collapse those boundaries.
Derived matrices and scan summaries do not override the authority artifact.

## Human Revision Contract

The Preview is a review input and does not mutate the canonical Plan. A human
comment or change request must identify the current Plan revision and at least
one target source anchor, requirement/acceptance id, decision id, or Plan
section. The next replan records the requested change, old/new semantic diff,
affected decisions, tasks, dependencies, acceptance, and evidence, while
preserving unaffected completed tasks and their refs. Approval applies only to
the current revision; a change that affects scope, contract, task ownership,
dependencies, or acceptance invalidates the previous approval and requires a
new Critic verdict when configured, and owner approval. Do not silently edit a preview or reuse
an approval after its covered content changed.

For greenfield PRD builds, include top-level `schema_version: "task-map.v1"`,
`target_root`, and `shared_conventions` before emitting success. At minimum,
fix `test_path_prefix`, package/workdir root, package import name, and any
packaging/scaffold file such as `app/pyproject.toml`. These values must be
repo-relative even when verification commands run through `cd <target_root>`.

Verification execution-context rule (ZF-E2E-RACING-P2, 2026-07-11): each
task's structured `verification` command is machine-executed from the
repository root — it must run as-is from there. If it depends on
`target_root` or another subdirectory, embed the directory in the command
itself (`cd <subdir> && …` or an equivalent flag); a bare `npm test` with
`package.json` under `app/` fails from the root and burns rework rounds.
Keep the structured command identical to the one stated in the acceptance
text.

## Human Review Before Approval

For product specification and technical-design sufficiency, apply
`zf-plan-execution-readiness/references/spec-design-readiness.md`: organize
user stories by value with observable success criteria, resolve the contracts
needed by the current implementation frontier, and retain later obligations.
Reuse authoritative source bodies through precise refs; an index alone is not
a synthesized specification. The configured reviewer, or Planner when no
Critic is configured, reports each dimension and actual scope before making an
explicit content-completeness claim.

For the standard PRD Plan, apply `zf-plan-execution-readiness` and its
`references/human-review.md` before submission. Lead the accepted Plan with
user goals, scope/conditional scope, phase outcomes and owner decisions;
follow with module specifications and concrete acceptance scenarios. Keep
engineering repair history in appendices. When execution_spec is already
used, populate its existing human_review fields before submission: place shared reading material in `shared.human_review` and module
specifications/scenarios in each existing `tasks[].body.human_review`.
Preserve canonical task/AC/command IDs and bind these artifacts in the same
Plan Package. The configured reviewer checks semantic consistency; the UI only renders the bound
content. For routes without execution_spec, keep the same reading sections in the
accepted Markdown; the reader quotes those sections without inventing a second
specification. Store atomic Given/When/Then cases in the existing
`test_matrix.tests[]` with task_id, acceptance_ids, command_ids, source_refs and
explicit evidence_status. Do not create a parallel scenario list just for UI.
Persist the complete decision register in `task_map.owner_decision_items`
(including deferred, non-blocking and pending-validation items), not only in
the role's control result. An explicitly empty list means the author reviewed
and found no decisions; a missing field means unknown. The Critic checks the
same IDs/statuses against the accepted Markdown and the bound task map;
its own empty action-question list must not be used as the product register.
Compare the complete ID sets before comparing statuses: a nonempty prose
register with an empty task-map register is a missing-record finding even
when the author says there are no new product-scope choices.
Neither a page count nor repeated RED/GREEN prose proves clause
coverage. Never rewrite admitted artifacts to improve presentation.

Before every resubmission, including an environment-only Critic rework, apply
`zf-plan-execution-readiness/references/spec-revision-review.md` and put its
`## 本次变更` reading entry in the new bound Plan. Identify the previous candidate,
changed consumers and preserved obligations; a larger replacement specification
does not itself explain the revision.

Avoid duplicating complete port bodies in both files and the control result.
For `source_inventory`, `capability_matrix`, `acceptance_matrix`, `test_matrix`,
`real_e2e_matrix` and `source_index`, write each complete JSON file once and use
the existing top-level `<name>_ref`, also listing that file in `artifact_refs`.
Do not repeat that port as an inline field or `plan_ports[].body`. These six
reference routes are supported by candidate preflight, Critic handoff and
Package materialization; consumers still read the complete bound artifact.
Keep other required bodies and every configured required read. Do not infer
new reference fields, shorten semantic contents, or omit missing files to save tokens.

For artifact writes containing source paths, prefer native Write/apply_patch with explicit
workdir-relative targets. The shell guard cannot distinguish arbitrary Python data
from writes. If rejected, inspect the target and use the explicit-target tool; never
weaken the guard, splice paths to evade it, or edit canonical runtime state. Use only
the current Kernel-declared result scratch for result submission.

## Task Design

Apply `zf-plan-execution-readiness/references/task-spec-slices.md` for task-local
behavior, shared references, setup/cleanup, RED/GREEN and reading partitions;
perform `references/final-package-review.md` from that same skill on the final
serialized candidate. Keep findings and their disposition in the existing review
artifact. These referenced methods are the single method source; do not repeat
their full checklist in every task or re-read unchanged bodies already read in
this turn. This does not waive required inputs or independent Critic review.

Split by end-to-end user journey, not by component-only layers. Each normal
slice should own the smallest runnable behavior across the API/runtime/web
layers it needs, plus its focused tests and evidence. A good task has:

- owner role such as `dev-api`, `dev-runtime`, or `dev-web`;
- `allowed_paths` and `exclusive_files`;
- dependencies and wave;
- acceptance criteria tied to the PRD;
- verification commands for product, API, and web verify agents.

Every mandatory user journey must map to one or more bounded tasks and exact
acceptance ids. Do not create independent "backend", "frontend", and "tests"
tasks that cannot demonstrate a user-visible outcome on their own. When more
than one slice feeds the same product, preserve one final assembly owner that
owns shared wiring and the real product entrypoint.

Each `exclusive_files` path has one owner across the full map. A later wave or
dependency does not permit duplicate ownership; assign shared entrypoint and
scaffold edits to one assembly/owner task.

For small products, a serial task map is often better than parallel horizontal
tasks:

- Wave 1: one scaffold task owns package metadata, directory layout, and
  conventions documentation.
- Later waves: feature tasks own their production files and matching tests.
- Final wave: one wiring/assembly task owns the real entrypoint and any package
  scaffold it changes, sets `root_owner_class: "assembly"` when more than one
  bundle feeds it, depends on prior feature tasks, and verifies the assembled
  product through the real entrypoint plus the full suite.

An explicitly declared assembly task needs an `owner_role` distinct from its
dependency feature owners, even when waves are serial. Keep its task id as the
`product_acceptance_spec.assembly_owner`; do not confuse task ids with role labels.

Do not emit a task map that places implementation in one task and its tests or
entrypoint smoke in another. Do not use `tests/...` paths when the declared
test prefix is `app/tests/...`; all owned paths stay repo-relative.

## Acceptance Evidence Satisfiability

Before emitting `task_map.json`, build an AC-to-evidence ownership matrix for
every mandatory acceptance criterion. Each row must identify:

- acceptance id and verification tier;
- the task that can produce the behavior;
- the task or Verify role that produces each required artifact/receipt;
- command ids and the real runner/method they invoke;
- configuration, specification, fixture, and output paths that must be
  writable;
- proof that those paths are covered by the producer task's `allowed_paths`,
  or by an explicit upstream dependency that already materializes the
  artifact.

Reject or revise the task map before handoff when a task is responsible for
evidence that it cannot legally produce. In particular, a final assembly task
may aggregate admitted evidence refs from prior slices, but it must not own
creation of browser traces, videos, screenshots, fixtures, or runner
configuration when those files belong to another task. Move evidence
production to the owning slice or expand/split ownership explicitly.

Declare `required_plan_ports` from the PRD's actual dependencies, normally
`requirement_spec`, `goal_claim_set`, `task_map`, `planning_result`, and the
matrices consumed by implementation or Verify. Emit their source refs; never
construct or select the current Plan Artifact Package yourself.

When the briefing declares `product_acceptance_spec` as a required plan port,
return one top-level `plan_ports[]` descriptor with logical name
`product_acceptance_spec`, schema `product_acceptance_spec.v1`, and an inline
body containing:

- one stable `assembly_owner` that matches the task-map assembly owner;
- runnable `entrypoints[]`, each with `entrypoint_id`, exact `start_command`,
  observable `health_check`, and owner;
- `user_journeys[]`, each with `journey_id`, `title`, `mandatory`,
  `verification_tier` (`runtime|e2e|manual_evidence`) and nonempty
  `assertions[]`;
- each journey's assertions with `assertion_id`, `statement`,
  `observation_kind` (`api|cli|dom|interaction|state|visual`), and `mandatory`;
- a `provider_qualification` policy that explicitly says whether real Provider
  qualification is required, which providers are in scope, and its TTL.

Check these nested fields together before the first validation. A prose
journey or an assertion using only `id`/`kind` is not the machine contract.
Use the current briefing's shape example to learn the fields, then replace
its example meaning with the confirmed requirement; never treat the example
as product evidence or use it to remove mandatory acceptance.

Do not invent run, plan-revision, or task-map-generation values in that body.
The Kernel binds those identities while constructing the current Plan Artifact
Package. Product semantics, journeys, commands, and assembly ownership remain
Planner-owned.

Use the complete descriptor example in `zf-plan-task-map-contract`'s
`references/product-acceptance.json` before writing this port. Populate every
mandatory journey from the PRD; the one-journey example is not a coverage limit.

For `e2e` / `real_e2e` criteria, bind the command to the intended executable
path and method. A deterministic analytical estimate, fixture replay, or
mock-only receipt cannot satisfy a contract that requires the real application,
browser, provider, or simulation unless the acceptance criterion explicitly
permits that substitute.

For Docker Playwright rows, preserve the complete runner boundary in the
canonical command: use `--entrypoint bash -lc` (or another explicitly pinned
entrypoint) and include `acceptance_ids` on every real-E2E row. Do not emit
`mcp/playwright:latest node --test ...`; the image's default MCP entrypoint
will parse `node` as an option and the workflow will fail before the browser
starts. Keep static/runtime commands in the normal Test Matrix and do not label
them as real-E2E evidence.

## Goal Closure Loop

PRD delivery can replan after verify without discarding completed slices. If
verify or judge finds unmet acceptance criteria, use:

- `zf-verify-rescan-replan` to rescan the implementation against the approved
  PRD and runtime evidence.
- `zf-goal-closure-replan-contract` with `goal_kind: "prd"` and
  `gap_category: "acceptance_gap"` to write a `goal-gap-plan.v1` artifact.
- `zf-gap-task-synth` to create bounded gap tasks with PRD acceptance ids,
  source refs, and focused verification.

The amended task map must dispatch only the new gap tasks with
`resume_scope: "gap_tasks_only"`; final closure still requires a clean rescan
with no open P0/P1 acceptance gaps.

When PRD delivery runs as a long-horizon goal loop, the runtime (not the PRD
roles) emits the goal lifecycle: `run.goal.started`, `run.goal.updated`,
`run.goal.completed`, and `run.goal.blocked`, plus
`run.goal.quiescent.entered` / `run.goal.quiescent.exited` when the loop idles.
Goal closure is the `run.goal.completed` signal, so a replan is truly done only
when a clean rescan lets the loop reach it instead of `run.goal.blocked`.

### 声明可导航关系

生产 Task 切片时按已注册 schema 保留 requirement_ids、acceptance_ids、blocked_by/depends_on
及 source_refs/image_refs 等显式关系；不要为导航添加 schema 不接受的字段。语义映射由
Planner/Critic 对账，不能依赖运行时从散文猜测；目前导航只提取这些字段中的字符串引用。
章节/关系目录只定位固定版本，导航不完整时继续核查获准正文，不将无命中视为无需求。

## 无法完成可执行计划时的草案审查

先遵循 Task Map Skill 的来源 AC 归组、原子场景保留和逐片验收责任方法。
业务约束冲突不等于 Provider/传输故障。若已完成分析，但真实互斥要求使完整 Task Map 无法成立，
保存当前 Markdown 草案和 `source-index.v1` 来源索引（可使用拟议 Task ID），提交以下明确声明：

```json
{
  "candidate_kind": "owner_review",
  "execution_status": "completed",
  "result_semantics": "artifact_production",
  "recommendation": "abstain",
  "summary": "列出冲突的原文依据、已尝试的可行方案和仍需决定的理由",
  "findings": [],
  "plan_artifact_ref": "<实际草案 Markdown 路径>",
  "plan_artifact_sha256": "<该文件原始字节的 SHA-256>",
  "source_index_ref": "<实际来源索引 JSON 路径>",
  "source_index_sha256": "<该文件原始字节的 SHA-256>",
  "owner_decision_items": [{"decision_id": "D1", "question": "具体互斥决定", "blocking": true}]
}
```

通过当前 operation 的 `zf result validate/submit` 提交，不直接改状态或生成审批事件。
此声明只表示草案工件生产完成，绝不表示计划可执行或已获 Critic 批准；禁止将 Provider 中断、
输入传递失败或工具异常标为此路径。实际执行失败继续明确报告 `execution_status: failed`。
独立 Critic 必须核对来源、逐项判断冲突是否成立：可由合理归组/责任调整解决时返回 needs_rework；
确实需要 Owner 时返回带完整 `owner_decision_items` 的非批准结果，并绑定当前候选摘要，进入
已有 Owner 检查点。不能仅复述 Planner 的阻塞结论，也不能批准缺失 Task Map 的草案。
Owner 答复后重新规划，完整计划仍需正常机械校验、独立 Critic 和人工审批。


### 完整 Plan 正文的首屏阅读顺序

共用依赖技能的[正文阅读与审查](../zf-requirement-reconciliation/references/document-reading.md)。
该方法同时覆盖 `prd-plan.md`、PRD 正文和可选 `human_review`，以及作者自检与独立 Critic
对实际文件的检查。完整原文先说明业务交付、方案和验收，工程来源放可达附件；
不通过前端关键词删改历史正文，不以技能加载代替原文检查。
