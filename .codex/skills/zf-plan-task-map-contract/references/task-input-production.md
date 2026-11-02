# 任务输入切片生产与反例审查

这是既有 Task / Plan Package 合同的生产方法，不新增 Task schema，也不授权修改已批准版本。

1. 对账完整原始材料，将条款、例外、共享规则、依赖接口、图片的页面/状态/位置绑定来源版本和定位。来源清单与覆盖账本不能只列摘要；排除要有理由，未知不得标成覆盖。
2. 按 Task Map 的精确 ID 生成一次 owned partition；跨任务约束放 shared，说明消费者义务。保留验收 ID、source_ref、image_ref，以及接口错误语义。仅复制 ID 不算语义完整。
3. 保留完整原始产物与矩阵。Impl / Task Verify 的 selection 只引用批准包内实际存在的端口；Candidate Verify、任务集成评审和 Goal Judge 保持各自 profile，不补造通用 judge selection。
4. 提交前制作端口名到相对 JSON 文件的映射，运行 `zf plan inspect-inputs --bundle bundle.json --task T1`。按 next_offset 继续；缺失资料、未知引用、重复/多余切片和依赖错误均需解释或修复。预检不批准 Plan，也不修改原文件。
5. Critic 独立从原始材料反向对账 shared + owned；逐条说明异常、共享规则、图片状态、依赖和验收责任是否保留。引用存在、结构合法或预检无问题都不能证明语义完整。

输入片段示例（正文自由字段属于输入材料，不能替代 canonical Task contract）：

```json
{
  "schema_version": "task-scoped-plan-input.v1",
  "shared": {
    "protocol": "库存接口 v1；所有修改记录操作者和原值",
    "source_refs": ["source.md#shared"]
  },
  "tasks": [{
    "task_id": "T1",
    "body": {
      "acceptance_ids": ["AC-1"],
      "source_refs": ["source.md#retry", "api.md#version"],
      "image_refs": ["stock.png#empty"],
      "behavior": "授权用户提交出库",
      "exception": "重复请求返回首次结果，不得再次扣减",
      "dependency": "消费 T0 的 v1 接口；版本冲突返回 409",
      "image_state": "空库存显示补货入口，禁止显示出库按钮"
    }
  }]
}
```

独立离线评测：

- `PYTHONPATH=src python scripts/eval-context-inputs.py prepare --output /tmp/zf-context-eval-<UTC>/`
  生成 100 Task 片段、生产者 bundle、五组匿名材料 blind.json 和分离的 grader.json。
- Reviewer 只接收 blind.json，不能看 grader。四组反例保留 AC/来源 ID，分别漏掉异常、共享规则、图片状态、依赖正文；另有一组完整对照。
- 返回绑定 evaluation_digest 的 cases/findings，包含 task_id、category、source_ref、精确 source_quote 与 explanation。
- `... score --grader <grader.json> --findings <findings.json>` 只评分显式 findings 与证据绑定，报告漏报/误报；不通过关键词猜测语义，不是模型资格或 Plan 准入。真实评审还需记录 provider/model/session、独立性、原始输出和人工抽查。
- `... benchmark` 仅测宿主快照/导航构建与分页准备时间和字节，不调用 Provider，不生成 worker 正文读取回执，不证明 token 节约。

`... retrieval` 测量 Impl/Task Verify/Candidate 的显式 Task、AC、来源、图片查询及 ACL、固定版本恢复；
报告完整导航达到上限造成的漏检，和自然语言正文查询的能力边界。它不调用 Provider。
