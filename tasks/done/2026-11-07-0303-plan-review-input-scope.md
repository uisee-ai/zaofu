> 状态: done

实现：`427c644b fix: preserve input scope when returning a plan for repair`。

# 计划退回的读取范围与任务正文交接

LS-v1 新候选 evt-d51d82f1bd48 已通过 result validate，但 34 份完整任务正文只在 Planner 私有目录，task_execution_input 的 body 仅引用未绑定相对路径；正式 aggregate 仅迁移六份顶层工件。按需读取不能依赖作者工作目录。当前未批准该计划，Planner operation 已成功收束，受控停止控制面，保留其全部产物。

1. CLI/受控 plan-reject 携带仅影响读取的 input_task_ids -> verify: 参数规范化和非法值拒绝，正式 plan.rejected 审计保留。
2. 实际 replan resynth 传至 Plan handoff -> verify: 同版完整快照和索引保留，选读范围不改变 task_ids/派发；省略时清除上次 operator scope，不静默漏读。
3. 方法补强 -> verify: task-map skill 明确不能用私有路径替代正文，完整 body 进入 shared/tasks，过大的 Plan port 使用既有 ref/sha256 描述符并验证迁移后可读；同步三份。
4. 本地验证、提交合入 -> verify: 影响闭包、sentinel、mock；随后仅退回当前候选的交接问题，保留 34 tasks/91 AC/62 commands 与已完成任务，恢复真实 Planner 修订，再检查 Worker 派发。

## 实现验证

- 主线复现 CLI 丢弃读取范围、受控退回忽略非法 scope。新测试初版误用了无返回值的 EventLog.append；改用 EventWriter 后端到端回归通过。
- 直接验证 44 passed / 1 skipped；自动规划选出 353 个影响文件（按 AGENTS 执行精确闭包，未扩至全量）：4,627 passed / 3 skipped（114.55s）。
- 串行 10 passed、sentinel 53 passed、mock flow 10 passed；Skill 元数据合法，source/Codex/Claude 三份 reference 逐字节一致。
- 日志：/tmp/zf-plan-review-scope-{plan,closure,tiers,sentinel,smoke}-20261107.*。
- 真实交接审计：LS-v1 docs/records/2026-11-07-plan-handoff-audit.json；具体修订反馈：2026-11-07-plan-handoff-feedback.md。
- 代码与方法已验证，部署后的受控退回/真实修订验证尚待完成，因此此时保持 active。


## 最终真实验证（UTC 2026-11-07 03:58–04:00）

- LS-v1 加载 dev 2475518b，Watcher PID 1421948；loop evt-b3fd4ad9df29，加载回执 diagnostics/runtime-load/79cde79d33ec485494e925a04ce59c84.json。
- 受控恢复 evt-83ea802b1e15 后，32 项未完成合同全部绑定当前 a48ab08b Package / 6a4790a4 generation；两项历史完成保持 done，验收不减，原 map 全 34 项保留。
- 实际 impl 派发：FMS-03-GAP-CONTRACT evt-6f2baaf3b785（03:58:52）、FMS-02-GAP-CORE evt-e12ad03d963a（03:58:58）、FMS-20-GAP-GATEWAY-02 evt-e4dbc860d145（03:59:06）；均有 workflow.operation.started / task.attempt.sent。
- 三者基线均 cc6e4d7d，包含已完成两项；各自的不可变合同快照与新包一致。必读范围为 6 项：验收、命令、共享输入、本人完整任务输入、关键 workflow/user-config。其他 33 项的正文未被列为必读；完整包保留可检索。
- 目前真实 worker 正在按 cursor 读取绑定输入，已产生本 attempt read-ledger；这是派发与上下文交接验证，不是业务任务验收完成。继续监工记录在 LS-v1 docs/records/2026-11-07-three-blockers-recovery.md。
