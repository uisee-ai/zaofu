# 恢复派发后的阶段工作记录

对应任务：`tasks/done/2026-11-09-1052-work-record-placement.md`。

datal P16B 在同一 operation 恢复到 placement 3 后，阶段工作记录仍将派发 envelope
与 immutable request 的初始 placement 1 比较，导致合法发布失败。反向回归也发现：
已恢复 operation 收到旧 placement 的派发时，旧校验可能接受它。

`work_record._operation_workdir` 对 Task Pipeline 使用 operation reducer 核验过的当前
placement。该 reducer 从绑定当前 run/task/attempt/dispatch/lease 的 kernel
`task.attempt.sent` 获取 epoch；未知 epoch 不回退到旧请求。其余合同 generation、
工作目录、角色、attempt、lease、envelope digest 仍按原路径校验。
保留 legacy 无 pipeline operation 的既有路径，不修改原请求或 canonical state。

验证覆盖实际 CLI 发布、回读、幂等，以及旧 placement、缺失或不可信 sent、错误 lease
的拒绝。阶段记录仍是 worker_report_unverified，不赋予继续执行、完成或验收权限。

验证结果：初始两项回归失败，修复后影响域66、sentinel53、动态续跑mock E2E4通过。真实P16B案卷只读回放正确定位placement3工作目录。

实现 `813f15c3` 已合并 `95db6981`，合并后58项定向及53项sentinel通过。
2026-11-09 10:57 UTC 原worker经当前CLI成功发布 `evt-ca95172175de`，
attempt/dispatch/manifest不变；sidecar摘要核验通过，claims_status仍为
worker_report_unverified、authorizes_execution=false。发布缺陷现场闭环，产品验收另行执行。
