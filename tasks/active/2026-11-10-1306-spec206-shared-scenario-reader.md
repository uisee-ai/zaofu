> 状态: active

# 共享场景与决定原始状态阅读

来源：用户本轮批准在当前 worktree 深挖参考、生成 backlog 并实施；design 206。基线 `2fdee307`。

复现真实 R2 的跨任务场景归属提示；展示逐任务 AC/命令注册及未绑定项，保留原文和 NOT_RUN；决定显示原始状态统计及关闭时点。

- 完成上述范围 -> verify: 聚焦 unit + Docker 浏览器证明合法共享不伪报未知、无归属仍显式提示、同名 AC 不串任务、未知状态不算解决、只读不写。

不包含 merge/push 或示例产品实现。

## 实施与验证

旧模型已复现真实 S-add 误报；修复后 unit 通过。Docker 1280/375 场景责任、原始决定状态/时点和只读检查通过；未知/重复/串任务保留提示。

实现提交在归档时补记；完整方案见 [206](../../docs/design/206-spec-method-borrowings-and-quality-evaluation.md)。
