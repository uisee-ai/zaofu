> 状态: active

# Planner 上下文压缩与失败操作终止

Owner 已授权监工记录、修复、验证并继续。dl 在 Planner 已有 92 个切片草稿时，
上下文到 91.6% 后持续 180 秒触发 fresh-context recycle；新会话重复读取资料。
首轮 Planner 的 child 已失败，durable operation 仍为 running，错误累计后续
Planner 用量，在 60 次采样时阻断整个 Run。

- 原生压缩提前于 harness 回收 -> verify: Codex fresh/resume 均使用已有角色
  context_window_tokens × context_compact_threshold 作为自动压缩预算，不覆写模型窗口。
- 恢复耗尽的 reader child 终止匹配 operation -> verify: 实际 Orchestrator.run_once
  回归覆盖失败、终止幂等和后续同角色用量不再触发旧操作预算。
- 保持预算、安全与成功结果门禁 -> verify: budget guard、operation、reader fanout、
  provider adapter/transport 影响闭包、sentinel 与 deterministic flow smoke。

事故源 Run：prepare-TASK-C9A6DC-a79c16705d4f57a7；首次 operation：
wop-prd-plan-planner-trig-evt-7cf6aa0f-839d15ea6a02。修复后通过受控恢复路径继续，
不清除成本记录、不伪造成功结果、不扩大产品外部事实。

实现说明：[Reader 恢复](../docs/impl/planner-context-and-operation-recovery.md)。
复现：原 HEAD 上 operation 终态断言为 running；两项原生压缩启动参数回归失败。

- 配置已注册 reader 的受控重触发 -> verify: 经 ControlledActionService.execute
  保留源产物、task、Run、因果身份；同源重复、未注册事件和新增 writer trigger 拒绝。
  原 HEAD 对已注册 reader trigger 的测试失败，恢复入口采用配置驱动而非追加 PRD 特例。

验证：reader/预算/operation/provider/lifecycle 15 文件 365 passed；受控恢复与
resume 5 文件 38 passed；dev-verify 自动 sentinel 51 passed、flow smoke 10 passed；
ruff 与 diff check 通过。planner 给出宽范围默认闭包，本次以以上直接影响闭包代替
全体 deterministic/serial 扫描；未执行全量 pytest，真实 Provider 的压缩待 dl 续跑观察。
收据：`/tmp/zf-planner-recovery-verification-20261031.json`。
