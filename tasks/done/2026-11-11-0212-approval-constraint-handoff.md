# 保留原始审批约束到 Requirement

> 状态: done — `2362995c` fix: preserve planning constraints and improve Plan review handoff；后续修正见下文。

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

## 完成证据与边界

- 实现：`2362995c` fix: preserve planning constraints and improve Plan review handoff；`f872d575` fix: align Channel conditions and preserve legacy Task identities。
- canonical constraints 支持 create/update/落库/Requirement；Channel flat 入口同步支持。空条件不改变历史身份，非空条件参与身份绑定；来源直引不匹配仍拒绝，未硬编码所有项目都需要审批。
- 聚焦影响闭包378 passed / 1 skipped；兼容性与 Channel 入口追加212 passed；两次 sentinel 各53 passed；非全量 pytest。
- 真实 Kanban 保留8条条件，首次 YAML 为 true，无人工配置修补；PRD保存终点曾经一次UI协助，不能报全链路无人干预。
- Channel 首轮2362995c 的 flat 入口仍丢字段并生成false，原失败保留；f872d575 全新运行的6条条件完整进入两版 Requirement，首份 YAML 为true，成功启动真实扫描/规划。
- 后续 Channel Planner 因10项来源AC/每Task上限8的解释而abstain，未到最终Preview；这不否定条件传递修复，但完整双入口不算全绿。该语义判断尚未独立复审。

详见忽略的 reports/spec206-optimization-20261111/evaluation.md、channel-fixed-handoff-proof.json、channel-fixed/verdict.json。未批准产品实施，未merge/push。
