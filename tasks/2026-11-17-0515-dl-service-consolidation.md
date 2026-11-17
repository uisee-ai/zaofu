# DL 服务版本收敛与读取断点恢复

> 状态: done — `58eff080` fix: preserve plan and reader checkpoints across transport recovery；补充 `65f4a97d`、`bff63b04`。

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
- 真实续读暴露长期停机后的 attempt 凭证过期；恢复投递必须验证不变输入并续发当前 attempt 的原范围授权。

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

## 完成证据（2026-11-17 UTC）

- 原修复与来源提交已纳入 dev：`8f8097c5`。上线检查另发现无结果的 Synth 超时与长期停机后读取凭证过期，分别以 `60af3c7c`、`33eee420` 合并修复。
- 最后恢复/安全影响闭包 900 passed；后续纯调用整理的直接回归 50 passed；合并前后 sentinel 53 passed。此前 Web 类型检查、单测、production build 和确定性 mock E2E 均通过，未声明全仓全量测试。
- DL 原 Run `prepare-TASK-C9A6DC-a79c16705d4f57a7` 与原 Synth operation/attempt 保持不变；request hash 仍为 `a35d4a65f36b025b80b6d1083ecd9f18ac4cf05d35d22c8c72a0cc2514a45712`，费用快照未重置。
- 用正式 restart、run-resume 与精确 workflow-batch-resume 恢复，未直接修改 canonical state。真实 Codex Provider 已接收多页 `required-artifact-page.v2`，页面内容 sidecar 哈希与 Provider 工具回执互相绑定。
- 原读取账本保留。跨版本旧 bundle 游标不兼容，`--resume` 已切换新游标并保守重读；本轮验证的是同请求恢复可执行，不声称零重复读取，也不代表 Plan 审查或产品任务完成。
- Runtime、8001 Dashboard、Autoresearch 和 DL 角色使用当前主 checkout；四个旧 services worktree 的 HEAD 均可达 dev，清洁检查与进程/环境/文件句柄引用检查通过后正常删除，未 force-remove。
- 切换前状态备份 550,223,971 bytes，tar 比较通过且 SHA-256 已记录。92 份实现验证文件完成清单和哈希校验归档。

持久交接入口：[服务收敛报告](../reports/worktree-archives/20261117-dl-service-consolidation/README.md)。
真实恢复证据：[live-final-proof.json](../reports/worktree-archives/20261117-dl-service-consolidation/live-final-proof.json)。
Dashboard 浏览器能显示任务，但冷启动时 snapshot pending / board-cards 202 和部分旧项目配置错误仍有观察记录；本次未将服务重启宣称为 Dashboard 性能问题已解决。
