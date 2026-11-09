# 预算修订实际应用于恢复的原操作

> 状态: done · 实施: `47802c8f` fix: scope token budget recovery to named operations；`3f72529e` fix: resume budget amended reader operations；`ce0a80d6` fix: renew scheduler delivery on synth budget replay

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

## Planner reader 补充

干净真实 Run 的 Planner 在约 156.8 万累计 token 后阻塞，原有具名预算
恢复仅覆盖 Task Pipeline 和 synth，无法恢复普通 `fanout_reader_child`。
扩展该受控路径，保留 Run、fanout、role、request/attempt 身份，验证授权
后、传输前再次停机及身份不符时拒绝派发。真实 Run `workflow-ab0566812a516ab8`
通过具名有限额度恢复原 Planner，扫描未重跑；后续继续验证结果与 Critic。

## 调度派发身份补充

真实 Critic 从预算阻塞恢复后出现 `task.attempt.shadow_mismatch`，原因是重用
已失败的旧 dispatch id；shadow 继续执行，enforce 会拒绝。修复后按获准的
redrive 事件生成新 scheduler dispatch id，保留 operation 与读取 attempt；
测试 shadow/enforce 均能产生新的已发送 TaskAttempt。真实结果 `evt-668ad13c35fd`
在该补充修复之前已准入；后续使用模拟工作流、真实 enforce 调度及一次真实
Codex 应答复验新派发通过，未重跑完整真实 Critic。


## 验证记录

- 真实隔离 Run 中 Planner 与 Critic 各经具名有限 token 修订继续原操作，
  Planner 与 Critic 结果均准入、两次都保留原 session，scan 未重跑；Critic
  结果的机械必读账本 `satisfied`。真实 Critic 提交时仍使用旧调度身份。
- 补充修复后，enforce 调度实际创建新的 scheduler attempt 并调用一次真实
  Codex；该测试的工作流载体为 mock，Provider 仅作简单应答。shadow/enforce
  调度单测、相关闭包 `374 passed`、sentinel `53 passed`、mock flow
  `10 passed`；与真实全链路适用范围分开报告。
- 全量 deterministic 未完成，原 dev 基线存在依赖 Claude 登录的同一失败。
