> 状态: done — d8ef5b08 fix: harden operator access and PRD evidence recovery

# 184 · P2：Sidecar 摘要与消费字节一致

基线：`8e06db7f`，基于当前 dev 在独立 worktree 复验。

## 授权与范围

用户已授权“先生成 backlog，再实施”，本记录先候选后迁入 tasks/active。

允许修改：src/zf/runtime/sidecar_refs.py；tests/test_sidecar_refs.py；直接消费者测试，以及相关 `docs/impl/` 说明。不修改实际运行状态。

实施说明：[边界修复](../../docs/impl/review-184-boundary-hardening.md)。

## 发现证据

hydrate 先校验文件再重读正文，原子替换可导致返回正文不属于所声明摘要。

## 验收

1. 读取中原子替换 -> verify: 仅返回同一读取快照或摘要错误，不能返回旧 digest + 新正文
2. 大小、路径与旧描述符兼容 -> verify: 聚焦测试通过，实际读取有界

## 验证与收尾

先复现当前 HEAD，再实施；执行 dev-verify plan/run、影响闭包测试和适用 sentinel/mock E2E。实现 commit 后记录 hash，再迁入 tasks/done。仅本地提交，不 push/merge。

## 基线复现

新增缺陷测试在 `8e06db7f` 得到 16 failed / 4 passed；四项均复现，随后才实施。

## 验证记录

共用命令与结果见 [2026-10-22-0620-184-prd-evaluation-recovery.md](./2026-10-22-0620-184-prd-evaluation-recovery.md#验证记录)。各项新增回归、直接调用者及共享边界均已通过。

## 完成结果

实现 commit：`d8ef5b08 fix: harden operator access and PRD evidence recovery`。四项任务均已通过验收；验证命令与结果集中记录于 PRD 评估恢复任务。
