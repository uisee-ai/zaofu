> 状态: active

# Reader 压缩后从工作进度恢复，避免重复全量重建

真实8×8窗口5已受控停止：19/19宿主命令通过、7路结果已准入，最后lane1在两次原生compacted后读完18项，又从source0重读；没有WRC。恢复投影historical_missing=0仍固定next_action=reconstruct_required_inputs，给出全量required命令。用户授权修复后从原断点继续；限定隔离分支，不合dev、不push。

- 恢复报告区分必读缺口与Agent语义缺口 -> verify: 同消费者必读证据齐全时不默认全量重读；缺读/换角色/输入失效仍读或受控rebind，current_memory保持unknown，不授予执行或通过。
- 阶段性WRC方法 -> verify: 更新既有Verifier方法，在材料读完、检查组完成后保存摘要和原始ref；恢复先读WRC，按需查原文，不用摘要替代首次必读或独立验收。
- 合法同operation断点延续能读原WRC -> verify: 真实CLI publish→受控stop/resume→周期派发→CLI show，原记录不变，新lease仍需有效；无准入、异身份、证据损坏拒绝。
- 回归后仅恢复最后1路 -> verify: 保留7个settled结果、16任务完成与19宿主回执；记录原生Reader继续、后续汇总和独立产品结果，不宣称无人总资格通过。

复现：补修前恢复指引及原WRC经过受控传输重派后不可读，两项有效RED。实现后定向90 passed；初次自动闭包1186 passed/1 skipped，1项前驱WRC恢复JSON大小检查失败，已压缩重复说明并通过包含该用例的90项复验，最终闭包进行中。临时测试最初与已有同名文件冲突，已恢复原测试全文，新用例独立为test_reader_context_progress_checkpoint.py，未删除既有覆盖。

最终验证：定向90 passed；dev-verify --base 23a0ba67 --workers 4自动闭包1187 passed/1 skipped（含恢复、重计划、过期合同handoff mock E2E）；sentinel53 passed。真实账本只读回放：18项必读齐全、historical_missing=0、建议assess_context_gaps_then_continue且无默认全量required命令；仍保持current_memory=unknown。待部署后真实签收方法与接续效果。
