# 冻结输入与会话恢复

> 状态: active

依据 [设计规格](../../docs/design/212-workflow-artifact-reading-and-session-resume.md)。混合媒体查询与冻结视图、凭据生命周期、原生与 CLI 的历史交付、两种恢复入口和并发恢复。

- 同一目标下运行 CLI 和 mock 恢复场景 -> verify: 精确绑定调用、来源和返回范围，浏览器 unavailable 可追溯，两次恢复合计新增执行为零。
