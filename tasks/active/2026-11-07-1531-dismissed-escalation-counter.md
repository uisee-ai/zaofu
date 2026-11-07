# R45：已正式 dismiss 的升级不再计入无进展熔断

> 状态: active

旧Grants升级evt-5d113b7537ae已通过ControlledActionService关闭，但no_progress仍把它计入同指纹第三次失败并置tripped。只从当前投影扣除精确已dismiss事件，保留历史账本与同指纹其他失败；不全局清空计数或随意重置预算。

- 受控dismiss精确生效 -> verify: 3次同类升级，正式dismiss一次后不再达到3次阈值；新失败到来再次tripped。
- 保持来源和Run隔离 -> verify: 请求未应用、外部伪造ack、非dismiss状态、错误Run/来源不能清理；重放不会重复减计数。
- 实际RM入口 -> verify: projection不再报告该旧breaker，R44允许其他真实任务继续诊断；focused router/recovery测试和sentinel。

验证：R44真实tick 1项RED、R45正式dismiss投影1项RED；负例初始发现无Run anchor时别名解析为空，已改为显式correlation回退并保留同Run要求。13文件164 passed，flow smoke10 passed，sentinel53 passed。dev-verify planner350文件触发全仓阈值，依AGENTS测试域规则跑上述明确闭包，不声称全仓或新真实Provider E2E。真实事件重放no_progress=clear，next_operation=failure-diagnosis:evt-4304f600c253，未投递诊断或改事件。
