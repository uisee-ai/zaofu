# Work record 当前派发身份校验

关联 [199](../design/199-worker-failure-recovery-and-role-handoff.md)、[任务](../../tasks/2026-11-08-1337-work-record-placement.md)。

同一 WorkflowOperation 因尚未启动时的资源或暂停原因重新placement，原不可变request保留首次值；当前operation通过 kernel task.attempt.sent 与 workflow.operation.started 的 run/task/attempt/dispatch/lease 对账确定当前placement。work_record曾把原request的placement当成当前值：合法p7被拒，而旧request和旧dispatch恰好一致时可能漏掉current placement不匹配。

现 `_operation_workdir` 对Task Pipeline使用事件归约的当前正整数placement，未知/未被matching sent证明的0拒绝。其它固定请求字段、当前身份、TaskStore、manifest、lease、envelope digest、工作区路径与幂等仍按原规则校验。旧请求不重写；传统非Task Pipeline路径保留原合同。

公共CLI回归覆盖当前placement发布/展示/幂等重放、旧placement及sent的run/task/dispatch/lease不匹配拒绝，并确认不可变request字节保留。现场真实发布应由原Worker使用自己的提交凭据重试；监工只做无副作用定位，不能冒充worker发布或把work record当Task完成。

## 无预算长任务的记录时机

关联[续接方法修复](../../tasks/2026-11-08-1900-budget-independent-continuation.md)。
无预算时 `warning_level=0` 是正常投影，不能把预算50%预警当作首次保存续接信息的前提。
Task Pipeline简报和Impl/Verify角色方法改为按完成的检查组、阶段切换、新未决项增量记录；
预算50%/80%仅额外提醒。Impl沿用合同内笔记或已有work record，Verify只填当前授权scratch
的已有对应字段及证据引用，不新增产品文件、scratch字段、状态表或准入门。
该方法不证明当前记忆、不替代required reads，也不自动发布work record或暂停任务；
减轻反复回查的实际效果仍须由真实worker后续记录与恢复行为核验。
