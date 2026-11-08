# Work record 当前派发身份校验

关联 [199](../design/199-worker-failure-recovery-and-role-handoff.md)、[任务](../../tasks/2026-11-08-1337-work-record-placement.md)。

同一 WorkflowOperation 因尚未启动时的资源或暂停原因重新placement，原不可变request保留首次值；当前operation通过 kernel task.attempt.sent 与 workflow.operation.started 的 run/task/attempt/dispatch/lease 对账确定当前placement。work_record曾把原request的placement当成当前值：合法p7被拒，而旧request和旧dispatch恰好一致时可能漏掉current placement不匹配。

现 `_operation_workdir` 对Task Pipeline使用事件归约的当前正整数placement，未知/未被matching sent证明的0拒绝。其它固定请求字段、当前身份、TaskStore、manifest、lease、envelope digest、工作区路径与幂等仍按原规则校验。旧请求不重写；传统非Task Pipeline路径保留原合同。

公共CLI回归覆盖当前placement发布/展示/幂等重放、旧placement及sent的run/task/dispatch/lease不匹配拒绝，并确认不可变request字节保留。现场真实发布应由原Worker使用自己的提交凭据重试；监工只做无副作用定位，不能冒充worker发布或把work record当Task完成。
