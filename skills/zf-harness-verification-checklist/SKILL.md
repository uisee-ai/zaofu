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

## Checklist

- The checked worktree/ref is the immutable target named by the briefing.
- The task contract and every mandatory acceptance criterion were read.
- Each criterion has a behavior-specific check; a green generic command alone
  is not coverage.
- Required verification tiers have reproducible command or artifact evidence.
- Changed files/artifacts and their evidence belong to the assigned scope.
- Declared implementation checks were independently rerun where applicable.
- Required regression, integration, browser, provider, packaging, or live
  checks were added when the contract calls for them.
- Skipped checks name the reason, risk, and next owner.
- Verifier/environment execution failure is distinct from product failure.
- Rejected and blocked criteria have evidence of the same quality expected for
  passed criteria.

## Verdict Method

- Recovery attempts: read the current recovery context before probing a runner.
  Inspect any supplied execution receipts against the exact target, command,
  source stability, exit code and required evidence. An unavailable local Docker
  socket does not invalidate a bound host execution receipt. Receipt availability
  alone does not establish product acceptance. If the recovery input itself is
  unreadable, report an input failure with the actual diagnostic evidence.
- `passed`: all mandatory criteria are covered; no blocking finding remains.
- `rejected`: verification executed successfully and evidence proves one or
  more product criteria failed.
- `blocked`: evidence proves an external prerequisite or decision prevents a
  product verdict.
- verifier execution failure: do not manufacture a product verdict; report the
  failed execution/target condition through the configured result profile.

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

## Reader 语义进度与接力

材料已读、条款已评估、报告已编写、结果已准入是四件不同的事。
在真正需要中断或交接时，用 briefing 的 `artifact work-record publish --stdin`
保存当前 operation 的记录，不改结果草稿或扩大只读权限：

- completed_actions：已评估的稳定条款 ID、结论、实际证据引用及适用输入。
- pending：未评估条款、矛盾证据、未决决定与外部等待，不写笼统“继续”。
- next_step：接手后第一个可执行动作。
- evidence_files：Reader 固定为空；证据按已有获准 ref 引用，不任意读取文件。
- 可选 report_draft：当前报告正文；草稿不能冒充独立通过。

沿 briefing 的大小上限和同 ID 幂等约束。不能为塞进检查点删掉验收义务；
大材料保持原不可变引用。新会话先 recover，再按 work_record_read 及摘要分页
回读记录，独立判断来源、版本和适用性；损坏/失效记录明确报告，不退回旧记录
假装当前。记录不证明模型记忆、不免除必读、不重置重试预算或授权额外 turn。
完成当前剩余评估后用原正式结果命令提交；检查点不能成为循环汇报进度的出口。

受控重试若新建 operation，recover 可能返回 predecessor_work_record_read。
使用当前 operation 的命令读取冻结的前驱草稿，逐条核对仍适用的证据和未决责任；
不得复用前驱的提交命令、凭据绑定或必读回执。没有该入口时明确报告历史不可用，
不要自行查找其他 child/角色的运行目录补上下文。
