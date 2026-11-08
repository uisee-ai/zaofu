# 输入恢复投影的继续工作语义

来源：[恢复投影指导修复](../../tasks/2026-11-08-1517-recovery-projection-guidance.md)，关联[199](../design/199-worker-failure-recovery-and-role-handoff.md)。入口为 `zf artifact recover` 与恢复briefing，方法在角色skills中。

FMS19真实失败evt-44716768ee8e把历史已读完整后的unknown/false/范围总数当作无法继续的证据。源码和现有回归确认这些值是保守投影：系统无法证明模型当前记忆，报告也不能授予权限。重复读取不能改变它们，缺少维护checkpoint也不是一项独立执行门禁。

修复只补响应说明与Impl、Verify、Critic及交付方法：先核对当前派发身份、workdir、lease、输入状态和真实暂停；已绑定时补齐必读，并从受控来源重建当前遗忘条款，再按角色执行下一项检查。rebind_required仍要求受控重绑定，unavailable仍须检查缺失绑定；不得把unknown解释为已读/已理解、不得改成authorizes_execution=true，也不得绕过读账本或结果准入。

保留原字段/计数、生命周期与鉴权逻辑，未另建状态机或认知证明。现场解释可解除一类错误等待，不能保证长输入在任何上下文窗口都可完整保留；若确有具体来源不可恢复，应准确上报而非伪造记忆。

验证：针对恢复CLI/精确读取21项通过；dev-verify按18个映射文件运行142项通过、1项跳过，包含stale-contract模拟E2E；sentinel53项通过。只改方法与响应解释，身份/鉴权/生命周期和保守字段值未改。尚待现场部署与恢复效果观察。
