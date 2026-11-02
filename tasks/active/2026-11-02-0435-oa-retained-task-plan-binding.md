> 状态: active

# P1：OA 无法接收保留旧代次任务的 blocked 审查

Owner 已授权恢复 blocked Task、经 OA 拉起修复并持续监工。
真实请求 evt-5d7836f84717 被 evt-2e128efa73eb 以 target_generation_stale 拒绝。
DL-FOUNDATION 生效代次 77a994dd 的包仍有效，但全局包已经进入 4e6df815。
当前 HEAD 的隔离复现证实相同拒绝。结果提交与决定准入也存在同类全局绑定错误。

## 验收

- 保留任务的旧代次 -> verify: 请求和真实 result-submit 链路接纳原包。
- Task 自身重新准入 -> verify: 拒绝旧结果提交及尚未应用的旧决定。
- Run/包摘要不一致 -> verify: 不回退全局包；全局检查点仍拒绝旧包。
- 合并并部署 -> verify: 原始失败审查请求生成 OA operation；保留 Critic 原请求与读取账本。

方法复用 Task Pipeline 代次和不可变包选择器，三处准入共用作用域绑定，
不改任务状态、失败历史或语义验收标准。说明见 docs/impl/oa-bounded-execution.md。
