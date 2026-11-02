# 保留诊断请求已绑定的 Run 和 Task Map 代次

> 状态: done；实现 `9861f2f5 fix: bind diagnostics to the active run generation`

## 证据

LS `evt-1d3040844a32` 的 `workflow_run_id` 和 `run_id` 为空，但其 context sidecar 中 `operation_precondition` 已绑定 Run `prepare-TASK-90D92C-e4eaededbd2b74fe` 与 Task Map generation。引用传递修复本身不能补回源头缺失的身份。

## 验收

1. 当前受控操作缺少显式 Run 时引用其已有 precondition -> verify: Run Manager 实际请求包含同一 Run/generation，不从未绑定的上下文猜测。
2. 隔离 Run 与 generation -> verify: 显式 Run 与 precondition 不一致时不得借用其他 Run 的 generation；原始字段与空值保持可解释。
3. invocation/trigger/loop/seed 继续传递 generation -> verify: 完整链回归、调用者/共享事件合同及 mock 恢复测试通过；不回写已发生的请求。

## 实现与验证

- 对 LS `rmar-bd116d51bd21` 原 context sidecar 的 action 在独立临时事件账本只读重放，请求输出正确 Run 和 generation；不改真实旧请求。
- 恢复、OA、Autoresearch 和 Supervisor 定向闭包 461 passed；串行 10、sentinel 53、flow smoke 10 passed。规划器将共享 runtime 路径保守扩至全仓，本次按影响范围使用上述定向闭包，未运行 full pytest 或真实 provider E2E。
