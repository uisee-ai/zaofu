# OA 有界 P0–P1 遗漏闭合记录

> 日期：2026-10-29
> 状态：实施中
> 基线：`dev@3812a291`（194/195 与前一批 bounded OA 已合入）

## 本批闭合项

### P0：OA profile 身份贯通

新增统一 `is_configured_orchestrator_role` 判定，显式 `workflow.orchestration.oa_profile` 支持 role name 和 instance id，并应用到 worker 调度、生命周期、状态投影、briefing、shutdown、recovery 与 Flow role closure。没有改变事件 ledger 中 `actor=orchestrator` 的 Kernel 兼容身份；这些 actor 是审计身份，不是 provider role lookup。

### P0：WRC normal frontier consumer

`normal_frontier_owner: wrc` 现在由 `workflow_frontier_consumer` 调用既有 registered read-only continuation executor。WRC 消费仍经过 reservation、operation、currentness、预算和幂等检查；没有增加第二套队列或状态机。dispatch event 明确记录 `actor=wrc` 与 `normal_frontier_owner=wrc`。Run Manager 在该配置下继续保持 owner fence，不会重复消费。

### P1：effective config currentness

execution context sidecar 保存 effective config ref/digest。Decision admission 对 checkpoint request、execution context 与当前配置摘要做一致性检查；快照 ref、digest 或 policy 变化会 fail-closed 为 `execution_authority_changed`。

### P1：`oa_profile` 启动校验

配置加载完成 role/profile merge 后，显式 profile 必须匹配已配置 role name 或 instance id；缺失 profile 在 `load_config` 阶段直接返回 `ConfigError`，不会等到 Provider 启动或 checkpoint 运行时才失败。

## 仍保留的边界

- 真实 Provider/tmux canary 仍是宿主资格层，不能由 deterministic/mock 测试替代。
- `actor=orchestrator` 的历史事件仍保留为 Kernel ledger identity；semantic OA transport 使用 resolved profile instance。

## 验证

实施后填入 focused/mock E2E、`scripts/dev-premerge-gate.sh` 与 worktree commit 证据。
