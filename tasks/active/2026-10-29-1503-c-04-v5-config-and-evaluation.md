# C-04 v5 WRC blocking 配置与评估

> 状态: proposed

## 目标

将版本化 PRD 配置切换到 C 路线，并用确定性 E2E 证明 194、195、P0-P1 与 WRC blocking 的组合行为，明确真实 provider 资格边界。

## 范围

- `zf-prd-v5.yaml` 默认 `taskPipeline.mode=blocking` 且 `normal_frontier_owner=wrc`。
- 更新 195 设计交付入口，说明 C 已实现的边界、Run Manager recovery-only 与 rollout 约束。
- 运行 config cold-start、focused impact、pre-merge sentinel、flow smoke 与 mock E2E。

## 验收

1. v5 config -> verify: loader 解析 owner=wrc、blocking、on-demand workers、OA semantic checkpoints。
2. end-to-end -> verify: plan/task-map admission -> WRC dispatch -> worker result -> verify/integration handoff 可重放。
3. rollback fence -> verify: 新 generation 才能切换 owner；旧 run 不在 owner 切换中混合派发。
4. 报告 mock 与真实 provider 证据边界，不把 cold-start provider 未认证误报为 E2E 通过。
