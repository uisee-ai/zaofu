> 状态: active

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
