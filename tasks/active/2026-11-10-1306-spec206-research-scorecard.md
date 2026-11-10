> 状态: active

# 六源深挖与可解释评分

来源：用户本轮批准在当前 worktree 深挖参考、生成 backlog 并实施；design 206。基线 `2fdee307`。

冻结 Spec Kit/OpenSpec 来源并核对官方文档；在 design 206 区分原始事实、推断、采纳及不采纳项。

- 完成上述范围 -> verify: 来源逐条可定位，评分维度、权重、分数依据、证据等级和未知项完整；主/敏感性权重可复算。

不包含 merge/push 或示例产品实现。

## 实施与验证

已核对六类官方来源并固定两个源码版本；design 206 给出七维逐项依据、两套权重、适用边界和 NOT_RUN。scores.json 复算一致。

实现提交在归档时补记；完整方案见 [206](../../docs/design/206-spec-method-borrowings-and-quality-evaluation.md)。
