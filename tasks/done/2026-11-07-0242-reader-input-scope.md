> 状态: done
> 实现: `5f604210 fix: bind explicit task input scope to authorized recovery`

# 授权恢复显式携带任务读取范围

LS-v1 `evt-5b69b77b8744` 恢复有六条操作员反馈但没有结构化读取范围；因此必读 rework context 包含完整任务合同（175,443 bytes），实际读取约 11 分钟并多次压缩。现有 scoped snapshot 支持存在，受控新代际入口未提供仅影响读取的范围参数。监工提交恢复参数时也未声明范围。

用户已授权发现并修复监工问题，本项实施时转 active。当前 Planner 已读完冻结输入，保持其执行，不重写既有 manifest。

1. 受控新代际接受可选 input_task_ids -> verify: 类型校验、审计载荷、同代际幂等；范围变化冲突，不静默复用。
2. 传递范围至 Plan handoff 并使用现有 scoped snapshot -> verify: 全量任务索引和完整不可变合同侧车保留，可按 JSON path 读取；未知 id 显式呈现；不改 task_ids、依赖、派发和验收。
3. 兼容未提供范围的恢复 -> verify: 无范围仍完整读取；新代际不得继承上次操作员输入范围；固定输入修复重试不能静默改变范围。
4. 更新实现说明、聚焦影响闭包、sentinel 和 mock flow -> verify: 记录结果，提交、合并 dev；本次活跃 operation 不因优化被打断。

## 验证

- 当前 dev 上重现：受控请求静默丢弃 input_task_ids，新回归 KeyError；非法参数也被忽略。测试 fixture 最初与已有 T1/T2 冲突，修正后才取得有效复现。
- 直接调用闭包 67 passed；dev-verify 选择 93 个影响文件。工具因 >80 文件建议全量，依 AGENTS 按精确影响闭包执行：1,655 passed / 9 skipped（75.55s），不含 host/real_provider/perf/serial。
- dev-verify 其他 tiers：串行 10 passed，sentinel 53 passed，mock flow smoke 10 passed。
- 真实 LS-v1 输入在 /tmp 隔离验证：全量 25 项快照相等，inline 3 项，context 150,397 -> 49,995 字符（-66.8%）。只证明构造与完整性，不声称当前运行已使用新范围。
- 日志：/tmp/zf-reader-input-scope-{plan,closure,tiers}-20261107.*。
- 当前 Planner 持续执行，不因优化中断；既有冻结输入保持原样。本修复用于后续带显式范围的授权恢复。
