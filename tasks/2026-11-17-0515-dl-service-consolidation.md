# DL 服务版本收敛与读取断点恢复

> 状态: active

用户批准将旧服务快照的有效修复集成至当前 dev，统一 DL Runtime、Dashboard、
Autoresearch 与角色会话，验证原 Run 恢复后退役四个旧 services worktree。

## 实现范围

- 基线 `cfee5f06`；来源 `f8269d79`、`c553a633`、`917b2631`。
- 保留被 Critic 拒绝的当前候选、精确输入绑定和受控恢复证据；兼容已有 Owner 问题路径。
- Dashboard 对明确声明暂不可用的投影 GET 有界重试，不缓存占位结果，并保留取消语义。
- Reader/Synth 仅根据同一请求与 attempt 的新增必读覆盖继续传输恢复；无进展仍受限。
- Reader 精确检查点恢复保留请求、读取账本和费用绑定，拒绝语义失败、policy 拒绝和身份漂移。
- 适配现有 `reader_dispatch_recovery` 的角色占用、预算修正和暂缓派发逻辑。
- 部署预检发现旧 Run 的直接阻塞为 Synth 超时；补充显式审批的精确超时检查点恢复，保留读取账本并验证停机与中断重放。

## 验收

1. 在当前基线复现缺口 -> verify: Plan 5 个失败、Reader 3 个失败与前端 503 失败均留存证据。
2. 适配恢复实现 -> verify: 变更模块、直接调用者、准入/重启/预算/事件合同和确定性 mock E2E 通过。
3. 前端构建 -> verify: queryClient 回归、Web 类型检查和 production build 通过。
4. 合入 dev -> verify: 合并前后 sentinel 通过，其他会话的改动不进入本次提交。
5. 服务切换 -> verify: 同一 Run/输入检查点连续，启动及新派发可观测，旧版本角色与 resident 退出。
6. 快照退役 -> verify: 证据与来源提交可恢复、无活跃路径引用，四个目录及 worktree 注册消失。

## 实现依据

- [Plan producer 失败准入](../docs/impl/plan-producer-failure-admission.md)
- [投影 GET 重试](../docs/impl/projection-get-retry.md)
- [Reader 传输失败的读取断点恢复](../docs/impl/reader-transport-checkpoint-recovery.md)

本轮证据先保存于 `reports/dl-service-consolidation/`，收尾时按仓库规则归档并提供持久链接。
