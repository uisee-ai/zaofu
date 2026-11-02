> 状态: done — 670997af `fix: bind OA recovery to the target task plan generation`; 60e5f5b7 `fix: recover OA attempt binding from settled operations`

# P1：OA 无法接收保留旧代次任务的 blocked 审查

Owner 已授权恢复 blocked Task、经 OA 拉起修复并持续监工。
真实请求 evt-5d7836f84717 被 evt-2e128efa73eb 以 target_generation_stale 拒绝。
DL-FOUNDATION 生效代次 77a994dd 的包仍有效，但全局包已经进入 4e6df815。
当前 HEAD 的隔离复现证实相同拒绝。结果提交与决定准入也存在同类全局绑定错误。
部署后真实请求进一步复现 target_attempt_missing：结算后的 Task 已清除 dispatch，
但 operation 仍保存精确 attempt。补充从已验证当前性的 settled operation 恢复绑定。

## 验收

- 保留任务的旧代次 -> verify: 请求和真实 result-submit 链路接纳原包。
- Task 自身重新准入 -> verify: 拒绝旧结果提交及尚未应用的旧决定。
- Run/包摘要不一致 -> verify: 不回退全局包；全局检查点仍拒绝旧包。
- 合并并部署 -> verify: 原始失败审查请求生成 OA operation；保留 Critic 原请求与读取账本。

方法复用 Task Pipeline 代次和不可变包选择器，三处准入共用作用域绑定，
不改任务状态、失败历史或语义验收标准。说明见 docs/impl/oa-bounded-execution.md。

## 验证与部署

- 原 HEAD 隔离复现 target_generation_stale；补充回归复现 target_attempt_missing。
- 主修复自动影响闭包 1291 passed / 7 skipped，补充调用链与事件契约 149 passed。
- attempt 补充修复自动闭包 994 passed / 7 skipped；聚焦链路 107 passed。
- 两批均通过 53 项 premerge sentinel、10 项 mock flow；最终 dev 回归 9 passed。
- 合并 2fbbc3b2、88fc4ddc，运行服务固定到 88fc4ddc。
- 原始请求 evt-5d7836f84717 经注册 checkpoint service 重处理，保留原拒绝事件。
  新 OA operation wop-orchestrator-agent-semantic_fail-checkpoint-revision-77a994ddb7b2-9199547f6d4a
  于 2026-11-02T04:39:35Z running；会话 01a194f0-02f8-7321-b489-64b970e2a280
  于 04:39:53Z 开始读取。Critic 的 provider 进程、原请求和旧读取账本前缀不变。
- 真实部署证据在 DL artifacts/supervision/oa-attempt-binding-deployment-2026-11-02.json。

本项完成的是 OA 准入和派发链路修复。DL 产品任务尚未完成，继续等待真实 OA 决策、
当前 Plan Critic 结论及后续独立验证；不得把本记录当作产品验收通过。
