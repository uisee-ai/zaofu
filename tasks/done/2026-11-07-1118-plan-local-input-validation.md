# 计划预检尊重任务专属输入的消费范围

> 状态: done — `94c3fca7 fix: validate local plan input partitions only for declared consumers`

LS-v1 Q04候选给五项任务增加q04_development_input，实际Handoff支持任务专属required_plan_ports；Plan预检却对每一个产出的分区输入要求全部37项Task都有分区，报FMS01缺分区。这是预检与实际消费协议不一致。其余51条旧矩阵/映射/来源/继承输入问题独立处理，不因本修复放行整个候选。

1. 同一有效任务专属分区输入在Handoff与预检行为不一致 -> verify: 新预检回归先红，实际消费者回归保持。
2. 只对声明消费该端口的Task检查分区存在；全局必需端口保持全量任务要求 -> verify: 缺失已声明分区、非法结构、重复分区、缺绑定均拒绝。
3. 未被任何Task消费的分区内容也须结构完整 -> verify: 结构校验不能通过空消费集绕过。
4. 实际pre-submit入口、source/package/读取闭包 -> verify: dev-verify plan/run、sentinel、mock E2E；记录LS候选错误变化，不声称产品恢复完成。

关联：[199实现](../../docs/impl/199-worker-recovery-implementation.md)。


## 验证记录

新正例初始失败（测试fixture同时有重复scope标题，修正为两个独立任务）；实现后发现既有execution_spec缺分区负例需要保留，故只对显式局部归属端口缩小检查，未声明局部归属的历史端口不放宽。6文件120 passed；dev-verify自动36文件755 passed；sentinel53、flow smoke10通过。真实当前Q04候选只读回放52→51，唯一消除q04_development_input对FMS01分区的错误，全部其他诊断保留。未运行全量pytest/真实Provider。合并前完成实际pre-submit调用测试；整体计划尚未准入。
