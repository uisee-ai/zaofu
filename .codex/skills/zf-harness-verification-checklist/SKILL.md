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

判词中的每项主张对应当前条款、真实触发入口、预期/观察和可复核证据；命令绿灯只证明
实际覆盖的行为。无阻断 finding 时也说明检查范围。报告正文/summary 末尾列“未验证项”
及原因、影响和下一责任方；不能以缺少本地 runner 否认适用的 host 收据，也不能以收据
存在替代语义判断。需要补齐上述映射时按[项目验证方法](references/project-verification.md)
核对；不增加字段、权限、重跑要求或第二份 verdict。

## Gap Handoff

Only blocking product/parity gaps become gap work. Load
`zf-verify-gap-producer-contract` to produce the canonical gap handoff; load
`zf-goal-closure-replan-contract` only when that handoff requires task-map
amendment. Suggestions and nits stay in the verification report.

## Boundary

恢复后的复验先对照原始需求和已批准合同：将已证明的产品缺陷、合同争议、执行结果未知
分别列出。不能将偏好的边界行为追补为阻塞 AC；每项阻塞须给原条款、实际触发与失败证据。
复用执行事实时核对目标、合同、来源、环境和补丁影响；只有审核身份变化不能单独成为重跑
全部命令的理由。受控交付的历史收据仍需独立判断，不是当前消费者的通过票。
Candidate 合同带 `candidate_recovery_obligations` 时，最终通过还须逐项提交
`recovery_obligation_results`，写明 finding_id、resolved、rationale 与真实 evidence_refs。
evidence_refs 必须使用当前输入索引中的 ref/sha256 对象；读权限或内容不匹配不能提交。
contract_question 可引用当前冻结的原批准需求及具体条款解释，不强制新审批；若声称
owner 已变更合同，resolution_event_id 必须通过真实批准请求、trigger 与当前 admitted
generation 绑定。无论如何表述，不能改变当前合同、命令、范围或权限；正式合同与 PRD
冲突须走原有 scoped Plan 修正。
带 original_start_event_id 的 host unknown 须引用精确原 effect 的完成、获准独立补采，
或当前目标针对原冻结命令的独立事实并说明适用性；已知非零也能解决未知，但产品验收
仍然失败。原 unknown 不改写。RM 建议、其他 Run 决定和无关探针不能充当授权或事实。
局部修复不能消除未知执行结果；需求决定仍由获准 owner 处理，不以 Reader 自行豁免代替。

Runtime validates schema, identity, evidence presence, result admission,
attempt/cap, affinity, stale/replay, and terminal state. This checklist decides
whether the selected checks and evidence meaningfully prove the project
contract.

## Reader 语义进度与接力

材料已读、条款已评估、报告已编写、结果已准入是四件不同的事。
完成必读材料、完成一组验收检查或发现改变后续动作的具体问题时，按
`artifact recover` 返回的 `work_record_publish` 命令保存当前 operation 的记录。
不要等到压缩、中断或交接才保存；没有新进展不重复发布，不扩大只读权限：

- completed_actions：已评估的稳定条款 ID、结论、实际证据引用及适用输入；
  只完成阅读时记录来源/摘要和约束，不能写成验收通过。
- pending：未评估条款、矛盾证据、未决决定与外部等待，不写笼统“继续”。
- next_step：接手后第一个可执行动作。
- evidence_files：Reader 固定为空；证据按已有获准 ref 引用，不任意读取文件。
- 可选 report_draft：当前报告正文；草稿不能冒充独立通过。

沿 briefing 的大小上限和同 ID 幂等约束。不能为塞进检查点删掉验收义务；
大材料保持原不可变引用。新会话先 recover，再按 work_record_read 及摘要分页
回读记录，独立判断来源、版本和适用性；损坏/失效记录明确报告，不退回旧记录
假装当前。记录不证明模型记忆、不免除必读、不重置重试预算或授权额外 turn。
完成当前剩余评估后用原正式结果命令提交；检查点不能成为循环汇报进度的出口。
同一输入身份的 `historical_missing=0` 只证明本消费者已完成读取，不证明当前理解。
先结合工作记录和仍掌握的上下文指出具体遗忘条款，再通过索引取相应原文；
缺少WRC、发生压缩或 `current_memory=unknown` 都不是全量重读的默认理由。
确实缺少正文时完整补读，首次必读、跨角色独立验证和当前证据门禁保持。
若 `artifact read --required --resume` 返回 `context_recovery`，历史完整交付已获验证，
当前记忆仍未知；执行 `artifact recover` 中绑定的工作记录入口，列出具体上下文缺口，
再按精确来源定位补读。该提示不代表已验收通过，也不授权跳过首次必读或其他角色的独立评估。
需要完整复核时可显式去掉 `--resume`；不要反复重启同一完整 bundle。

受控重试若新建 operation，recover 可能返回 predecessor_work_record_read。
使用当前 operation 的命令读取冻结的前驱草稿，逐条核对仍适用的证据和未决责任；
不得复用前驱的提交命令、凭据绑定或必读回执。没有该入口时明确报告历史不可用，
不要自行查找其他 child/角色的运行目录补上下文。
