> 状态: active

# 分离返修合同读取范围与下游派发范围

监工授权：用户已要求发现问题后修复、合并 dev、恢复任务并继续监工。DL 的 SUP-08/09 返修将 Foundation/Assembly 作为直接受影响对象、93 项作为下游派发范围。`bind_scoped_snapshot` 将两种范围取并集，使 93 份完整合同进入必读正文；实际 Planner 在 2026-10-31T14:14:02Z 报告必读返修包超过单轮读取预算并准备提交失败。范围不能删减，完整合同不能丢失，预算不能自动扩大。

最小修复：仅在显式 failed_children_and_downstream 且存在直接失败任务时，正文内联直接任务合同；保持所有任务索引、完整快照摘要引用、未解析 ID 和原派发范围。修改其他任务前仍需读取对应完整合同。其他恢复模式保持既有行为。

- 复现 2 直接任务 + 93 下游派发合同 -> verify: 真实 handoff 调用的回归测试在当前 HEAD 失败。
- 分离正文读取范围 -> verify: 93 项派发范围/索引及完整合同可恢复，正文只内联两项；其余模式及未知 ID 保持可追踪。
- 交付 -> verify: impact closure、前哨门、确定性 mock 流程通过，提交、归档、合并后回归；安全边界部署并恢复同一受控返修，记录输入缩减。

验证记录：当前 HEAD 回归先失败 1 项，修复后直接调用者 34 项通过；自动 impact closure 462 passed / 7 skipped、哨兵 51 passed、mock flow 10 passed。未运行 full pytest 或真实 Provider 测试。实际返修包只读重放从 941065 bytes / 1102580 pretty JSON chars 缩到 116274 bytes / 129013 chars；93 个派发任务/索引、完整快照和反馈不变，内联 2 个直接任务。运行态部署与后续受控恢复由监督记录继续跟踪，不把源码验证视为产品通过。

实现说明：[Plan 生产失败准入](../../docs/impl/plan-producer-failure-admission.md)。
