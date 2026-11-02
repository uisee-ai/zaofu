# Kanban 看板说明任务状态与执行状态

> 状态: done；实现 `4749850a fix: expose supervision evidence and unblock diagnosis`

## 证据

LS 的两个 Task 为 in_progress，但对应 pipeline 实际 blocked；`zf kanban --board` 按生命周期分列，不输出现有 dispatch summary 的执行状态，容易将 blocked 列 0 解释为没有执行阻塞。

## 验收

1. 看板说明各列为任务生命周期并给出执行状态查询入口 -> verify: 正常渲染包含准确命令，空看板和机器可读协议不变。
2. Task Pipeline summary 仍为执行状态来源 -> verify: 现有任务投影、CLI 渲染和状态测试通过，不更改任务状态或伪造实时值。

## 验证记录

- Run Manager、continuation、resident、Supervisor 和 mock E2E 定向闭包 387 passed；看板及 Task Pipeline 投影 20 passed。
- `dev-verify.py run --base dev --skip-tier deterministic`：serial 10、premerge sentinel 53、flow smoke 10 passed。规划器保守选择 349 文件的全量层，按 AGENTS 的影响范围规则以以上定向闭包替代；未运行 full pytest 或真实 provider E2E。
- 对 LS 历史事件只读重算：两项任务各有独立派发、heartbeat 与 `dev.blocked` 引用；不视为新业务结果或独立验收。
