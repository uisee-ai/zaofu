# 冻结输入与会话恢复

> 状态: done · `022ad4e6 feat: deliver scoped workflow inputs and milestone recovery`

原 8×8 的两条 Candidate Verify lane 分别用三种方式重放，六组均覆盖 40/40 项。两次恢复新增宿主执行为 0，保留 18 项可用证据和 1 项浏览器缺口。Linux 真实挂载另验凭据撤销和跨进程 FD 拒绝。详见[实施记录](../../docs/impl/workflow-inputs-and-milestone-recovery.md)。

依据 [设计规格](../../docs/design/212-workflow-artifact-reading-and-session-resume.md)。混合媒体查询与冻结视图、凭据生命周期、原生与 CLI 的历史交付、两种恢复入口和并发恢复。

- 同一目标下运行 CLI 和 mock 恢复场景 -> verify: 精确绑定调用、来源和返回范围，浏览器 unavailable 可追溯，两次恢复合计新增执行为零。
