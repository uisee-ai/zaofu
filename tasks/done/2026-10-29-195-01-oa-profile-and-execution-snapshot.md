> 状态: done — ed2f6a81 feat: bind OA profiles and frontier ownership

# 195-01 OA profile 绑定与执行计划快照

## 目标

让 195 的 OA 成为可配置的 semantic profile，同时把 admitted execution plan 绑定到本次 operation 的 effective config 快照；保持没有配置 `oa_profile` 时的 194 兼容行为。

## 范围

- 在 `workflow.orchestration` 增加可选 `oa_profile`，显式值解析为 role name 或 instance id。
- OA checkpoint operation、transport、recovery 使用同一 profile resolver，不再把 semantic OA transport 目标写死为 `orchestrator`。
- `orchestrator-execution-plan.v1` 的 identity 和 admission event 保存 `effective_config_ref`/`effective_config_digest`。
- 不改变 Layer 2 旧配置的默认语义；配置 profile 不等同于 authority activation。

## 验收

1. `workflow.orchestration.oa_profile` 可被 loader 解析；未配置时保持 `orchestrator` fallback -> verify: `tests/test_orchestrator_agent_contracts.py` 与新增 profile resolver 测试通过。
2. semantic checkpoint operation 的 `role_instance`、transport target、respawn identity 来自同一 resolver -> verify: OA transport/operation focused tests 通过。
3. admitted execution plan 的 identity、sidecar 顶层和 admission event 都带 effective config ref/digest -> verify: `tests/test_orchestrator_agent_run_plan.py` 对 sidecar 和 event 做断言。
4. 默认根配置与现有 194 mock E2E 不回归 -> verify: `tests/e2e/test_oa_wrc_dispatch_mock_e2e.py` 和 OA semantic control focused tests 通过。

## 实施记录

- 已在 loader/schema 增加 `oa_profile`，并由统一 resolver 驱动 operation、transport、recovery 和 capability identity。
- 已把 effective config ref/digest 写入 admitted execution plan identity、sidecar 顶层和 admission event。
- 验证：OA profile/operation/plan/194 mock E2E focused tests 全部通过。
