> 状态: done — 42808a9a fix: reconcile recovery carriers with current fanout lineage

# 恢复案卷的代际与承接关系

Owner 已授权监工发现问题后修复并继续。Run Manager 在 evt-bd6ba2cd05a1 报告父任务 TASK-C9A6DC 的 wip_without_carrier 误报；旧 Planner 的 malformed_report 终局之后，同版 Critic、授权 Plan 审批和 Task Pipeline 准入已完成，DL-FOUNDATION 正常执行。维护请求 evt-c6bce71731bf 为非阻塞 side escalation，不能据此重排或打断实施。

- 复现旧投影 -> verify: 真实账本只读对比及新回归显示父任务误报、兄弟任务在飞误报、过期代际覆盖当前派发。
- 复用 fanout identity 和当前 Task Pipeline generation -> verify: 仅同 Run、来源 fanout、父任务且完整的当前代际接管旧 child；输出承接事件和代际证据。
- 保留真正孤儿 -> verify: 其他任务忙碌、过期/不完整/无关准入、已关闭 fanout、只有 Plan 审批均不能隐藏当前孤儿。
- 验证实际 tick 调用与投影写出 -> verify: 承接事实可见，TaskStore 内容不变；通过调用闭包、相关代际合同、mock E2E 与 sentinel 后提交合并。

实现说明：[恢复案卷承接证据](../../docs/impl/recovery-case-carrier-lineage.md)。维护不修改 dl 业务代码、工件和 canonical 状态，也不触发任务重排。

验证：新回归原版 4 failed / 10 passed；补充同 fanout 不同任务的反例，并经真实 tick 写出验证。dev-verify 自动闭包 66 passed；fanout identity、Task Pipeline 代际/恢复、mock E2E 和 flow smoke 共 89 passed；sentinel 51 passed，Ruff/diff check 通过。真实 dl 12,004 条事件、94 个任务只读复现保存于 /tmp/zf-recovery-carrier-live-repro-20261031.json。未运行 full pytest。

本轮为监工第 10 批修复；核对当前 backlogs 与近期 git log：没有 proposed 条目，仅一条已 defer 的 Generic Workflow 工件路径问题，未满足复核触发条件。dl runtime 的在飞实施持续运行，源修复合并后待安全边界加载，不能把只读复现当作常驻进程已更新。
