# R36：跨代际 gap 恢复尊重任务显式输入选择

> 状态: active

真实evt-5ff191701ea9：附件旧任务选择task_execution_input，原hash及requirement_spec未变；Q04更新全局execution_spec后，gap baseline仍无条件比较该未消费端口，阻止原任务增量恢复。

- 复现 -> verify: 未选择execution_spec的旧任务，在该输出更新后仍可基于当前全图恢复，保留无关任务。
- 最小修复 -> verify: 保留全局requirement_spec、Run/合同/祖先/Task正文与共享决策校验；execution_spec只在任务显式选择时纳入比较；已选择task_execution_input或execution_spec变化仍拒绝。
- 影响闭包与部署 -> verify: baseline/bridge/依赖延续/Plan包测试、sentinel、mock E2E；真实旧包只读before/after证明。
- 恢复 -> verify: 修正附件提案责任/依赖后经受控动作重新准入，不修改冻结结果；其他在途任务保留。不push。

已验证：RED 复现未选 execution_spec 误拒绝；GREEN 13 项直接回归；10 文件影响闭包 202 passed；sentinel 53 passed；mock E2E 8 passed。dev-verify plan/run 记录 /tmp/zf-r36-dev-verify.json，因 182 文件阈值自动选择全量，按仓库域测试规则跳过 broad/serial 改跑以上具体闭包。真实 before/after 见 LS docs/records/2026-11-07-r36-input-selection-proof.json。仍需部署后恢复回执再 done。
