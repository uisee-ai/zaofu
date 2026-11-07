# 连续后继任务继承原始输入

> 状态: active — 用户授权监工发现阻塞后修复；范围限定为有证据的连续替换输入继承。

LS-v1 的 FMS13 → POLICY → POLICY-EVIDENCE 后继链仍引用原始 task_execution_input。当前解析器只读取直接前任分区，第二层前任不在原始输入内时失败；不能通过复制完整输入、猜测任务名或跳过必读解决。实际新提案另缺显式输入选择，先保持未准入。

1. 两次及以上替换复现 -> verify: 当前 HEAD 的实际 HandoffResolver 入口测试先红。
2. 沿同 Run、摘要校验、显式单前任与继承端口一致的不可变包追溯 -> verify: implementation/task-verify 只选择原始 shared 与匹配任务分区；跨 Run、歧义、缺来源、变更输入拒绝。
3. 保留现有单跳与候选全量读取行为 -> verify: 输入解析/包/读取账本/实际派发调用闭包、sentinel 与 mock E2E。
4. 合并 dev，保留在途 worker 重载 -> verify: 实际附件恢复预检与派发证据；不把本地测试称为产品验收通过。

关联：[199 实现](../../docs/impl/199-worker-recovery-implementation.md)。


## 实现验证

- 两层/三层后继的真实HandoffResolver入口4项先红；当前4文件32 passed，含跨Run、歧义、非gap、无关祖先、输入变化及循环拒绝。初版新增测试把改变的输入同时带入当前包，实际上构成合法legacy输入，已修正fixture为当前旧输入与祖先新输入不一致；没有放宽生产验证。
- 14文件输入/包/sidecar/账本/实际handoff与199 mock闭包120 passed；dev-verify自动18文件254 passed。sentinel53 passed；额外flow smoke见本轮日志。未跑全量pytest/真实Provider。
- 解析器复用原来源检查，每层返回带摘要绑定的历史端口；原始输入ref/digest始终不变。层数保护32与现有包祖先上限一致，超过显式报错，不截断上下文。
