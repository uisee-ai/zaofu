---
name: zf-refactor-plan-synth
description: "Use for ZaoFu RefactorFlow plan synthesis. Requires a lane-pipeline-compatible task_map with assembly/root ownership so implementation, review, verify, and judge stages can dispatch deterministically."
---

# ZaoFu Refactor Plan Synthesis

## Goal

Convert scan/review artifacts into a refactor plan that can pass deterministic
lane-pipeline admission. The output is not only a markdown plan; it is the
source of truth for lane dispatch.

## Required Inputs

- `review_artifact_ref` from the scan/review fanout.
- Coverage matrix, findings, uncovered areas, and scan evidence. 覆盖矩阵/发现
  的合约语义以 `yoke/verify-review` 为准:其 `requirement_coverage_matrix` 有
  `non_empty` 档位内核校验(`event_schema.py` 的 `non_empty` + FIX-14),并与
  `verify.child.completed` 报告合约机械配对,教育骨架含 `gap_findings`
  (`orchestrator_fanout.py` 的 `_SCHEMA_EDU_PLACEHOLDERS`)。本技能只消费这些
  字段,不另立矩阵语义,避免两处漂移。
- `plan_intent` if provided by the triggering event.
- `refactor_contract` from the briefing. This is the runtime contract for lane
  count and assembly policy; read it before synthesizing `task_map.json`.

## Required Outputs

Write and emit:

- `refactor-plan.md` via `plan_artifact_ref`.
- `task_map.json` via `task_map_ref`.
- `source_index.json` or scan evidence refs via `source_index_ref`.
- `scan_quality_audit_ref` when scan quality was checked.
- `risk-register.json` and backlog candidates when useful.

All refs needed by the next stage must be top-level payload fields and included
in `artifact_refs` or `evidence_refs`.

## Source Index Rules

The implementation fanout consumes per-task provenance before dispatch. For
every task in `task_map.json`, include either:

- direct task anchors: `source_key`, `source_keys`, `source_ref`,
  `source_refs`, or `source_excerpt`; or
- a `source_index.json` entry under `tasks[]` or `task_sources[]` with the same
  `task_id` and non-empty source anchors.

Good refactor anchors point to scan findings, audit findings, PRD sections, or
plan sections, for example `scan/findings.json#F-023` or
`docs/plans/refactor-plan.md#lane-runtime`. A global `sources[]` list is not
enough for multi-task refactors.

## Lane Task Map Rules

拆 task_map 的形状方法委托 `yoke/vertical-slicing`(每个 task 纵切全部集成层、
独立可验收);本节只管每条 task 的可派发字段。

Every task must be dispatchable:

- `task_id` is stable and unique.
- `affinity_tag` maps to a lane or ownership class.
- `wave` 与 `depends_on` define order(字段名以 admission 为准,
  `writer_fanout_admission.py` 读 `wave` / `depends_on`,不是 `dependencies`)。
- `allowed_paths` lists every path the worker may touch.
- `exclusive_files` lists non-shared files, or the task explains why it must be
  serialized.
- `verification` names concrete commands or evidence checks.

The workflow contract is authoritative:

- If `refactor_contract.assembly_policy == "declared_task"`, the task map must
  either include `refactor_contract.assembly_task_id` exactly or include one
  task with `root_owner_class: "assembly"`.
- If `refactor_contract.assembly_policy == "none"`, a one-bundle serial plan
  may omit assembly, but every task still needs explicit owned paths and source
  anchors. `none` 现在**还会同时跳过根 owner 启发式**(不止跳 assembly 声明,
  见下方「根 owner」段)。
- Do not infer that assembly is optional from task count when the workflow
  contract declares an assembly task.

### 根 owner(workspace-root-owner)启发式

