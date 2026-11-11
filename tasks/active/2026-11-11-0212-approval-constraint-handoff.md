# 保留原始审批约束到 Requirement

> 状态: active

来源：206 续批两个真实入口首轮均丢失“Plan 单独批准后才能实施”。Kanban 恢复前 plan_approval=false；Channel evt-759ee2564aa4 的 source_quotes 无法绑定。详见 reports/spec206-delivery-20261111/evaluation.md。

## 范围与边界

先对比聊天/PRD、Task、Requirement 逐跳字段，定位丢失发生在哪一跳。语义提取归 prompt/Skill；确定性代码只验证绑定和状态。不能关闭审批来源校验、硬编码所有项目 plan_approval=true 或以加强测试输入替代生产修复。同时消除约束 dict 字符串与普通文本重复。

## 验收

- 原始约束传递 -> verify: 两入口从同一原输入生成的 Requirement 保留审批时序、source_ref/quote 可核验；无需人工补字段
- 反例 -> verify: 原输入未要求批准时不虚构 Owner 约束；source quote 不匹配仍拒绝
- 新真实验证 -> verify: 独立冻结版本，两入口首次准备成功；失败历史保留

## 2026-11-11 新真实复现

当前 bd5ba25d 的重复测试进一步定位：Agent 提案 evt-3c7fcf82202e 明确写 8 条 contract.constraints，TaskContract 未声明该字段，task_contract_from_mapping 忽略未知字段；create-task 自动 prepare_workflow 的转交列表也不包含 constraints。Requirement 从 exclusions / explicit_non_goals / unknowns 重建，只保留 5 条非目标，随后 YAML plan_approval=false。见 reports/spec206-preview-repeat-20261111/kanban-constraint-loss.json（原始提案、落库字段、Requirement、纯函数复现）。

不能仅修提示词，也不能把 unsupported constraints 当作已经支持的 Task 字段。需明确生成入口的真实合同、未知字段处理和受控传递到 Requirement 的合法载体，保持一个 canonical Task contract。测试提示词本身也应与受支持的合同一致。

## 本批实现与验证进度

已实现字段传递、阅读投影、生成/评审方法及分阶段测试取证。聚焦闭包 378 passed / 1 skipped，
哨兵 53 passed，前端构建/单元通过，Docker 阅读 25 passed，两条 fake UI 旅程通过。
首次 pytest 因共享依赖目录导入 dev 被保护性拒绝，已用 PYTHONPATH=src:. 重跑；
首次浏览器 fixture 因 UID/JSON 导入设置失败，已修正并独立复验。真实验证待本批冻结后执行。

写入策略为显式目标的原生工具；任意 Python shell 的保守拒绝仍存在，不宣称语义解析误判已根除。
