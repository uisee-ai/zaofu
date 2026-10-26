> 状态: done — d8ef5b08 fix: harden operator access and PRD evidence recovery

# 184 · P2：PRD 请求绑定质量契约

基线：`8e06db7f`，基于当前 dev 在独立 worktree 复验。

## 授权与范围

用户已授权“先生成 backlog，再实施”，本记录先候选后迁入 tasks/active。

允许修改：src/zf/runtime/channel_prd_quality.py；synthesis 请求/回复/评估链路；相关 tests/，以及相关 `docs/impl/` 说明。不修改实际运行状态。

实施说明：[边界修复](../../docs/impl/review-184-boundary-hardening.md)。

## 发现证据

Agent 输出省略 quality_contract 与读取状态字段时，新质量门静默退出。

## 验收

1. 新请求固定质量契约版本 -> verify: 缺失/错误标记及无效正文被拒绝，合法回复通过
2. repair/replay 与评估 -> verify: 版本要求随持久请求传递且评估保持一致
3. 历史兼容 -> verify: 无新版本绑定的历史产物可读，不追溯伪造通过结论

## 验证与收尾

先复现当前 HEAD，再实施；执行 dev-verify plan/run、影响闭包测试和适用 sentinel/mock E2E。实现 commit 后记录 hash，再迁入 tasks/done。仅本地提交，不 push/merge。

## 基线复现

新增缺陷测试在 `8e06db7f` 得到 16 failed / 4 passed；四项均复现，随后才实施。

## 验证记录

共用命令与结果见 [2026-10-22-0620-184-prd-evaluation-recovery.md](./2026-10-22-0620-184-prd-evaluation-recovery.md#验证记录)。各项新增回归、直接调用者及共享边界均已通过。

## 完成结果

实现 commit：`d8ef5b08 fix: harden operator access and PRD evidence recovery`。四项任务均已通过验收；验证命令与结果集中记录于 PRD 评估恢复任务。
