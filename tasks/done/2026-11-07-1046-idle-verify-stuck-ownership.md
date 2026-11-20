> 状态: done — a3816e1c fix: prevent idle verify workers from triggering safe halt

# 按活动工作归属约束 on-demand 心跳卡死恢复

Media Verify 06:20 已通过，prd-verify-lane-1 后续空 current_task_id 的 busy 心跳仍被当作活动工作，06:47/06:55 重启熔断触发全局 safe_halt，阻断已通过的 Retention integration。原检查空 task_id 返回 true，完成检查在无活动 Task 时返回 false。

- 以 typed operation/TaskAttempt 和 legacy Task/fanout 判定 on-demand 工作归属 -> verify: 已终止/跨角色不触发 silent/stuck，活动 reader 父任务 backlog 仍检测。
- 保留 resident/eager 角色、未知读取的原检测 -> verify: focused 正反回归。
- 真实 sweep 与 direct stuck 入口闭包 -> verify: idle 不再 respawn/global safe_halt，真正活动工作仍产生 stuck。
- 补实现说明、focused/sentinel/mock -> verify: 本文件记载精确结果；主会话统一提交，不修改生产运行态。

实现说明：[idle-verify-stuck-ownership](../../docs/impl/idle-verify-stuck-ownership.md)。

## 验证记录

- RED：11 failed / 6 passed；最后新增 26 个回归场景。
- Focused caller/契约/恢复闭包 15 文件：203 passed / 36.81s；精确路径见实现说明。
- Sentinel：50 passed / 1 skipped；相关 deterministic lifecycle/context E2E 包含于 focused，自动 flow smoke 10 passed。
- dev-verify --base dev 选 784 文件，--base HEAD 选 175 文件，均升级 broad；以本片 focused 替代自动 deterministic/serial，其余 contract/mock_e2e 已执行。
- 真 operation 事件有界回放：6 条原始事件；重建空 task busy mirror 后旧 guard=true、新 guard=false，无新 stuck/pause。并非完整 provider transcript 回放。
- 主会话已完成审阅和本地实现提交；主 checkout 加载与生产观察由1043恢复任务继续跟踪，本片测试不冒充真实 provider E2E。
