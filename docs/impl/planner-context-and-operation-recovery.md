# Reader 上下文与终止操作恢复

Codex 的手动 `/compact` 需要空闲会话。长时间执行的 reader 在默认原生压缩发生前，
可能先达到 ZaoFu hard cap，并在 drain hold 到期后被回收，丢失会话内的继续工作线索。
`CodexAdapter.build_command` 现在为 fresh/resume 都传入原生自动压缩预算：
`max(1, int(role.context_window_tokens * role.context_compact_threshold))`。
默认是 140,000 tokens；这是配置窗口的保守工作预算，不是模型实际窗口声明。
不覆盖 `model_context_window`，不修改用户全局配置，context hard cap 和预算熔断保持原规则。

[Codex 配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)
定义 `model_auto_compact_token_limit` 为自动历史压缩阈值。模型窗口较小或单次工具输出
很大时，Provider 自身上限和 harness hard cap 仍可能先触发，不能将此设置视为完成保证。

Reader 恢复次数耗尽时，内核写入 `fanout.child.failed` 后、评估聚合前，通过既有
`WorkflowOperationService.fail` 终止该 child 绑定的 operation。复用超时路径的
`fail_reader_child_operations`，保留请求身份、因果链和终态幂等。失败 operation
不再被预算守卫视作运行中，也不会继续累计同一角色后续工作的成本与采样次数。
不接纳成功结果、不清零成本账本、不改变允许的恢复次数。

回归由 `tests/test_reader_recovery_operation_closeout.py` 经真实 Orchestrator
入口验证，包含耗尽后的终态、因果链、重复 tick 和后续同角色用量；adapter 测试
覆盖默认/自定义预算以及 fresh/resume。实际长任务仍须观察原生压缩和产物推进。

当 Run 终止连带取消后续 reader 时，监工可先经 `zf goal set --status active`
恢复目标，再通过 `stage-retrigger` 指定原触发事件。该动作除既有固定事件外，
识别当前配置中 `topology: fanout_reader` 的已注册 trigger；沿用原事件的全部
产物、任务、Run 身份，生成新 `rework_of` 代际，同源重复恢复被拒绝。
未注册事件及新增 writer trigger 不因这项支持而获准。
Run 激活与重触发都保留审计，不清零原 Run 成本、不修改既有操作的终态。

停滞检测与派发可以观察到不同批次的事件：补发时原触发尚未消费，真正派发时
原 fanout 已启动。Reader/Writer 派发入口因此按 `original_trigger_event_id`、
事件类型、关联运行与完整载荷核对自动补发及原触发；同一来源在该 stage 已有
fanout 时仅写幂等 `fanout.retrigger.suppressed`，不创建新 fanout 取消正常工作。
两种消费顺序都适用。受控的新 rework/retrigger、不同载荷或其他 stage 保持独立，
未处理过的原触发仍可由停滞恢复启动。见 `tests/test_stall_fanout_idempotence.py`。

`zf plan inspect-execution` 与运行时共用 `flow_metadata_for`：多 Flow 使用
flow_metadata_by_kind 的指定条目，单 Flow 回退到编译后的 flow_metadata。
检查工具不得因单 Flow 缺 by-kind 容器而把已启用的 Task Pipeline 误报为 off；
它仍只报告事实，不批准 Plan、不派发任务，也不创建运行状态。
