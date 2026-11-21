# 旧Plan preflight遥测按需读取

> 状态: active

隔离8×8授权修复。原生Planner在previous-plan-candidate-2的execution_review.usage_observations上强制分页超过160KB；已有plan_execution_delivery仅覆盖新execution review，旧preflight作为完整candidate再被送入必读链。当前Planner已读完，不修改其冻结输入；修复供后续合法派发。

- 新operation的已知preflight中usage groups用已有摘要/明细规则输送 -> verify: 原preflight/业务字段/错误/命令/unknown字段完整保留，源ref/digest可追溯，明细仍受控可读，必读体积下降。
- 重放保持精确输入 -> verify: 已存在legacy operation保留原始必读；新投影operation重放hash一致；原candidate或语义字段变化仍拒绝divergence。
- 边界不扩张 -> verify: 部分授权、未知schema、非usage字段不自动缩减；错digest/明细篡改被拒绝；无worker read凭据补写。
- 真正入口接通 -> verify: prepare_call_operation经真实manifest/required-read路径覆盖Planner和Critic，聚焦/影响闭包/哨兵/流程验证。

实现验证：82聚焦通过，最终源码影响闭包4911 passed/7 skipped、53哨兵、10流程mock通过。真实旧preflight副本132139→11099字节（必读减少91.6%），105组usage明细与摘要可完整还原原对象，原件/运行态/冻结manifest未改。首次RED确认旧preflight没有交付投影；独立审核发现派生access_scope被空值覆盖，已用真实读取授权检查复现RED并修复，原件、摘要及明细均继承限制。多历史来源与当前review无source_id冲突，producer伪造runtime view被拒绝。

部署及下一次原生输入的现场验证尚待完成，因此保持active。证据位于隔离worktree reports/eight-qualification-20261120T122929Z/previous-preflight-{focused.log,final-closure.log,flow.log,retained-proof.json}。宽闭包后新增多来源及真实required_reads断言经聚焦测试通过；未以无状态投影替代原有审计证据。
