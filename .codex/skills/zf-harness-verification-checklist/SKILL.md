---
name: zf-harness-verification-checklist
description: "Use before a ZaoFu Verify/test role issues a product verdict; checks target binding, acceptance coverage, reproducibility, and failure classification without owning runtime state."
stages: [verify, test, discovery, scan, refactor_scan]
tags: [verification, evidence, checklist]
auto_inject: false
load_on_demand: true
---

# ZaoFu Verification Checklist

Use this reference at verdict time under
`zf-yoke-test-evaluator-role-context`. It defines verification quality, not the
event schema or recovery state machine.

## Project verification

在选择项目验收场景、编写项目验证 Skill 或补齐测试覆盖时，先读
[项目验证方法](references/project-verification.md)。它把原始条款、场景、
断言和执行证据关联起来；项目 Skill 提供启动、数据和具体测试入口，
本轮 Prompt 只提供目标版本、范围和已授权的执行层级。
单一已知回归可直接执行对应探针，无需重新生成全项目地图。

## Checklist

- The checked worktree/ref is the immutable target named by the briefing.
- The task contract and every mandatory acceptance criterion were read.
- Each criterion has a behavior-specific check; a green generic command alone
  is not coverage.
- Compare each mandatory AC's full statement with the actual probe scope,
  not only its ID, command owner or the author's module summary. If a first
  slice's AC says "both entrypoints" or "final README" but the evidence only
  covers its local entrypoint, do not mark that AC passed. Record the exact
  conflict with the Plan/source and use the configured finding/rework path;
  do not silently narrow an admitted AC or edit the canonical contract.
  A contract conflict is not proof of faulty product behavior, nor permission
  to implement a successor early. Request the authorized contract correction
  while preserving the successor's mandatory verification obligations.
- Required verification tiers have reproducible command or artifact evidence.
- Changed files/artifacts and their evidence belong to the assigned scope.
- Deliverable links and required resources resolve from the clean candidate.
  Runtime-injected PRDs, fixtures or local sidecars are not shipped files;
  their presence in a worker directory cannot prove a product link works.
- Declared implementation checks were independently rerun where applicable.
- Required regression, integration, browser, provider, packaging, or live
  checks were added when the contract calls for them.
- Skipped checks name the reason, risk, and next owner.
- Verifier/environment execution failure is distinct from product failure.
- Rejected and blocked criteria have evidence of the same quality expected for
  passed criteria.

## Verdict Method

- `passed`: all mandatory criteria are covered; no blocking finding remains.
- `rejected`: verification executed successfully and evidence proves one or
  more product criteria failed.
- `blocked`: evidence proves an external prerequisite or decision prevents a
  product verdict.
- verifier execution failure: do not manufacture a product verdict; report the
  failed execution/target condition through the configured result profile.

外部权威版本变化、缺少 Owner 决定或当前 Plan 不允许所需修订时，先区分已批准
实现的缺陷与合同阻塞。若判定修复依赖修改不可变 Plan port，采用已有 `blocked`
结果与对应 blocked requirement，结构化 rework item 的 `allowed_scope` 写明
`plan_ports:<实际 port 名>`，附原/新权威、版本冲突、当前实现及批准缺口的证据。
例如任务合同确需修订时填写 `plan_ports:task_map`。单独的 `plan_ports` 缺少工件名。
这表示交给 Planner 修订，不授权 Verify 修改 Plan。不能只在 summary/owner/next_gate
中写“重新规划”，同时把范围全写成源码路径；也不能把普通产品错误改报为合同阻塞。
混合缺陷分别保留，不用重规划请求掩盖已证明的实现错误。

The current runtime-provided `verification-result`/child report profile is the
authoritative output shape. Fill it exactly and cite durable evidence refs.
Do not copy field lists from old examples, emit a different terminal event, or
move task truth directly.

## Gap Handoff

Only blocking product/parity gaps become gap work. Load
`zf-verify-gap-producer-contract` to produce the canonical gap handoff; load
`zf-goal-closure-replan-contract` only when that handoff requires task-map
amendment. Suggestions and nits stay in the verification report.

## Boundary

Runtime validates schema, identity, evidence presence, result admission,
attempt/cap, affinity, stale/replay, and terminal state. This checklist decides
whether the selected checks and evidence meaningfully prove the project
contract.
