> 状态: done

实现提交：`813f15c3` — fix: validate work records against current recovery placement；合并 `95db6981`。

# 恢复派发后的阶段工作记录校验

用户授权监工发现问题后修复。datal P16B 当前 operation placement=3，immutable request placement=1；`_operation_workdir` 只读实测误拒 `work record dispatch identity mismatch`。不修改原 request、attempt、manifest 或账本。

- 当前派发可发布并回读阶段记录 -> verify: 真实 CLI 回归覆盖 immutable request=1 / 当前已验证 placement=3，发布与回读同一 digest。
- 旧派发与未经核验的 placement 继续拒绝 -> verify: old epoch、缺失/伪造 sent、错误 lease、错误 envelope 均不发布记录。
- 部署后原 worker 重试 -> verify: current CLI 发布真实非终态记录，原 task/attempt 不变；记录不替代实现或验收。

复现证据：datal `diagnostics/work-record-placement-20261109/before.json`。

验证：初始回归 2 failed/3 passed；修复后定向40通过、dev-verify影响域66通过、sentinel53通过、动态续跑mock E2E4通过。真实P16B只读绑定从拒绝变为正确工作目录。

现场闭环：合并后58项定向及53项sentinel通过；2026-11-09 10:57 UTC 原worker发布 `evt-ca95172175de`，保留 attempt/dispatch/manifest，记录sidecar摘要核验通过。当前CLI加载修复，无需重启worker或控制面。记录是未独立验证的工作报告，不代表产品完成。实现worktree已按合并、干净、无活动使用检查清理。
