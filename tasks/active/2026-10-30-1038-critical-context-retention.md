> 状态: active

# 关键上下文常驻与来源绑定

来源：用户批准按建议 1–3 建立 backlog、在 context-delivery worktree 实施并真实验证。

## 范围

将原始需求、用户问答、批准配置、当前身份、未完成验收与返工项通过现有权威来源和摘要绑定到派发/恢复读取面；语义完整性由 agent 验收，运行时仅保证来源/版本。

## 验收

- 真实 Kanban 问答与 Raw model 修改 -> verify: 下游 worker 能从绑定来源读取选择和批准配置；新 attempt/rework 保留当前身份与完整约束。
- focused 测试、影响闭包、sentinel 与 mock E2E -> verify: 全部通过，记录明确测试层级。
- 使用当前分支源码、zf-prd-v5 和隔离状态真实运行 -> verify: Ask User → 回答 → 匹配 workflow → Raw 修改 → 批准 → 启动 → task 完成，保留证据与清理记录。

## 交付

实施与验证已完成，待记录本地实现提交；不 push、不 merge。

- 最终直接调用/共享契约闭包 103 passed，sentinel 51 passed；其余层级见报告。
- 真实 Codex：TASK-F156CC / prepare-TASK-F156CC-a64b94daeb40e095，
  经 Raw 明确批准 model 与 lanes=1，完整 v5 流程于 12:05:16 UTC 完成。
- 交付目录 9 个产品测试、14 项独立 CLI 验收通过；证据已归档，临时 runtime 已清理。
- 原 lanes=8 的总预算失败和交付观测 unknown 边界均保留在报告中。

验证报告：[context delivery 实弹记录](../../docs/records/2026-10-30-1042-context-delivery-e2e.md)。
