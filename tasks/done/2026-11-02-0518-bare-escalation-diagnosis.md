# 历史人工升级阻断无进展诊断

> 状态: done；实现 `4749850a fix: expose supervision evidence and unblock diagnosis`

## 证据

LS Run 中两条 `human.escalate` 不含 checkpoint/approval_command；已有 no-progress 诊断因 run 级升级而无法选择（`evt-fa64dfb451ec`、`evt-1428e1aacc65`）。`src/zf/runtime/run_continuation.py` 已允许少数旧通知迁移路径，但未覆盖该只读诊断。

## 验收

1. 对裸通知和 `no_progress_breaker` 的 `needs_diagnosis` 只开放 `diagnose-attention` -> verify: Run Manager tick 只请求一次诊断并保留人工阻塞。
2. 明确审批合同、其他 policy 和其他恢复动作保持原门禁 -> verify: 正负回归均通过，不产生任务完成或假审批。
3. 实际入口及共享恢复协议 -> verify: 影响闭包、mock E2E 和 premerge sentinel 通过；LS 账本只读重算，不修改运行态。

## 验证记录

- Run Manager、continuation、resident、Supervisor 和 mock E2E 定向闭包 387 passed；看板及 Task Pipeline 投影 20 passed。
- `dev-verify.py run --base dev --skip-tier deterministic`：serial 10、premerge sentinel 53、flow smoke 10 passed。规划器保守选择 349 文件的全量层，按 AGENTS 的影响范围规则以以上定向闭包替代；未运行 full pytest 或真实 provider E2E。
- 对 LS 历史事件只读重算：两项任务各有独立派发、heartbeat 与 `dev.blocked` 引用；不视为新业务结果或独立验收。
