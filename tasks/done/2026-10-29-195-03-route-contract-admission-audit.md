> 状态: done — ed2f6a81 feat: bind OA profiles and frontier ownership

# 195-03 路由契约与 admitted plan 审计

## 目标

确认 common execution-plan envelope 不会把不同 workflow route 的完成语义错误合并，并将 195 的静态控制面、动态 plan artifact、运行时 event 三层边界固定为可验证审计项。

## 范围

- 为 common envelope 与 route-specific completion contract 增加 focused contract assertions。
- 审计 `zf.yaml` 只提供静态控制面，Plan/TaskMap/execution plan/event 不回写配置文件。
- 审计 OA/WRC/Worker/Run Manager 的 state ownership 与现有 194 实现、测试、提交证据。

## 验收

1. common envelope 仅承载 identity/graph/delegation/context；route completion contract 保持 route-specific -> verify: run-plan and task-pipeline contract tests。
2. 运行时生成的 plan/event 只写 state sidecars/EventLog，不修改 `zf.yaml` -> verify: static config hash plus mock admission test。
3. 审计报告列出已实现项、未实现项和真实 Provider qualification HOLD -> verify: task artifact references commits/tests and does not claim mock as real-provider proof。

## 实施记录

- 已核对 common execution-plan envelope 与 Task Pipeline route-specific acceptance contract，未合并不同 route 的完成语义。
- execution plan 只写 runtime sidecar/EventLog；`zf.yaml` 仍是静态控制面。
- 验证：run-plan、task-pipeline、orchestration-mode、flow smoke 和 pre-merge sentinel 通过。
