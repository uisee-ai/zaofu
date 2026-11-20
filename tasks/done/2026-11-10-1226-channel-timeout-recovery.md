# 原生 turn 已终止后的 Channel 不确定投递恢复

> 状态: done

优先级 P2。真实 Channel 综合在测试的 600 秒预算处终止，Channel 记录
`failure_status=delivery_unknown`，因此 UI 禁止再次综合。原生 Codex transcript 已记录
`turn_aborted`，但当前没有将经核验的原生终止证据绑定到该失败 request 的操作入口。
未知状态默认禁止重复投递是正确保护；不能直接改事件或删掉保护。

本次触发首先来自测试预算配置：Kanban 的 600 秒环境变量覆盖了 Channel 默认
1800 秒。测试已改为在 zf.yaml 明确配置 Channel 1800 秒；这不等于实现失败请求恢复。

证据：`/tmp/zf-prd-ui-real-channel-20261110-115958/evidence/provider-termination.json`；
对应 `reply-958ddf9d2c8ee395` / `run-d7826bb603af0476`，Synthesizer 使用 GPT-6-Astra xhigh。

- 设计与原 request/generation/session/turn 绑定的受控终止核验 → verify: 仅确定终止的原生回执能解除不确定投递，不凭 PID 消失或文字说明授权重试。
- 保留原失败，生成新 attempt 或新的综合请求 → verify: 不重复写入原结果，不改变 PRD revision 或 Owner 签署。
- UI 呈现核验与恢复入口 → verify: 用户可从原讨论继续；仍在运行、回执不符或无回执时保持禁止重复投递。

## 本轮实施

实现与交互分析：[PRD UI 恢复](../../docs/impl/prd-ui-recovery.md)。
验证与保留失败：[验证记录](../../docs/records/2026-11-10-prd-ui-recovery-validation.md)。

实现提交：`9af9e1f3 fix: restore PRD workflow config handoff and review recovery`。

验证：定向回归 65 passed；调用方 80 passed；串行 10 passed；哨兵 53 passed；
两条 warm-runtime Docker 完整旅程均通过，Owner/Channel 交互验证通过。
广域回归 4850 passed / 9 skipped / 2 failed；两项失败在 dev 基线复现，详情见验证记录。
