# 预算修订实际应用于恢复的原操作

> 状态: active

真实 Critic 达到 operation tokens 上限后，恢复投影错误地接受 Run 级
`--token-budget` 修订作为单个 operation 的恢复授权；但两层预算必须独立。
同时现有具名 operation 修订仅支持 max_usage_samples，无法对 token 限额
进行安全、有限、可审计的原操作续跑。

1. 由真实阻塞事件、operator CLI 记录的具名 operation token 修订和 kernel redrive 绑定推导新额度
   -> verify: 仅被拦截且实际覆盖的维度作用于原 operation，其余额度与请求不变。
2. Run 级修订、错 Run、未授权、缺失修订、无效额度或不匹配 operation -> verify: 拒绝恢复或
   不改变额度，不能利用 Run 级字段修改其他 operation 的限制。
3. 恢复真实 Critic 原检查点 -> verify: 保留读取回执、原 request/session，超限
   修改可审计并能继续；超出新有限额度仍然阻塞。
4. 预算 guard、operation、恢复、EventLog 相关测试和 premerge sentinel -> verify:
   通过并记录真实与模拟范围。
