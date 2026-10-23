> 状态: done — d8ef5b08 fix: harden operator access and PRD evidence recovery

# 184 · P1：Operator WebSocket 来源校验

基线：`8e06db7f`，基于当前 dev 在独立 worktree 复验。

## 授权与范围

用户已授权“先生成 backlog，再实施”，本记录先候选后迁入 tasks/active。

允许修改：src/zf/web/server.py；src/zf/web/terminal_routes.py；tests/test_operator_websocket_security.py，以及相关 `docs/impl/` 说明。不修改实际运行状态。

实施说明：[边界修复](../../docs/impl/review-184-boundary-hardening.md)。

## 发现证据

受信本地模式下旧 Operator WebSocket 无 Origin 校验，新 Terminal 已有同源门。

## 验收

1. 外部 Origin 与缺失 Origin 连接 -> verify: 两组 Operator 入口均返回 1008，终端管理器未被访问
2. 同源与显式授权 -> verify: 保留 token/passcode/trusted 模式及正常 IO/control 行为

## 验证与收尾

先复现当前 HEAD，再实施；执行 dev-verify plan/run、影响闭包测试和适用 sentinel/mock E2E。实现 commit 后记录 hash，再迁入 tasks/done。仅本地提交，不 push/merge。

## 基线复现

新增缺陷测试在 `8e06db7f` 得到 16 failed / 4 passed；四项均复现，随后才实施。

## 验证记录

共用命令与结果见 [2026-10-22-0620-184-prd-evaluation-recovery.md](./2026-10-22-0620-184-prd-evaluation-recovery.md#验证记录)。各项新增回归、直接调用者及共享边界均已通过。

## 完成结果

实现 commit：`d8ef5b08 fix: harden operator access and PRD evidence recovery`。四项任务均已通过验收；验证命令与结果集中记录于 PRD 评估恢复任务。
