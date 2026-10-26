# 187-03 · 任务图与并行诊断

> 状态: done
> 实现 commit: 2299765a feat: add BMAD-inspired delivery methods and plan dependency diagnostics
> 授权: 用户明确要求拆分 187 并按 backlog 实施；本项由 backlogs 移入 tasks/active。
> 来源: [设计 187](../../docs/design/188-bmad-inspired-epic-task-map-and-tdd.md) 187 §5–§6。

## 范围

增强现有只读 Plan execution review 的最长链路径、依赖层及交接索引；保留既有调度。

## 验收

1. 先写行为测试 → verify: 旧实现因缺少可解释图事实失败，再完成实现使其通过。
2. 接入既有 CLI/评审入口 → verify: 菱形、顺序无关、缺失依赖、环、空图及 CLI 只读行为均通过。
3. 既有 v4 调度/背压复核 → verify: 无提前消费依赖、无超容量派发，不修改默认配置。

## 验证与评估记录

新增最长依赖路径、分层任务、直接依赖/后继，复用 CLI 和 Plan preflight。
新增 6 项先 RED，功能实现后与旧检查共 15 passed。扩大回归覆盖缺失/环/空图、
1200 节点链、顺序稳定、只读 CLI 及既有 v4 池/背压/依赖。
RED 基线: fe6f3bd620e158acdf5e2b09794a860ebfad8212。
RED 测试文件: `tests/test_plan_dependency_diagnostics.py`。
测试 SHA-256: `e205cd8a15facb9f4198e924f71e3ae01a4ef612194ee1e6098f2ca1ad271267`。
实现提交保留同一测试，可在隔离基线恢复该测试文件复现 RED。

完整命令、失败修复与未覆盖范围见[评估记录](../../docs/records/2026-10-26-0336-187-delivery-validation.md)。