默认(greenfield / 从零 scaffold):任一持根级脚手架路径的 task 必须把这些路径
列进 `allowed_paths`,否则 admission 报「no task owns workspace-root paths」——
R21 失败形状:根 build config 无主,根 `tsc -b` / `pytest` 从未被任何 lane 执行。
被识别的脚手架 basename 含 `package.json` / `pnpm-lock.yaml` /
`pnpm-workspace.yaml` / `tsconfig.json` / `vitest.config.ts` / `eslint.config.js`,
以及 Python 侧 `pyproject.toml` / `setup.py` / `setup.cfg` / `requirements.txt` /
`uv.lock` / `poetry.lock` / `Pipfile`(`lane_pipeline.py`
`validate_lane_pipeline_admission` 的 `_SCAFFOLD_BASENAMES`)。

**已有项目 / 局部 refactor 的 opt-out**:把一个已存在仓库导入做局部重构时,根
脚手架通常已存在、由 plan 之外持有,不该强求某条 task 认领它。此时可关掉这条
启发式(`lane_pipeline.py` `validate_lane_pipeline_admission` 内的
`_workspace_root_owner_required`,布尔显式优先于 policy 推断):

- task_map 顶层 `workspace_root_owner_required: false`,或
  `refactor_contract.workspace_root_owner_required: false`(布尔,优先级最高);
- 或 `refactor_contract.assembly_policy: "none"`(policy=none 隐含 root owner
  不需要)。

关掉 root owner **只跳过这一条启发式**,task schema / path / evidence 校验一律
不放宽——每条 task 照旧要唯一 `task_id`、显式 `allowed_paths`、`verification`
和 source anchors。

## Completion Check

Do not emit plan success until:

1. `task_map.json` satisfies `refactor_contract` assembly/root owner
   requirements, or the configured failure event is emitted with a concrete
   reason.
2. Each lane has complete allowed paths and verification.
3. `scan_quality_audit_ref` is present or a clear failure reason is emitted.

内核门语义(铸 plan / 重触发时会静默拦截,写 plan 前先知道):

- 铸新审批单前——`plan_approval` 开启时,同 `stage`+`pdd`+task 集的未决 plan
  已在队会被 `plan.minting.suppressed`(`orchestrator_fanout.py` FIX-12,
  `reason=pending_plan_same_fingerprint`)拦下,不铸新单。replan 若产出与未决
  plan 相同的任务集,新审批单会被静默抑制,所以 replan 必须是 **delta 任务集**。

## Goal Closure Loop

Refactors must close parity, not merely finish the first task map. After verify,
use:

- `zf-verify-rescan-replan` to rescan the produced code against the original
  system and the scan matrix.
- `zf-goal-closure-replan-contract` with `goal_kind: "refactor"` and
  `gap_category: "parity_gap"` to produce `goal-gap-plan.v1`.
- `zf-gap-task-synth` to append precise missing parity tasks through the normal
  `task_map.amended` / `task_map.ready` bridge.

Gap tasks should reuse the original module/lane affinity when possible and
should preserve source refs to both original implementation paths and produced
target paths. Do not pass judge while any P0/P1 parity gap remains open.

两条闭环内核门(replan / 重触发审会静默抑制,替换掉自旋):

- **replan 必须是 delta 任务集**:同 `stage`+`pdd`+task 集的未决 plan 会被
  `plan.minting.suppressed`(`orchestrator_fanout.py` FIX-12,
  `reason=pending_plan_same_fingerprint`)拦下。`goal-gap-plan.v1` 产出的 gap
  任务集若与未决 plan 相同,不会铸新审批单——replan 只放**真正新增/收窄**的
  parity 任务。
- **重审前必须有新 commit**:同一 pinned `target_commit` 在该段已有驳回记录时
  重开 judge/verify 会被 `fanout.retrigger.suppressed`(`orchestrator_fanout.py`
  FIX-15,`reason=no_delta_since_failure`)抑制。gap task 必须先落**新 commit**
  才能再触发判审,不能拿同一 commit 反复送审。
