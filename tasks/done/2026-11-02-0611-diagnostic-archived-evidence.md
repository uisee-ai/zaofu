# 隔离诊断应使用归档感知的原事件查询

> 状态: done；实现 `421d5db4 fix: resolve archived events during incident diagnosis`

## 证据

LS 诊断 `rmar-bd116d51bd21` 首轮 Planner 弃权，认为源事件 `evt-b728d435b9c5` 和 `evt-10477023547b` 不在当前活动账本；原始事件位于源 state 的轮转归档。后续 Planner 重规划又触发 token budget；不能推断仅此原因导致全部预算消耗。

## 验收

1. 在 `incident-reproduction` 方法中明确来源 state 和按 ID 的归档感知查询 -> verify: seed 包含可执行 `zf events --id` 入口，区分逻辑 `events.jsonl#id` 与单个活动文件。
2. 隔离实验从另一 state 查询归档 -> verify: 真实 CLI 精确返回归档事件并保持源账本不变；保留 sidecar digest、访问约束、缺失时 abstain 和独立验证。
3. 不改变任何原 Run、预算、provider 或验收 -> verify: 场景、CLI、EventLog 和调用方回归通过；真实模型成本与成功率另行验证，不据 mock 宣称完成。

## 验证记录

- 从实际 LS 归档经 `zf events --id evt-b728d435b9c5 --state-dir <source> --json` 查得原事件；从隔离实验 cwd 查询也成功。此前 Planner 以活动文件缺失判断原证据缺失的结论不成立。
- 真实 CLI 归档隔离测试先 RED 后 GREEN；自动影响闭包 140 passed，sentinel 53、flow smoke 10 passed。保留旧诊断失败与预算事实，未证明真实 provider 已在预算内成功。
