> 状态: active

# Reader正常读取续跑保留执行尝试

来源：真实K3 Candidate第二轮verify-lane-4，evt-b671f5ea59ce、evt-ad0c06a50392触发续读却supersede TaskAttempt；evt-f640708e8a5f在3/3预算处失败。现有mock仅比较WorkflowOperation active_attempt_id，未覆盖TaskAttempt enforce。用户授权监工发现、修复并先mock再真实验证。

- 真实调度入口的enforce回归 -> verify: 超过3次新的输入进展仍只使用原sent TaskAttempt/lease/dispatch；可正式提交结果。
- 保留机械约束 -> verify: 暂停、过期、过时dispatch/operation、预算、未知发送不得越过或重复投递；重复页不产生无限续跑。
- 受控发送和准确回执 -> verify: 原请求/来源/权限不变，未送达不得标delivered，正常续读不消耗故障重试。
- 影响闭包、sentinel、mock -> verify: 记录准确命令、结果与未覆盖的真实范围，合入后才准备新轮源码。

验证：当前dev有效RED为1失败（期待5次发送，实际3次）；修复后聚焦26通过；dev-verify影响闭包3158通过/7跳过，sentinel53、flow-smoke10通过。真实源码切换与无人验收尚待独立运行，未宣称通过。
