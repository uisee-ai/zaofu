# OA 有界 P0–P1 遗漏闭合记录

> 日期：2026-10-29
> 状态：已完成
> 基线：`dev@3812a291`（194/195 与前一批 bounded OA 已合入）
> 实现提交：`2419f0d0 feat: close bounded OA p0 p1 gaps`；`3fb3caff fix: route configured OA actor seams`。

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

## 验证证据

- focused / mock E2E：`237 passed`。
- serial tier：`10 passed`。
- `bash scripts/dev-premerge-gate.sh`：`50 passed, 1 skipped`。
- `bash scripts/run-flow-smoke.sh`：`10 passed`。
- 首轮 broad deterministic：`13310 passed, 21 skipped`，5 项仓库既有缺失文件失败；6 项最小 host usage 测试发现并修复了 profile seam 的兼容问题，修复后 targeted `8 passed`。
- 第二次 broad deterministic 因共享 `/tmp/pytest-of-min` 磁盘耗尽退出，属于测试环境容量问题，不作为代码回归结论；清理后完成上述 focused、serial、sentinel、flow smoke 复验。
