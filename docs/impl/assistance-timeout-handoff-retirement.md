# 咨询超时通知的正式交接收尾

对应[199 的失败恢复与角色交接](../design/199-worker-failure-recovery-and-role-handoff.md)。

ls-v1 的 FMS13 曾在咨询超时后收到 `human.escalate`。OA 随后回答、原 worker 提交已准入的 blocked 结果、operation settled、内核分诊选择 `request_replan`，但旧通知仍阻止 Run Manager 执行该恢复。`worker.assistance.closed` 会停止咨询轮询；原人工门禁投影不能识别恢复职责已经移交。

`assistance_handoff_retirement.assistance_handoff_retired` 为既有 Run Manager 的过期事件判定增加有限证明，供恢复选择和完成情况投影共同使用：

1. 通知必须来自内核 Run Manager，精确关联咨询 request，具有原生超时/owner不可用原因和既有恢复条件；显式人工决策、审批命令和 checkpoint 不适用。
2. 同一 request 必须有内核 `worker.assistance.closed(status=waiting)`，Run、Task、operation、attempt、lease 全部相符。
3. 原 worker 的失败必须绑定相同身份，并关联已准入的 CallResult。内核 operation settlement 必须绑定同一 request hash 和完整 envelope ref/digest。
4. 后续同一失败的内核分诊必须为非终止的 `request_replan`。只有这条完整链才能让旧咨询通知退出阻塞。

该逻辑只证明恢复路由已经接手，不决定产品合同、不自动回答 Owner 问题、不授予新 writer、不将产品标记通过。迟到回答或其他任务进展不能代替失败准入和正式交接。事件历史、原咨询、失败和通知均保留，不伪造人类确认，也不改变已关闭咨询的回答时限。后续计划准入、WRC、依赖、暂停和其他 Owner 门禁继续按原流程执行。

真实旧事件可以直接重放得到同一投影；缺少完整证据的历史通知仍需精确的操作员审核。本次现场先使用已授权的 `human-decision-dismiss` 解除单条旧通知，再用回放证明通用修复覆盖该事故，不能将这次手动解锁描述为新代码的自然自动恢复。

回归覆盖实际 `build_run_manager_projection` 和 continuation 选择入口，并保留同一 Task 的另一条 Owner 门禁；反例覆盖来源、Run/Task/operation/attempt/lease、摘要、准入和分诊缺失、终止分诊、迟到回答和真实审批。

Resident 请求人工处置时应给出具体的 `question`、作用范围及证据。RM 制作决策卡和读取已绑定建议时，优先保留顶层问题及已有的 `recommended_replan_or_resume_action.decision_required`，再读取 `recommended_run_manager_action.question`。具体问题中的选项随原文转送，缺少问题时才显示摘要或原因。独立诊断报告的采纳或归档可以标为 `blocking_scope=side`，前提是 resident 确认产品推进不依赖该决定。影响范围未明时沿用 run 门禁。仅供人工审阅的建议通过已授权的 `human-decision-dismiss` 关闭，历史门禁保留原作用范围，运行面板可从其绑定建议还原待决定的具体问题。采纳报告不会补发缺失证据或授予重试权限。

### Candidate 根任务的人工等待与孤儿超时

孤儿扫描只保护当前 run 下未分配 worker 的根任务。内核核对冻结 Candidate、实际启动的 Verify fanout、失败聚合、当前 Run Manager 诊断和仍有效的有作用域 Owner 门禁，resident 必须已启用。取得失败检查点与恢复负责人的绑定即可证明有人负责等待，Reader 业务结论仍按原流程审核。

每次扫描复用同一账本索引、有效门禁和待诊断列表。持续等待期间根任务保留在进行中；观察到门禁释放时重置该任务的孤儿计时，若之后仍无进展，完整阈值耗尽才重新回到 backlog。已发出的历史告警和已回队的任务仍走已有处置流程。冷启动沿用现有机制，内存调度时钟为空，后续派发或阶段进展重新建立计时；这项保护不为重启前已无主且没有任何新事件的任务重建历史计时。
