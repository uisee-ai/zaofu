# Plan 工件字节边界与监工返修反馈

Plan producer 的结果预检与 Critic 派发前检查共用 `plan_candidate_ports`。
引用型 Plan port 按原文件 UTF-8 字节读取，内联对象按 `canonical_json_bytes`
计算落盘大小，均遵守 `sidecar_refs.DEFAULT_HYDRATE_LIMIT_BYTES`（当前 4 MiB）。
引用文件先检查大小，再有界读取，防止检查后增长导致无界读取。
超限返回 `plan_port_too_large`，包含逻辑端口名、实际大小及上限；预检不写状态。
没有提高消费者上限，也不将过大的文件截断后当作完整输入。

此检查在 Planner 调用 `result validate` 时即可反馈，并在实际 Critic 派发路径再次检查。
完整来源与验收义务仍由 Planner/Critic 管理。遇到过大的补充规范，应消除重复表述，
使用已有显式端口与按任务输入合同；不能通过删减需求或改写已接纳证据消除超限。

`stage-replan-new-generation` 的原升级事件、Run 身份、授权引用及幂等约束保持不变。
该受控动作可额外接受 `operator_feedback: list[str]`。条目必须是非空字符串；
坏输入在恢复 Run 之前拒绝。新代际保留原失败诊断，再附加 operator 提供的文本与
请求事件 ID，通过已有 `rework_feedback` 进入 Planner 的不可变 `plan-rework-context`。
运行时不解释业务语义、不替 operator 批准 Plan。相同代际与反馈重复调用幂等；
同一代际试图更换反馈时拒绝，避免复用已批准的恢复身份暗改输入。

验证覆盖：`tests/test_plan_port_size_preflight.py` 的引用/内联 UTF-8 边界、
producer validate、Package hydrate 及实际 Critic 调用；
`tests/test_reader_replan_feedback.py` 的受控动作到 Planner 输入、无效输入与重放。
