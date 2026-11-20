> 状态: active

# 已审核完成复用的 Reader 输入与结果准入闭环

真实8×8续测新Candidate已冻结、8路已派发，但 `candidate_currentness_issues` 仍把旧已审核子任务身份直接与新Task比对，产生48条stale_task_authority。lane3读取恢复投影要求rebind，自动transport retry仍失败。用户已授权修复问题、记录并从断点续验；仅隔离实验分支，不合入dev、不push。

- 同一不可变复用证明供冻结、输入生命周期、结果准入复核 -> verify: 真实Reader prepare/start及input recover保持current/bound，正式SemanticResultSubmitService准入mock结果。
- 当前完整合同、Task done、审核摘要、旧身份、原Verify事件、候选/Plan绑定保持机械校验 -> verify: 任一证据篡改、当前合同/状态改变、错误复用引用或旧身份均拒绝。
- 无复用的Candidate沿原路径 -> verify: 既有Candidate和Task currentness回归。
- 修复先mock与影响闭包、sentinel，再部署隔离控制面从原Run恢复 -> verify: 新运行窗口记录原生Reader继续及最终状态，不把人工修复当零介入。

前置复用修复见 [已完成任务](../done/2026-11-20-2120-plan-completion-reuse.md)。该任务只证明冻结及Reader派发；本条补齐真实续测发现的输入和结果准入消费端遗漏。

复现：真实c300528d对象48条stale_task_authority；最终有效mock在原currentness代码上2 failed/9 passed（输入及正式提交均因旧身份失败）。修复代码对同8个真实Reader只读核验全部无currentness错误，未写运行状态。初版测试夹具缺少完整Plan admission、freeze及submit credential，修正后才记正式RED；夹具启动失败不算产品复现。定向73 passed，闭包及真实续验待完成。

验证：初轮定向73 passed；补齐共享校验直接调用方后175 passed（13.67s）。dev-verify自动层167 passed/7 skipped，sentinel53 passed，flow-smoke10 passed。没有全量pytest/coverage。真实失败对象8/8只读currentness预览通过；部署后继续验证，任务保持active。
