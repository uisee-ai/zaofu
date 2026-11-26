# 计划准入事故恢复后的 Reader 收尾

对应 [199](../design/199-worker-failure-recovery-and-role-handoff.md) 的恢复责任闭环。

ls-v1 监工发现：FMS03 的新任务已真实派发，因先前 PlanPackage 准入错误排队的
Discovery 仍启动。原收尾检查只证明业务失败事件到替换任务的因果链；准入事故的
`rework_of` 指向原 `task_map.ready`，没有同一形状的业务替换链，因此没有退出。

`plan_admission_reader_closeout.recovered_plan_admission` 从既有事件证明相同事故
已经恢复：原 kernel ready 的 ref/digest、相同 incident 与 trigger 的 kernel
`task_map.admitted`、精确引用该 admission 的同 Run WRC generation，以及该
generation/ref/digest 对应的有效 PlanPackage。只有旧取消 trigger 的包时仍拒绝；
后续由新 trigger 正式重新准入的同包可以通过。不改变包的取消隔离规则。

该证明接入共享 `reader_failure_supersession`，实际 fanout 生命周期沿用既有
受控取消路径：首次派发前可停止排队 Reader，运行中的旧 Reader 关闭 operation
和对应 transport，重启重放保持幂等。取消原因是
`superseded_by_recovered_plan_admission`。不写产品通过、不回收其他业务 worker，
所有原失败、取消、重准入事件保留。

验证入口：`tests/test_plan_admission_reader_closeout.py` 覆盖实际 runtime tick、
首次派发前取消、重启，以及缺 WRC、仅有取消包、异事故、异 Run、异 trigger、
摘要/包不符和不可信事件的反例；原业务替换检查继续由
`tests/test_reader_replacement_closeout.py` 与 `tests/test_reader_failure_supersession.py`
覆盖。这项修复不代表所有 `task_map.amend.failed` 已具备自动补齐语义输入的能力。


Planner 简报提供已启用的宿主验证命令原文。执行审阅在 `candidate_commands_outside_host_registry` 中列出与宿主登记不匹配的 Candidate 命令及其任务 ID。Critic 核对执行渠道和验收时点。阻塞式 Task Pipeline 按任务就绪派发独立 Verify，已配置的 Candidate Verify 在候选冻结后启动。

## 独立 Critic 批准后的 Writer 准入失败

Writer 拒绝已核准的 Task Map 时，失败事件携带原 kernel `task_map.ready` 的 Run 和阶段重试代次。对已持久化的旧失败，恢复从其直接引用的 ready 追溯已生效的 Owner 授权。若该代从已签收的 Planner 断点启动，还须核对独立 Critic 已准入的批准结果、该结果的输入合同及原候选所有引用。返工接收此次 Writer 的失败原因、相同候选的文件与摘要。

当 Planner 原 Scan 和 ready 都未声明 PDD，Writer 补写的展示值 `default` 不构成输入变更。复用 Scan 仍须证明原调查员组、结果收据、接收角色和输入绑定。达到旧上限且 Run 已封口时，Owner 可使用 `stage-replan-new-generation`，指定原升级事件、批准引用与 `operator_feedback`。这次请求启动新代，反馈进入 Planner 的签名返工输入。新候选仍须经过 Critic 与 Writer 准入。[恢复与反例测试](../../tests/test_unissued_plan_critic_recovery.py) [Scan 复用测试](../../tests/test_prd_stage_teams.py)


同一 Run 中，Kernel 为某个具体阶段失败发出 `stage_replan_cap_exhausted` 后，该发生点的自动额度已耗尽。Watcher 启动和空闲补偿都读取此事实。即使新版本能够从旧 Writer 收据还原更准确的代次，也须通过指向该升级的受控动作开始下一代。来自当前代的新失败仍使用自身额度。

旧版本若已发布直接以已升级失败为原因的 Planner 重试，Kernel 会拦住其派发和迟到聚合。若它已经替换了合法 Planner，先用 `zf runs pause <run>` 暂停该 Run，再从经过身份校验的 Web 动作重投被取代的原请求。使用 `POST /api/actions/stage-replan-new-generation`，提供 `workflow_run_id`、`escalation_event_id`、`approval_ref` 和 `redrive_owner_trigger_event_id`。后三项分别取自原 Owner 的升级事件、批准引用和已获准的触发事件。可选的 `reason` 记入操作审计。

控制面核对最近一次有效代次、原始 Web 授权、原 Planner 的撤销凭据以及旧分支的来源，随后从账本恢复原来的候选、Scan 和意见。错代 Reader 的产物仍可审计。正确分支已交付或 Run 已进入后续业务决策时，请求返回 409。重复请求返回同一触发事件。收到成功回执后用 `zf runs resume <run>` 继续派发。[HTTP、并发与中断重试验证](../../tests/test_paused_owner_plan_redrive.py)
