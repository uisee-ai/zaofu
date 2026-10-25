# 通用模块与任务关联

当输入包含产品能力、业务模块或需要独立解释的共享技术边界时，在现有
`task-map.v1` 中输出 `modules`。模块划分由 Planner 根据确认需求、设计和仓库
事实决定；不要按 Agent、Provider、Workflow 阶段或目录名称机械分组。

```json
{
  "modules": [
    {
      "module_id": "MOD-ACCOUNT",
      "title": "账户与权限",
      "parent_module_id": "",
      "goal_claim_ids": ["REQ-ACCOUNT"],
      "task_ids": ["TASK-SHARED", "TASK-ACCOUNT"],
      "source_refs": ["docs/prd.md#account"],
      "design_refs": ["docs/design.md#account"]
    }
  ]
}
```

- `module_id` 在计划修订间保持稳定；`parent_module_id` 可省略，层级不得成环。
- `task_ids` 引用本版 Task Map 的任务；共享任务可以被多个模块引用，但只定义一次。
- `goal_claim_ids` 引用需求条款，`source_refs`/`design_refs` 绑定实际来源。不能捏造文档。
- 首次规划和重新规划都先读取已绑定的需求 / Goal Claim Set，复用其中的稳定条款 ID 与完整条款；不能用自己命名的同义目标替换原始需求分母。新增细分条款应保留与原始条款的显式关系。
- 检查模块 / Task 对原始条款的覆盖；没有任务承接的条款仍保留并明确缺口。
- 已有 Task 的 `phase`、`plan_section` 和 `blocked_by` 保留原语义；模块不改变派发。
- 简单任务无合理模块拆分时可以不声明模块，图将明确显示未提供，不能伪装完整覆盖。
- PRD、设计与计划的可读正文应通过已有 Plan Artifact Ports 提供，便于完整预览、
  引用讨论和受控修订；运行中的已接纳文档不能原地覆盖。

生成图所需的是明确关系与可验证产物，不要求 Agent 输出 Mermaid、坐标或前端组件。
图布局、状态投影和实时刷新由通用代码完成。
