# Provider 终止错误与 Task Pipeline 回收

Codex rollout 的 `task_complete` / `turn_complete` 表示一轮结束，并不保证成功。
Session tailer 将非空 `error` 投影为 `provider.turn.closed` 的
`provider_status=failed` 和分类后的 `provider_stop_reason`，不复制错误正文或模型输出。
结构化 `cyber_policy` 优先于错误文案中的 auth 等关键词。

Task Pipeline 通过已注册的 reactor 入口关联当前 sent attempt。匹配必须同时满足
role instance、最新 prompt 的 turn id、attempt/prompt/close 时间顺序，以及 operation
的 Run、Task、dispatch 和 running 状态。旧轮次、其他角色、已结束 operation 不生效；
重复事件不产生第二次 operation 终局。原始事件 id 保留为回收原因。

匹配后复用既有 provider-stop 服务与 TaskAttemptStore：策略拒绝阻塞 operation 与任务，
释放 attempt，要求 operator 处理；不能将拒绝当作普通丢失会话并自动重试。
reader fanout 的丢失派发恢复也排除明确的策略拒绝。连接故障仍由 Run Manager 的既有
受控 redrive 路径处理，不增加 scheduler 自行重试。正常结束的既有路径保持不变。

Provider 失败不等于语义 Verify 裁决，不能凭该信号生成通过、失败验收或 task.done。
恢复工作须保留此前已发现的缺陷证据，并以正常合同完成独立验证。

Provider 终止后的 TaskAttempt 必须保留结构化 `provider_stop:*` 失败类别，并将
`recovery_owner` 交给 `run_manager`。不可重试只限制执行动作，不应使恢复责任消失；
现有 Run Manager 会展示不可自动执行的诊断/升级事项。产品语义裁决仍归 workflow。
对于旧版本误标为 `workflow_operation_terminal` 的 failed attempt，常规 reconcile
仅在原 `terminal_event_id`、operation、Run、Task 和结构化 Provider 事实匹配时修正
责任字段；保留失败状态、retryable、终局引用和既有事件，不重开 attempt、不新增裁决。

回归入口：`tests/test_provider_turn_error.py` 与
`tests/runtime/test_provider_stop_recovery.py`，包含实际 `Orchestrator.run_once()` 调用；
`tests/test_provider_attempt_recovery_owner.py` 覆盖 Run Manager 投影与旧记录幂等恢复。
