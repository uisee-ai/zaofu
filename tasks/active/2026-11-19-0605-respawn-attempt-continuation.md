> 状态: active

# Provider重启后接续当前TaskAttempt

授权来源：Owner要求真实无人验收暴露问题后mock复现、修复、再验证。
新轮`workflow-0b7e95eee0d318d9`的dev-lane-0、dev-lane-6、verify-lane-6
在pane退出后自动spawn成功，但均出现`failed to inject recovery briefing`。
隔离实际恢复入口复现：`TaskAttempt … delivery is already sent`。恢复入口漏用已有
同attempt continuation，误走首次派发claim。初始pane为何退出另行诊断，不与此混淆。

- 恢复已发送的当前pipeline attempt -> verify: 实际恢复入口送出原typed briefing，
  不新增/替换attempt，不产生产品通过，原lease/dispatch保持。
- 暂停、过期、已结算、dispatch错误、预算限制 -> verify: 不发送。
- 未发送的prepared/delivering不进入sent continuation -> verify: 旧派发回归保持。
- 调用闭包、sentinel、mock E2E -> verify: 先mock通过；当前真实Run在补丁加载前
  保留原源码及无人计数；任何批准后维护另计人工，不回填零介入资格。

RED：有效fixture下1失败、5拒绝反例通过；初次fixture API错误不算业务RED。

验证：直接入口及continuation聚焦32 passed；dev-verify自动影响闭包3075 passed / 7 skipped，sentinel53 passed，flow-smoke10 passed。真实故障来自K4的3个worker；K4容量验收已失败并正常停止，保留候选代码，不追认为无人通过。
