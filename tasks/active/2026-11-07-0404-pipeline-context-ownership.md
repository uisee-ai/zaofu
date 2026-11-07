# Task Pipeline 上下文回收归属修复

> 状态: active

## 问题与边界

2026-11-07 03:57 UTC，Retention/Media 的 kernel `worker.context.critical`
触发完成审计，尚在工作的 Impl 被当作缺完成证据而产生 `task.retry_scheduled`。
随后旧重排路径将 operation 挂为 suspended、attempt superseded、Task 退回 backlog；
Pipeline 将 suspended 视为在途，生命周期回收则已失去当前 Task，导致两路停摆。

本批保留 provider lifecycle 对上下文阈值、hold、fresh recycle 和恢复 briefing 的所有权。
Task Pipeline 的完成事实仍只能由既有 typed result admission 判定；不改变重试上限、
scope、验收标准、运行态或 Run Manager 恢复策略。

## 验收

- 精确识别原 critical 时点的 running Impl/Verify operation -> verify: 同 task/run/dispatch/role 且 kernel 来源；不接受未知、跨 run、非 context 或其他 stage。
- 抑制已证明由上述 critical 派生的旧 retry/continuation -> verify: 后续 attempt 替换或结束后重复消费仍不改 Task/operation/attempt。
- 保留生命周期硬回收 -> verify: 冷 runtime 首次 95% 样本保持在途；180 秒后真实 recycle 发送恢复 briefing，operation/dispatch/attempt/lease 不变。
- 保留 reader 与 legacy 路径 -> verify: reader ownership 与未知来源/timeout 回归通过。
- 完成结构和闭包检查 -> verify: focused、premerge sentinel、deterministic context-recycle mock E2E；不运行 full pytest。

## 证据

实现说明：[Task Pipeline 断点修复](../../docs/impl/task-recovery-20261105.md)。
RED：新增 ownership 回归 3 failed / 11 passed。
第一版 focused 73 passed；sentinel 首次因 reactor 冻结行数新增14行失败，改为复用现有两处 guard 接口、薄聚合 reader/pipeline owner，未放宽结构基线。
最终 focused + lifecycle/mock caller 闭包 73 passed；sentinel 50 passed / 1 skipped；flow mock E2E 10 passed。
`dev-verify plan` 因 reactor 触发 205 文件的自动 broad selector；遵循仓库 full-suite 限制，run 明确跳过 deterministic/serial 自动全套，用上述六文件 focused 闭包替代，保留 sentinel/flow-smoke，未声称 full pytest。
只读实况 replay：本次两 critical 与两 scheduled 共4个事件均匹配 kernel ownership，未调用 reactor 或写事件。
证据目录 `/tmp/zf-supervision-20261107/pipeline-context-*`；待主会话审核与提交后登记实施 hash。
本片不含主 checkout 尚未提交的 typed recovery briefing 接线，主会话另做组合验证。
