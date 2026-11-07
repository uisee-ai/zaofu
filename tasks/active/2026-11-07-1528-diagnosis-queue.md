# R44：独立任务诊断不能被占用的深诊断队列饿死

> 状态: active

LS-v1 壳层15:19失败，Run Manager15:22 tick发现明确failure-diagnosis任务，但队首历史no-progress诊断等待单飞Autoresearch，tick输出continue_waiting且0动作；可用的resident诊断者未被使用。与此前车辆成功路由相比，差别是过期深诊断占槽。仅给可派发、任务定界、无人工门禁的独立诊断选择机会，不关闭后台调查或跳过语义恢复。

## 验收

- 重现真实投递入口饥饿 -> verify: no-progress队首+Autoresearch占槽+空闲resident+当前失败，实际run_manager_tick应派发绑定任务诊断。
- 保留互斥与权限 -> verify: resident忙/不可用、人工门禁不被绕过；不能放行任意replan或重试。
- 接通并验证实际消费 -> verify: focused recovery/continuation/gating/diagnosis闭包、sentinel、mock E2E；合并部署后真实壳层诊断投递事件。

验证：R44真实tick 1项RED、R45正式dismiss投影1项RED；负例初始发现无Run anchor时别名解析为空，已改为显式correlation回退并保留同Run要求。13文件164 passed，flow smoke10 passed，sentinel53 passed。dev-verify planner350文件触发全仓阈值，依AGENTS测试域规则跑上述明确闭包，不声称全仓或新真实Provider E2E。真实事件重放no_progress=clear，next_operation=failure-diagnosis:evt-4304f600c253，未投递诊断或改事件。
