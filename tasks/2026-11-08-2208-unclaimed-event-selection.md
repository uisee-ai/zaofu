# 无人认领巡检按事件类型读取

> 状态: active；依据用户“实时监工，遇到问题修复并记录”的授权，修复监工中复现的控制面重复复制开销。

LS-v1当前HEAD 2cfbe131的真实无人认领巡检，热缓存三次耗时8.74秒；其中read_days/_copy_events约8.52秒，处理约4.9万条当天事件的完整正文，却没有待发告警。独立只读测量使用真实TaskStore/EventLog和不落账的event sink，证据位于LS记录2026-11-08-unclaimed-sweep-profile/。这是局部函数证据，不宣称已定位或解决整个Watcher高CPU。

只让该调用者使用现有EventLog事件类型选择；类型集合与纯函数的认领/去重消费规则共用。保留同日窗口、SLA、payload task_id、所有认领事件、工作流任务排除、告警去重和异常处理，不改运行态、不新增调度规则。

- 保护巡检不复制无关大正文 -> verify: 实际LifecycleManagerMixin调用冷/热/append路径，加入大agent.text；原行为RED，修复后仅未认领普通任务告警。
- 保留四种认领事件与已有告警抑制 -> verify: task.dispatched、fanout.child.dispatched、dev.build.done、task.done.evidence和task.unclaimed.warning均通过实际调用证明，不误报已完成/工作流任务。
- 保留共享EventLog完整性与调用闭包 -> verify: shepherd、lifecycle、EventLog缓存/签名及Orchestrator直接调用测试，premerge sentinel及相关mock E2E。
- 复放同份冻结真实事件 -> verify: 新旧告警payload完全一致，记录同样三次热读耗时，不以性能数字替代行为等价。
- 合入、受控加载并观察 -> verify: 原worker operation/attempt/dispatch/workdir/pane保持；记录局部改善与整体未证实边界。

验证记录：新增用例在修复前1 RED（无关正文被复制），修复后首轮34通过；调用/共享合同闭包130通过；dev-verify自动合同哨兵53通过、mock E2E 10通过。planner因共享LifecycleMixin反向映射194测试文件扩到整套；依据AGENTS采用已明确的两模块/实际调用/EventLog闭包，跳过自动deterministic和serial全量层，不宣称full pytest或真实provider验证。

冻结49092条真实事件，新选择148条完整相关正文及顺序精确一致，告警payload序列相同（当前均无告警）；同样三次未加profiler热读2.18493秒降至0.075568秒。临时副本已清理。待合并、受控加载与工作进度保留检查后归档done。
