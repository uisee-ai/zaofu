> 状态: active

# Critic 样本预算的受控修订与原调用恢复

Owner 要求继续监工并恢复 dl。当前 Critic 的 fanout_synth 因 usage_samples=60
阻塞，既有 goal set 修订仅接受 Task Pipeline，无法沿 CLI 恢复。
本批补齐 fanout_synth 的精确预算修订与 Runtime 重派，取消当前 Critic 的样本上限，
保留费用上限、不可变请求、完整必读合同和旧 Verify policy refusal。
原大型输入去重/分片候选不随本批冒充完成。

- 操作级预算修订 -> verify: CLI 接受具名 synth 的有效修订，范围/旧身份/过期修订检查不变。
- Runtime 重派 -> verify: 原 request hash、attempt 和预算累计保留，实际发送一次并回到 running。
- 反例 -> verify: 无修订、错误操作、输入 hash 不符、终态/过时 fanout 和非预算拒绝均不得恢复。
- 部署 -> verify: 精确 CLI 修订及恢复事件、实际 provider 工作与后续审查结果；不以 requested 代替执行中。

验证：预算/Operation/恢复/CLI 闭包 61 项，Reader fanout/结果准入闭包 210 项；
最终调用链回归 161 项（与前述部分重叠），新增恢复反例 8 项；51 项合并哨兵
和 10 项确定性 flow-smoke 通过。dev-verify 因共享 Operation 扩张为 217 个文件，
按 AGENTS.md 执行上述直接调用闭包，没有运行全量 pytest 或实 Provider E2E。
部署版本 `df9a5508`，dl 配置 `eabc1c8`；当前正在执行具名预算修订和恢复验收。

首次线上恢复暴露载体残留：预算 Run 停止取消了 aggregate，随后 kernel 生成无结果退出失败。
补齐事件式重开、旧失败重放边界，以及尚未派发的 budget redrive 停机恢复。
新增闭包 252 项通过（含 flow-smoke），最终调整后的重点 40 项通过；哨兵 51 项通过。
dev-verify 选择器扩张为 231 个测试文件，依照 AGENTS.md 使用精确调用链闭包，未跑全量。
